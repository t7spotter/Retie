"""Scheduled activation: links that go live at a future moment."""

from datetime import timedelta

import json
import pytest
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from tests.conftest import make_link

from links.models import Link

pytestmark = pytest.mark.django_db


def test_future_moment_not_live_yet(client, user):
    make_link(
        user,
        code="laters",
        goes_live_at=timezone.now() + timedelta(hours=3),
    )
    response = client.get("/laters")
    assert response.status_code == 404


def test_past_moment_redirects(client, user):
    make_link(
        user,
        code="early",
        goes_live_at=timezone.now() - timedelta(hours=1),
    )
    assert client.get("/early").status_code == 302


def test_no_moment_redirects(client, user):
    make_link(user, code="nowgo")
    assert client.get("/nowgo").status_code == 302


def test_status_scheduled_on_dashboard(client_user, user):
    make_link(
        user,
        code="sched1",
        goes_live_at=timezone.now() + timedelta(days=2),
    )
    body = client_user.get("/dashboard/").content.decode()
    assert "Scheduled" in body
    link = Link.objects.get(code="sched1")
    assert link.status == "scheduled"
    # Once the moment passes the same link is live again.
    Link.objects.filter(pk=link.pk).update(goes_live_at=timezone.now() - timedelta(minutes=5))
    assert Link.objects.get(code="sched1").status == "active"


def test_edit_sets_and_clears_moment(client_user, user):
    link = make_link(user, code="setgo")
    response = client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {
            "destination": link.destination,
            "code": link.code,
            "is_active": "on",
            "goes_live_at": "2030-05-05T09:30",
        },
    )
    assert response.status_code == 302
    link.refresh_from_db()
    assert link.is_scheduled is True

    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {"destination": link.destination, "code": link.code, "is_active": "on"},
    )
    link.refresh_from_db()
    assert link.goes_live_at is None


def test_edit_rejects_bad_datetime(client_user, user):
    link = make_link(user, code="badgo")
    response = client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {
            "destination": link.destination,
            "code": link.code,
            "is_active": "on",
            "goes_live_at": "not-a-date",
        },
    )
    assert response.status_code == 200  # form redisplay
    link.refresh_from_db()
    assert link.goes_live_at is None


def test_api_goes_live_field(client_user, user):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    moment = (timezone.now() + timedelta(days=1)).replace(microsecond=0)
    created = api.post(
        "/api/v1/links/",
        {"destination": "https://example.com/sched", "goes_live_at": moment.isoformat()},
    )
    assert created.status_code == 201
    assert created.json()["status"] == "scheduled"
    assert created.json()["goes_live_at"].startswith(moment.isoformat()[:19])

    cleared = api.patch(
        "/api/v1/links/" + created.json()["code"] + "/",
        {"goes_live_at": None},
        format="json",
    )
    assert cleared.json()["status"] == "active"
    assert cleared.json()["goes_live_at"] is None


def test_mcp_goes_live_roundtrip(client_user, user):
    token = Token.objects.create(user=user)

    def rpc(payload):
        return json.loads(
            client_user.post(
                "/mcp/",
                data=json.dumps(payload),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Token {token.key}",
            ).json()["result"]["content"][0]["text"]
        )

    created = rpc(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {
                "name": "create_link",
                "arguments": {
                    "destination": "https://example.com/mcpgs",
                    "goes_live_at": "2030-01-01T09:00",
                },
            },
        }
    )
    assert "2030-01-01" in created["goes_live_at"]
    updated = rpc(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 2,
            "params": {
                "name": "update_link",
                "arguments": {"code": created["code"], "goes_live_at": None},
            },
        }
    )
    assert updated["goes_live_at"] is None
