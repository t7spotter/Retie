"""Private per-link notes: owners-only, never public."""

import json

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from tests.conftest import make_link

pytestmark = pytest.mark.django_db


def test_edit_page_sets_and_clears_note(client_user, user):
    link = make_link(user, code="note1")
    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {
            "destination": link.destination,
            "code": link.code,
            "is_active": "on",
            "notes": "kill on Friday",
        },
    )
    link.refresh_from_db()
    assert link.notes == "kill on Friday"

    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {"destination": link.destination, "code": link.code, "is_active": "on", "notes": ""},
    )
    link.refresh_from_db()
    assert link.notes == ""


def test_note_never_reaches_public_pages(client, user):
    link = make_link(user, code="note2", notes="secret-invoice-42")
    for path in ("/note2", "/stats/note2", "/note2.qr.svg"):
        body = client.get(path).content.decode()
        assert "secret-invoice-42" not in body


def test_api_notes_roundtrip(client_user, user):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    created = api.post(
        "/api/v1/links/",
        {"destination": "https://example.com/n", "notes": "from api"},
    )
    assert created.status_code == 201
    assert created.json()["notes"] == "from api"

    updated = api.patch("/api/v1/links/" + created.json()["code"] + "/", {"notes": ""})
    assert updated.json()["notes"] == ""


def test_mcp_notes_roundtrip(client_user, user):
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
                "arguments": {"destination": "https://example.com/mcpnote", "notes": "agent note"},
            },
        }
    )
    assert created["notes"] == "agent note"
    updated = rpc(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 2,
            "params": {
                "name": "update_link",
                "arguments": {"code": created["code"], "notes": ""},
            },
        }
    )
    assert updated["notes"] == ""
