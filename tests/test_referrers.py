"""Referrer allow-list (hotlink protection) on redirects."""

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from tests.conftest import make_link

from links.forms import LinkForm
from links.models import normalize_referrer_domains

pytestmark = pytest.mark.django_db


def test_normalizer_accepts_domains_and_strips_junk():
    assert normalize_referrer_domains("News.Example.com/path; https://blog.example.com, ") == [
        "news.example.com",
        "blog.example.com",
    ]
    with pytest.raises(Exception):
        normalize_referrer_domains("not a domain")
    assert normalize_referrer_domains("") == []


def test_no_referer_always_allowed(client, user):
    make_link(user, code="open1", allowed_referrers="news.example.com\n")
    # No HTTP_REFERER: typed URL / QR scan.
    assert client.get("/open1").status_code == 302


def test_foreign_referer_blocked_with_403_and_no_click(client, user):
    from analytics.models import ClickEvent

    link = make_link(user, code="guard", allowed_referrers="news.example.com\n")

    response = client.get("/guard", HTTP_REFERER="https://evil.example.org/page")
    assert response.status_code == 403
    assert ClickEvent.objects.count() == 0
    assert "https://example.com/page" not in response.content.decode()  # no destination leak

    # Allowed host (exact + subdomain) passes.
    assert client.get("/guard", HTTP_REFERER="https://news.example.com/x").status_code == 302
    assert client.get("/guard", HTTP_REFERER="https://old.news.example.com/").status_code == 302
    assert ClickEvent.objects.count() == 2


def test_empty_list_never_restricts(client, user):
    make_link(user, code="wild9")
    assert client.get("/wild9", HTTP_REFERER="https://anything.example/").status_code == 302


def test_form_normalizes_list(client_user, user):
    link = make_link(user, code="norm1")
    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {
            "destination": link.destination,
            "code": link.code,
            "is_active": "on",
            "allowed_referrers": "https://Blog.Example.com/post; docs.example.com",
        },
    )
    link.refresh_from_db()
    assert link.allowed_referrers == "blog.example.com\ndocs.example.com"


def test_form_rejects_bad_domain_with_friendly_error(client_user, user):
    link = make_link(user, code="badro")
    response = client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {
            "destination": link.destination,
            "code": link.code,
            "is_active": "on",
            "allowed_referrers": "!!!",
        },
    )
    assert "not a valid public hostname" in response.content.decode()
    link.refresh_from_db()
    assert link.allowed_referrers == ""


def test_api_list_interface(client_user, user):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    r = api.post(
        "/api/v1/links/",
        {"destination": "https://example.com/ref", "allowed_referrers": "A.example.com, b.example.com"},
    )
    assert r.status_code == 201
    assert r.json()["allowed_referrers"] == ["a.example.com", "b.example.com"]

    # PATCH bodies must be JSON-encoded (DRF only parses form data for POST).
    cleared = api.patch(
        f"/api/v1/links/{r.json()['code']}/", {"allowed_referrers": ""}, format="json"
    )
    assert cleared.json()["allowed_referrers"] == []


def test_mcp_allows_list(client_user, user):
    import json

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
                    "destination": "https://example.com/mcprv",
                    "allowed_referrers": ["mcp.example.com"],
                },
            },
        }
    )
    assert created["allowed_referrers"] == ["mcp.example.com"]
