# !/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2020 tribe29 GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
import json


def test_openapi_password(wsgi_app, with_automation_user, suppress_automation_calls):
    username, secret = with_automation_user
    wsgi_app.set_authorization(('Bearer', username + " " + secret))

    base = '/NO_SITE/check_mk/api/v0'

    resp = wsgi_app.call_method(
        'post',
        base + "/domain-types/password/collections/all",
        params=json.dumps({
            "ident": "foo",
            "title": "foobar",
            "owner": "admin",
            "password": "tt",
            "shared": ["all"],
        }),
        status=200,
        content_type='application/json',
    )

    _resp = wsgi_app.call_method(
        'put',
        base + "/objects/password/foo",
        params=json.dumps({
            "title": "foobu",
            "comment": "Something but nothing random"
        }),
        status=200,
        headers={'If-Match': resp.headers['ETag']},
        content_type='application/json',
    )

    resp = wsgi_app.call_method(
        'get',
        base + "/objects/password/foo",
        status=200,
    )
    assert resp.json["extensions"] == {
        'comment': 'Something but nothing random',
        'docu_url': '',
        'password': 'tt',
        'owned_by': None,
        'shared_with': ['all'],
    }


def test_openapi_password_admin(wsgi_app, with_automation_user, suppress_automation_calls):
    username, secret = with_automation_user
    wsgi_app.set_authorization(('Bearer', username + " " + secret))

    base = '/NO_SITE/check_mk/api/v0'

    _resp = wsgi_app.call_method(
        'post',
        base + "/domain-types/password/collections/all",
        params=json.dumps({
            "ident": "test",
            "title": "Checkmk",
            "owner": "admin",
            "password": "tt",
            "shared": [],
        }),
        status=200,
        content_type='application/json',
    )

    _resp = wsgi_app.call_method(
        'get',
        base + "/objects/password/test",
        status=200,
    )


def test_openapi_password_delete(wsgi_app, with_automation_user, suppress_automation_calls):
    username, secret = with_automation_user
    wsgi_app.set_authorization(('Bearer', username + " " + secret))

    base = '/NO_SITE/check_mk/api/v0'

    _resp = wsgi_app.call_method(
        'post',
        base + "/domain-types/password/collections/all",
        params=json.dumps({
            "ident": "foo",
            "title": "foobar",
            "owner": "admin",
            "password": "tt",
            "shared": ["all"],
        }),
        status=200,
        content_type='application/json',
    )

    resp = wsgi_app.call_method(
        'get',
        base + "/domain-types/password/collections/all",
        status=200,
    )
    assert len(resp.json_body["value"]) == 1

    _resp = wsgi_app.call_method(
        'delete',
        base + "/objects/password/nothing",
        status=404,
    )

    _resp = wsgi_app.call_method(
        'delete',
        base + "/objects/password/foo",
        status=204,
    )

    _resp = wsgi_app.call_method('get', base + "/objects/password/foo", status=404)

    resp = wsgi_app.call_method(
        'get',
        base + "/domain-types/password/collections/all",
        status=200,
    )
    assert len(resp.json_body["value"]) == 0
