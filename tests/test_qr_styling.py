"""Per-link QR styling: owner-chosen dark/light colors on the SVG."""

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from tests.conftest import make_link

pytestmark = pytest.mark.django_db


def _compressed(hexcolor):
    """Segno compresses #aabbcc-style colors to short form (#abc)."""
    r, g, b = (hexcolor[i : i + 2] for i in (1, 3, 5))
    if r[0] == r[1] and g[0] == g[1] and b[0] == b[1]:
        return f"#{r[0]}{g[0]}{b[0]}"
    return hexcolor


def test_default_colors_when_unset(client, user):
    make_link(user, code="qrdef")
    body = client.get("/qrdef.qr.svg").content.decode()
    assert "#0e353c" in body
    assert _compressed("#ffffff") in body


def test_custom_colors_rendered(client, user):
    from links.models import Link

    link = make_link(user, code="qrcustom")
    Link.objects.filter(pk=link.pk).update(qr_dark="#123456", qr_light="#abcdef")
    body = client.get("/qrcustom.qr.svg").content.decode()
    assert _compressed("#123456") in body
    assert _compressed("#abcdef") in body


def test_edit_page_sets_colors(client_user, user):
    link = make_link(user, code="qrset")
    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {
            "destination": link.destination,
            "code": link.code,
            "is_active": "on",
            "qr_dark": "#336699",
            "qr_light": "#eeaa11",
        },
    )
    link.refresh_from_db()
    assert link.qr_dark == "#336699"
    assert link.qr_light == "#eeaa11"


def test_edit_page_rejects_bad_hex(client_user, user):
    link = make_link(user, code="qrnope")
    response = client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {
            "destination": link.destination,
            "code": link.code,
            "is_active": "on",
            "qr_dark": "dodgerblue",
        },
    )
    assert "hex color" in response.content.decode()
    link.refresh_from_db()
    assert link.qr_dark == ""


def test_qr_code_input_still_public_with_colors(client, user):
    make_link(user, code="qrpub", qr_dark="#123456")
    assert client.get("/qrpub.qr.svg").status_code == 200


def test_api_qr_fields(client_user, user):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    created = api.post(
        "/api/v1/links/",
        {"destination": "https://example.com/qr", "qr_dark": "123456", "qr_light": "#ABCDEF"},
    )
    assert created.status_code == 201
    assert created.json()["qr_dark"] == "#123456"
    assert created.json()["qr_light"] == "#abcdef"

    bad = api.patch(
        "/api/v1/links/" + created.json()["code"] + "/",
        {"qr_dark": "9999999"},  # 7 chars, fails the hex pattern
        format="json",
    )
    assert bad.status_code == 400
    assert "hex" in str(bad.json())


def test_mcp_qr_colors(client_user, user):
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
                    "destination": "https://example.com/mcpqr",
                    "qr_dark": "#654321",
                },
            },
        }
    )
    assert created["qr_dark"] == "#654321"
    updated = rpc(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 2,
            "params": {
                "name": "update_link",
                "arguments": {"code": created["code"], "qr_light": "#cccccc"},
            },
        }
    )
    assert updated["qr_light"] == "#cccccc"
