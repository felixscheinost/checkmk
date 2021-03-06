#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2019 tribe29 GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

from typing import Optional, List, NamedTuple, Dict
from dataclasses import dataclass, asdict
from livestatus import SiteId, LivestatusResponse

from cmk.utils.type_defs import HostName

from cmk.gui import config
import cmk.gui.sites as sites
import cmk.gui.visuals as visuals
from cmk.gui.globals import html, request
from cmk.gui.utils.urls import makeuri_contextless
from cmk.gui.i18n import _
from cmk.gui.plugins.dashboard import dashlet_registry, ABCFigureDashlet
from cmk.gui.plugins.dashboard.utils import render_title_with_macros_string
from cmk.gui.pages import page_registry, AjaxPage


@dataclass
class Part:
    title: str
    css_class: str
    count: int


@dataclass
class ABCElement:
    title: str
    tooltip: str

    def serialize(self):
        raise NotImplementedError()


@dataclass
class SiteElement(ABCElement):
    """Renders a regularly available site"""
    url_add_vars: Dict[str, str]
    total: Part
    parts: List[Part]

    def serialize(self):
        serialized = asdict(self)
        serialized["type"] = "element"
        serialized["total"] = asdict(self.total)
        serialized["parts"] = [asdict(p) for p in self.parts]
        return serialized


@dataclass
class HostElement(ABCElement):
    """Renders a regularly available site"""
    link: str
    host_css_class: str
    service_css_class: str
    has_host_problem: bool
    num_services: int
    num_problems: int

    def serialize(self):
        serialized = asdict(self)
        serialized["type"] = "host_element"
        return serialized


@dataclass
class IconElement(ABCElement):
    """A hexagon containing an icon representing e.g. disabled or down sites"""
    css_class: str

    def serialize(self):
        serialized = asdict(self)
        serialized["type"] = "icon_element"
        return serialized


class SiteStats(NamedTuple):
    hosts_in_downtime: int
    hosts_down_or_have_critical: int
    hosts_unreachable_or_have_unknown: int
    hosts_up_and_have_warning: int
    hosts_up_without_problem: int


class HostStats(NamedTuple):
    scheduled_downtime_depth: int
    state: int
    num_services: int
    num_services_crit: int
    num_services_unknown: int
    num_services_warn: int


