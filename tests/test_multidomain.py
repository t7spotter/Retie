"""Multi-domain: pin links to extra hostnames from EXTRA_DOMAINS."""

import pytest
from django.test import Client, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from tests.conftest import make_link

from links.models import Link

pytestmark = pytest.mark.django_db

EXTRA = override_settings(
    EXTRA_DOMAINS=["go.example.org", "sho.rt"],
    ALLOWED_HOSTS=["testserver", "127.0.0.1", "localhost", "go.example.org", "sho.rt"],
)


@EXTRA
def test_short_url_honors_pinned_domain(user):
    link = make_link(user, code="pin")
    assert link.short_url.endswith("/pin")
    Link.objects.filter(pk=link.pk).update(domain="go.example.org")
    link.refresh_from_db()
    assert link.short_url.startswith("http://127.0.0.1") or "go.example.org" in link.short_url
    # Scheme comes from SITE_URL; host is the pinned domain.
    assert link.short_url.split("://", 1)[0] in ("http", "https")
    assert link.short_url.split("://", 1)[1] == "go.example.org/pin"


@EXTRA
def test_redirect_host_binding(client, user):
    make_link(user, code="bound9", domain="go.example.org")
    # Wrong host: 404 even though the code exists.
    assert client.get("/bound9").status_code == 404
    # Pinned host: works.
    assert client.get("/bound9", HTTP_HOST="go.example.org").status_code == 302
    # A second configured domain does not serve it either.
    assert client.get("/bound9", HTTP_HOST="sho.rt").status_code == 404


@EXTRA
def test_unpinned_links_serve_everywhere(client, user):
    make_link(user, code="free1")
    assert client.get("/free1").status_code == 302
    assert client.get("/free1", HTTP_HOST="go.example.org").status_code == 302


@EXTRA
def test_qr_and_row_use_pinned_url(client, user):
    import io

    import segno

    link = make_link(user, code="qrpin", domain="go.example.org")
    qr = client.get("/qrpin.qr.svg", HTTP_HOST="go.example.org").content.decode()
    # The SVG is a pure matrix, no URL text — compare byte-for-byte against
    # what segno would emit for the pinned short URL.
    expected = io.BytesIO()
    segno.make(link.short_url, error="m").save(
        expected, kind="svg", scale=8, border=2, dark="#0e353c", light="#ffffff"
    )
    assert qr == expected.getvalue().decode()
    body = make_client_for(user).get("/dashboard/").content.decode()
    assert "go.example.org/qrpin" in body


def make_client_for(user):
    from django.test import Client

    client = Client()
    client.login(username=user.username, password="correct-horse-42")
    return client


@EXTRA
def test_edit_sets_and_clears_domain(client_user, user):
    link = make_link(user, code="dompin")
    response = client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {
            "destination": link.destination,
            "code": link.code,
            "is_active": "on",
            "domain": "sho.rt",
        },
    )
    assert response.status_code == 302
    link.refresh_from_db()
    assert link.domain == "sho.rt"

    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {"destination": link.destination, "code": link.code, "is_active": "on"},
    )
    link.refresh_from_db()
    assert link.domain == ""


@EXTRA
def test_edit_only_offers_configured_domains(client_user, user):
    link = make_link(user, code="badmel")
    response = client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {
            "destination": link.destination,
            "code": link.code,
            "is_active": "on",
            "domain": "evil.example",
        },
    )
    assert response.status_code == 200  # invalid choice -> form redisplay
    link.refresh_from_db()
    assert link.domain == ""


@EXTRA
def test_api_domain(client_user, user):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    created = api.post(
        "/api/v1/links/",
        {"destination": "https://example.com/dom", "domain": "go.example.org"},
    )
    assert created.status_code == 201
    assert created.json()["short_url"].split("://")[1] == f"go.example.org/{created.json()['code']}"
    assert created.json()["domain"] == "go.example.org"

    rejected = api.post(
        "/api/v1/links/", {"destination": "https://example.com/dom", "domain": "nope.example"}
    )
    assert rejected.status_code == 400
    assert "EXTRA_DOMAINS" in str(rejected.json())


@EXTRA
def test_mcp_domain(client_user, user):
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
                    "destination": "https://example.com/mcpdom",
                    "domain": "sho.rt",
                },
            },
        }
    )
    assert created["short_url"].split("://")[1] == f"sho.rt/{created['code']}"
    released = rpc(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 2,
            "params": {
                "name": "update_link",
                "arguments": {"code": created["code"], "domain": None},
            },
        }
    )
    assert released["domain"] == ""
