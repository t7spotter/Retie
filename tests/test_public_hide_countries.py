"""Public stats privacy toggle: hide the country list on demand."""

import json

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from tests.conftest import make_link

from analytics.models import ClickEvent

pytestmark = pytest.mark.django_db


def test_countries_hidden_when_opted(client_user, user, client):
    link = make_link(user, code="privy", public_stats=True, public_hide_countries=True)
    ClickEvent.objects.create(link=link, device="desktop", country="NL")
    body = client.get("/stats/privy").content.decode()
    assert "Countries" not in body
    assert "NL" not in body


def test_countries_shown_by_default(client_user, user, client):
    link = make_link(user, code="geoall", public_stats=True)
    ClickEvent.objects.create(link=link, device="desktop", country="NL")
    body = client.get("/stats/geoall").content.decode()
    assert "Countries" in body


def test_edit_toggle_roundtrip(client_user, user):
    link = make_link(user, code="tog2", public_stats=True)
    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {
            "destination": link.destination, "code": link.code, "is_active": "on",
            "public_stats": "on", "public_hide_countries": "on",
        },
    )
    link.refresh_from_db()
    assert link.public_hide_countries is True
    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {"destination": link.destination, "code": link.code, "is_active": "on"},
    )
    link.refresh_from_db()
    assert link.public_hide_countries is False


def test_api_flag_written(client_user, user):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    r = api.post("/api/v1/links/", {"destination": "https://example.com/hc", "public_stats": True, "public_hide_countries": True})
    assert r.status_code == 201
    assert r.json()["public_hide_countries"] is True


def test_mcp_flag_roundtrip(client_user, user):
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

    created = rpc({
        "jsonrpc": "2.0", "method": "tools/call", "id": 1,
        "params": {"name": "create_link", "arguments": {
            "destination": "https://example.com/mcphc", "public_stats": True, "public_hide_countries": True}},
    })
    assert created["public_hide_countries"] is True
    updated = rpc({
        "jsonrpc": "2.0", "method": "tools/call", "id": 2,
        "params": {"name": "update_link", "arguments": {"code": created["code"], "public_hide_countries": False}},
    })
    assert updated["public_hide_countries"] is False