class SiteOverviewDashletDataGenerator:
    @classmethod
    def generate_response_data(cls, properties, context, settings):
        if config.is_single_local_site():
            site_id: Optional[SiteId] = config.omd_site()
        else:
            site_filter = context.get("site", {}).get("site")
            site_id = SiteId(site_filter) if site_filter else None

        render_mode = "hosts" if site_id else "sites"

        if render_mode == "hosts":
            assert site_id is not None
            elements = cls._collect_hosts_data(site_id, context)
            default_title = _("Host overview")
        elif render_mode == "sites":
            elements = cls._collect_sites_data()
            default_title = _("Site overview")
        else:
            raise NotImplementedError()

        return {
            # TODO: This should all be done inside the dashlet class once it is instantiated by the
            #  ajax call
            "title": render_title_with_macros_string(
                context,
                settings["single_infos"],
                settings.get("title", default_title),
                default_title,
            ),
            "title_url": settings.get("title_url"),
            "render_mode": render_mode,
            "plot_definitions": [],
            "data": [e.serialize() for e in elements],
        }

    @classmethod
    def _collect_hosts_data(cls, site_id: SiteId, context) -> List[ABCElement]:
        elements: List[ABCElement] = []

        for host_name, host_stats in sorted(cls._get_host_stats(site_id, context).items(),
                                            key=lambda h: h[0]):
            elements.append(
                HostElement(
                    title=host_name,
                    link=makeuri_contextless(
                        request,
                        [
                            ("host", host_name),
                            ("site", str(site_id)),
                            ("view_name", "host"),
                        ],
                        filename="view.py",
                    ),
                    host_css_class=cls._host_css_class(host_stats),
                    service_css_class=cls._service_css_class(host_stats),
                    has_host_problem=(host_stats.state != 0 or
                                      host_stats.scheduled_downtime_depth > 0),
                    num_services=host_stats.num_services,
                    num_problems=(host_stats.num_services_crit + host_stats.num_services_unknown +
                                  host_stats.num_services_warn),
                    tooltip="",  # host tooltips are fetched on hover through an ajax call
                ))

        return elements

    @classmethod
    def _host_css_class(cls, host_stats: HostStats) -> str:
        css_class = {
            2: "unreachable",
            1: "down",
            0: "up",
        }[host_stats.state]
        if host_stats.scheduled_downtime_depth > 0:
            css_class = "downtime"
        return css_class

    @classmethod
    def _service_css_class(cls, host_stats: HostStats) -> str:
        if host_stats.num_services_crit > 0:
            return "critical"
        if host_stats.num_services_unknown > 0:
            return "unknown"
        if host_stats.num_services_warn > 0:
            return "warning"
        return "ok"

    @classmethod
    def _get_host_stats(cls, site_id: SiteId, context) -> Dict[HostName, HostStats]:
        filter_headers, _only_sites = visuals.get_filter_headers(table="hosts",
                                                                 infos=["host"],
                                                                 context=context)
        try:
            sites.live().set_only_sites([site_id])
            rows: LivestatusResponse = sites.live().query(cls._host_stats_query() + "\n" +
                                                          filter_headers)
        finally:
            sites.live().set_only_sites(None)

        return {row[0]: HostStats(*row[1:]) for row in rows}

    @classmethod
    def _host_stats_query(cls) -> str:
        return "\n".join([
            "GET hosts",
            "Columns: name",
            "Columns: scheduled_downtime_depth",
            "Columns: state",
            "Columns: num_services",
            "Columns: num_services_crit",
            "Columns: num_services_unknown",
            "Columns: num_services_warn",
        ])

    @classmethod
    def _collect_sites_data(cls) -> List[ABCElement]:
        sites.update_site_states_from_dead_sites()

        site_states = sites.states()
        site_state_titles = sites.site_state_titles()
        site_stats = cls._get_site_stats()

        elements: List[ABCElement] = []
        for site_id, _sitealias in config.sorted_sites():
            site_spec = config.site(site_id)
            site_status = site_states.get(site_id, sites.SiteStatus({}))
            state: Optional[str] = site_status.get("state")

            if state is None:
                state = "missing"

            if state != "online":
                elements.append(
                    IconElement(
                        title=site_spec["alias"],
                        css_class="site_%s" % state,
                        tooltip=site_state_titles[state],
                    ))
                continue

            stats = site_stats[site_id]
            parts = []
            total = 0
            for title, css_class, count in [
                (_("hosts are down or have critical services"), "critical",
                 stats.hosts_down_or_have_critical),
                (_("hosts are unreachable or have unknown services"), "unknown",
                 stats.hosts_unreachable_or_have_unknown),
                (_("hosts are up but have services in warning state"), "warning",
                 stats.hosts_up_and_have_warning),
                (_("hosts are in scheduled downtime"), "downtime", stats.hosts_in_downtime),
                (_("hosts are up and have no service problems"), "ok",
                 stats.hosts_up_without_problem),
            ]:
                parts.append(Part(title=title, css_class=css_class, count=count))
                total += count

            total_part = Part(title=_("Total number of hosts"), css_class="", count=total)

            elements.append(
                SiteElement(
                    title=site_spec["alias"],
                    url_add_vars={
                        "name": "site",
                        "site": site_id,
                    },
                    parts=parts,
                    total=total_part,
                    tooltip=cls._render_tooltip(site_spec["alias"], parts, total_part),
                ))

        #return elements + cls._test_elements()
        return elements

    @classmethod
    def _get_site_stats(cls) -> Dict[SiteId, SiteStats]:
        try:
            sites.live().set_prepend_site(True)
            rows: LivestatusResponse = sites.live().query(cls._site_stats_query())
        finally:
            sites.live().set_prepend_site(False)

        return {row[0]: SiteStats(*row[1:]) for row in rows}

    @classmethod
    def _site_stats_query(cls) -> str:
        return "\n".join([
            "GET hosts",

            # downtime
            "Stats: scheduled_downtime_depth > 0",

            # Down/Crit
            "Stats: state = 1",
            "Stats: worst_service_state = 2",
            "StatsOr: 2",
            "Stats: scheduled_downtime_depth = 0",
            "StatsAnd: 2",

            # unreachable/unknown
            "Stats: state = 2",
            "Stats: worst_service_state != 2",
            "StatsAnd: 2",
            "Stats: state = 0",
            "Stats: worst_service_state = 3",
            "StatsAnd: 2",
            "StatsOr:2",
            "Stats: scheduled_downtime_depth = 0",
            "StatsAnd: 2",

            # Warn
            "Stats: state = 0",
            "Stats: worst_service_state = 1",
            "Stats: scheduled_downtime_depth = 0",
            "StatsAnd: 3",

            # OK/UP
            "Stats: state = 0",
            "Stats: worst_service_state = 0",
            "Stats: scheduled_downtime_depth = 0",
            "StatsAnd: 3",
        ])

    @classmethod
    def _test_elements(cls):
        test_sites = [
            (
                "Hamburg",
                "ham",
                (
                    240,  # critical hosts
                    111,  # hosts with unknowns
                    100,  # hosts with warnings
                    50,  # hosts in downtime
                    12335,  # OK
                )),
            ("München", "muc", (
                0,
                1,
                5,
                0,
                100,
            )),
            ("Darmstadt", "dar", (
                305,
                10,
                4445,
                0,
                108908,
            )),
            ("Berlin", "ber", (
                0,
                4500,
                0,
                6000,
                3101101,
            )),
            ("Essen", "ess", (
                40024,
                23,
                99299,
                60,
                2498284,
            )),
            ("Gutstadt", "gut", (
                0,
                0,
                0,
                0,
                668868,
            )),
            ("Schlechtstadt", "sch", (
                548284,
                0,
                0,
                0,
                0,
            )),
        ]
        elements: List[ABCElement] = []
        for site_name, site_id, states in test_sites:
            parts = []
            total = 0
            for title, css_class, count in zip([
                    "Critical hosts",
                    "Hosts with unknowns",
                    "Hosts with warnings",
                    "Hosts in downtime",
                    "OK/UP",
            ], ["critical", "unknown", "warning", "downtime", "ok"], states):
                parts.append(Part(title=title, css_class=css_class, count=count))
                total += count

            total_part = Part(title="Total", css_class="", count=total)

            elements.append(
                SiteElement(
                    title=site_name,
                    url_add_vars={
                        "site": site_id,
                    },
                    parts=parts,
                    total=total_part,
                    tooltip=cls._render_tooltip(site_name, parts, total_part),
                ))

        for state, tooltip in sorted(sites.site_state_titles().items()):
            elements.append(
                IconElement(
                    title="Site: %s" % state,
                    css_class="site_%s" % state,
                    tooltip=tooltip,
                ))

        return elements

    @classmethod
    def _render_tooltip(cls, title: str, parts: List[Part], total_part: Part) -> str:
        with html.plugged():
            html.h3(title)
            html.open_table()
            for part in parts:
                html.open_tr()
                html.td("", class_=["color", part.css_class])
                html.td(str(part.count), class_="count")
                html.td(part.title, class_="title")
                html.close_tr()

            html.open_tr()
            html.td("", class_="color")
            html.td(str(total_part.count), class_="count")
            html.td(total_part.title, class_="title")
            html.close_tr()

            html.close_table()
            return html.drain()


