"""Per-link permanent (301) redirect option.

Default stays 302 (browsers never cache it, click counts stay exact);
setting permanent swaps to a 301 that browsers themselves cache.
"""

import json

import pytest
from tests.conftest import make_link

pytestmark = pytest.mark.django_db


def test_default_redirect_is_302(client, user):
    make_link(user, code="flex")
    response = client.get("/flex")
    assert response.status_code == 302
    assert response["Cache-Control"] == "no-store"


def test_permanent_link_redirects_301(client, user):
    make_link(user, code="pinned", permanent=True)
    response = client.get("/pinned")
    assert response.status_code == 301
    assert response.url == "https://example.com/page"
    # Cachable on purpose (that's the point of 301) but bounded.
    assert response["Cache-Control"] == "max-age=3600"


def test_edit_page_toggles_permanent(client_user, user, client):
    make_link(user, code="togs")
    link = user.links.get(code="togs")
    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {"destination": link.destination, "code": link.code, "is_active": "on", "permanent": "on"},
    )
    link.refresh_from_db()
    assert link.permanent is True
    assert client.get("/togs").status_code == 301


def test_api_permanent_is_writable_and_listed(client_user, user, client):
    from rest_framework.authtoken.models import Token
    from rest_framework.test import APIClient

    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    response = api.post(
        "/api/v1/links/",
        {"destination": "https://example.com/moved", "permanent": True},
    )
    assert response.status_code == 201
    assert response.json()["permanent"] is True
    assert client.get("/" + response.json()["code"]).status_code == 301


def test_mcp_create_and_update_permanent(client_user, user):
    from rest_framework.authtoken.models import Token

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
                "arguments": {"destination": "https://example.com/p", "permanent": True},
            },
        }
    )
    assert created["permanent"] is True

    updated = rpc(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 2,
            "params": {
                "name": "update_link",
                "arguments": {"code": created["code"], "permanent": False},
            },
        }
    )
    assert updated["permanent"] is False
