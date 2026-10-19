"""Per-link gate passwords: unlock page, session once-per-browser, remove."""

import json

import pytest
from django.contrib.auth.hashers import make_password
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from tests.conftest import make_link

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_cache():
    """Rate-limit counters live in the LocMem cache; isolate them per test."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def test_brute_force_brake(client, user, locked_link):
    for _ in range(20):
        assert client.post("/vault", {"password": "bad"}).status_code == 200
    # 21st wrong answer from the same IP: blocked outright.
    assert client.post("/vault", {"password": "opensesame"}).status_code == 403


@pytest.fixture
def locked_link(user):
    return make_link(user, code="vault", password_hash=make_password("opensesame"))


def test_locked_link_shows_unlock_page_not_redirect(client, user, locked_link):
    response = client.get("/vault")
    assert response.status_code == 200
    body = response.content.decode()
    assert "protected" in body
    assert "https://example.com/page" not in body  # no destination leak


def test_wrong_password_rerenders_with_error(client, user, locked_link):
    response = client.post("/vault", {"password": "letmein"})
    assert response.status_code == 200
    assert "Wrong password" in response.content.decode()
    # And stayed locked.
    assert client.get("/vault").status_code == 200


def test_correct_password_redirects_then_is_session_persistent(client, user, locked_link):
    response = client.post("/vault", {"password": "opensesame"}, follow=False)
    # Round-trips through the short code (so the real visit records a click).
    assert response.status_code == 302
    assert response.url.endswith("/vault")

    through = client.get("/vault")
    assert through.status_code == 302
    assert through.url == "https://example.com/page"

    # No second prompt in the same browser session.
    assert client.get("/vault").status_code == 302


def test_unlock_never_adds_a_second_client_magic(client, user, locked_link):
    """A fresh browser session is locked again even if this one unlocked."""
    from django.test import Client

    client.post("/vault", {"password": "opensesame"})
    assert client.get("/vault").status_code == 302
    fresh = Client()
    response = fresh.get("/vault")
    assert response.status_code == 200
    assert "https://example.com/page" not in response.content.decode()


def test_locked_link_counts_no_click_until_unlocked(client, user, locked_link):
    from analytics.models import ClickEvent

    client.get("/vault")
    client.post("/vault", {"password": "wrong"})
    assert ClickEvent.objects.count() == 0
    client.post("/vault", {"password": "opensesame"})
    # The unlock round-trips through the short code; the actual redirect is
    # what records the click.
    client.get("/vault")
    assert ClickEvent.objects.count() == 1  # the real redirect only


def test_inactive_link_404s_even_with_password(client, user, locked_link):
    locked_link.is_active = False
    locked_link.save(update_fields=["is_active"])
    assert client.get("/vault").status_code == 404


def test_edit_page_sets_and_clears_password(client_user, user, client):
    link = make_link(user, code="gated")
    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {"destination": link.destination, "code": link.code, "is_active": "on", "password": "hush1"},
    )
    link.refresh_from_db()
    assert link.password_hash  # hashed, not the raw value
    assert "hush1" != link.password_hash

    assert client_user.get("/gated").status_code == 200

    # Clear via checkbox, empty password field.
    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {
            "destination": link.destination,
            "code": link.code,
            "is_active": "on",
            "password": "",
            "remove_password": "on",
        },
    )
    link.refresh_from_db()
    assert link.password_hash == ""
    assert client.get("/gated").status_code == 302


def test_empty_password_edit_keeps_existing(client_user, user):
    link = make_link(user, code="keep", password_hash=make_password("stay"))
    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {"destination": link.destination, "code": link.code, "is_active": "on", "password": ""},
    )
    link.refresh_from_db()
    assert link.password_hash  # untouched


def test_api_sets_password_and_never_reads_it_back(client_user, user, client):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    response = api.post(
        "/api/v1/links/",
        {"destination": "https://example.com/secret", "password": "s3cret"},
    )
    assert response.status_code == 201
    assert "password" not in response.json()
    code = response.json()["code"]

    assert client.get("/" + code).status_code == 200  # unlock page
    unlock = client.post("/" + code, {"password": "s3cret"}, follow=True)
    assert unlock.redirect_chain[-1][0] == "https://example.com/secret"


def test_api_removes_password(client_user, user, client):
    link = make_link(user, code="apigate", password_hash=make_password("z"))
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    response = api.patch(
        f"/api/v1/links/{link.code}/", {"remove_password": True}
    )
    assert response.status_code == 200
    link.refresh_from_db()
    assert link.password_hash == ""


def test_mcp_create_and_update_password(client_user, user, client):
    from django.test import Client

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
                "arguments": {"destination": "https://example.com/v", "password": "mcppw"},
            },
        }
    )
    assert client.get("/" + created["code"]).status_code == 200
    client.post("/" + created["code"], {"password": "mcppw"})
    assert client.get("/" + created["code"]).status_code == 302

    updated = rpc(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 2,
            "params": {
                "name": "update_link",
                "arguments": {"code": created["code"], "remove_password": True},
            },
        }
    )
    anon = Client()
    assert anon.get("/" + created["code"]).status_code == 302