@dashlet_registry.register
class SiteOverviewDashlet(ABCFigureDashlet):
    @classmethod
    def type_name(cls):
        return "site_overview"

    @classmethod
    def title(cls):
        return _("Site overview")

    @classmethod
    def description(cls):
        return _("Displays either sites and states or hosts and states of a site")

    @classmethod
    def single_infos(cls):
        return []

    @staticmethod
    def _vs_elements():
        return []

    @staticmethod
    def generate_response_data(properties, context, settings):
        return SiteOverviewDashletDataGenerator.generate_response_data(
            properties, context, settings)


@page_registry.register_page("ajax_host_overview_tooltip")
class HostOverviewTooltipPage(AjaxPage):
    def page(self):
        title = html.request.get_str_input_mandatory("title")
        host_css_class = html.request.get_str_input_mandatory("host_css_class")
        service_css_class = html.request.get_str_input_mandatory("service_css_class")
        num_services = html.request.get_integer_input_mandatory("num_services")
        num_problems = html.request.get_integer_input_mandatory("num_problems")

        with html.plugged():
            html.h3(title)
            html.span(
                _("Host is %s" %
                  (host_css_class if host_css_class != "downtime" else "in downtime")))

            if host_css_class != "up":
                return {"host_tooltip": html.drain()}

            html.open_table()
            problem_services_str = _("problem services")
            if num_problems == 1:
                problem_services_str = _("service in %s state" % service_css_class)
            elif num_problems > 1:
                problem_services_str += _(" (worst state: %s)" % service_css_class)

            html.open_tr()
            html.td(str(num_services), class_="count")
            html.td(_("services") if num_services > 1 else _("service"))
            html.close_tr()

            html.open_tr()
            html.td(str(num_problems), class_="count")
            html.td(problem_services_str)
            html.close_tr()

            html.close_table()
            return {"host_tooltip": html.drain()}
