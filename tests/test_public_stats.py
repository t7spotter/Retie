"""Public shareable stats pages: opt-in, aggregates only, no login."""

import json

import pytest
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from tests.conftest import make_link

from analytics.models import ClickEvent

pytestmark = pytest.mark.django_db


def click(link, **kwargs):
    return ClickEvent.objects.create(link=link, **kwargs)


def test_stats_page_hidden_by_default(client, user):
    make_link(user, code="quiet")
    assert client.get("/stats/quiet").status_code == 404


def test_stats_page_public_when_opted_in(client, user):
    link = make_link(user, code="openbook", public_stats=True)
    click(link, device="desktop", country="NL", referrer="https://news.example/")
    click(link, device="mobile", country="DE")
    click(link, device="desktop", country="NL")

    response = client.get("/stats/openbook")
    assert response.status_code == 200
    body = response.content.decode()
    assert "Public link statistics" in body
    # Aggregate counts appear; the destination and click-level detail do not.
    assert "Total clicks" in body
    assert "https://example.com/page" not in body


def test_stats_page_404s_when_link_paused_or_trashed(client, user):
    link = make_link(user, code="paused", public_stats=True, is_active=False)
    make_link(user, code="gone", public_stats=True).delete()  # trash
    trashed = [u for u in user.links.all() if u.code == "gone"]

    assert client.get("/stats/paused").status_code == 404

    trashed_link = make_link(user, code="trashed", public_stats=True)
    from links.models import Link as L

    b = L.all_objects.get(code="trashed")
    b.deleted_at = timezone.now()
    b.save(update_fields=["deleted_at"])
    assert client.get("/stats/trashed").status_code == 404


def test_stats_page_404s_when_expired(client, user):
    make_link(user, code="stale", public_stats=True, expires_at=timezone.now() - timezone.timedelta(days=1))
    assert client.get("/stats/stale").status_code == 404


def test_stats_persist_for_locked_links_viewers(client, user):
    """Public stats stay visible even while the link itself is gated."""
    from django.contrib.auth.hashers import make_password

    make_link(user, code="gated", public_stats=True, password_hash=make_password("x"))
    response = client.get("/stats/gated")
    assert response.status_code == 200


def test_edit_page_toggles_public_stats(client_user, user):
    link = make_link(user, code="toggly")
    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {"destination": link.destination, "code": link.code, "is_active": "on", "public_stats": "on"},
    )
    link.refresh_from_db()
    assert link.public_stats is True
    assert client_user.get("/stats/toggly").status_code == 200


def test_edit_page_unpublish_removes_access(client_user, user, client):
    link = make_link(user, code="unpub", public_stats=True)
    assert client.get("/stats/unpub").status_code == 200
    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {"destination": link.destination, "code": link.code, "is_active": "on"},
    )
    link.refresh_from_db()
    assert link.public_stats is False
    assert client.get("/stats/unpub").status_code == 404


def test_api_public_stats_field(client_user, user, client):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    response = api.post(
        "/api/v1/links/",
        {"destination": "https://example.com/pub", "public_stats": True},
    )
    assert response.status_code == 201
    assert response.json()["public_stats"] is True
    code = response.json()["code"]
    assert client.get(f"/stats/{code}").status_code == 200


def test_mcp_public_stats_roundtrip(client_user, user):
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
                "arguments": {"destination": "https://example.com/mcppub", "public_stats": True},
            },
        }
    )
    assert created["public_stats"] is True
    updated = rpc(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 2,
            "params": {
                "name": "update_link",
                "arguments": {"code": created["code"], "public_stats": False},
            },
        }
    )
    assert updated["public_stats"] is False
