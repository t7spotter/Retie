"""API keys and the documentation portal (/api/docs/: apiwarden).

Covers the full API-key life cycle: obtain by username+password, use with
both `Token` and `Bearer` Authorization schemes, revoke, plus the web
profile-page key management. The docs portal (apiwarden) is asserted at the
HTTP level: portal page, agent surface (index.json, llms.txt, spec files)
and the MCP server endpoint.
"""

import pytest

from rest_framework.test import APIClient

from links.models import Link
from rest_framework.authtoken.models import Token
from tests.conftest import make_link

pytestmark = pytest.mark.django_db


@pytest.fixture
def api(db):
    return APIClient()


# ---- Obtain / use / revoke ---------------------------------------------------


def test_obtain_token_with_valid_credentials(api, user):
    response = api.post(
        "/api/v1/token/", {"username": "alice", "password": "correct-horse-42"}
    )
    assert response.status_code == 201
    key = response.data["token"]
    assert Token.objects.filter(user=user, key=key).exists()


def test_obtain_token_rejects_bad_password(api):
    response = api.post(
        "/api/v1/token/", {"username": "alice", "password": "wrong"}
    )
    assert response.status_code == 401
    assert "token" not in response.data


def test_obtain_token_rejects_missing_fields(api):
    assert api.post("/api/v1/token/", {}).status_code == 400


def test_obtain_token_returns_existing_key_not_a_second_one(api, user):
    first = api.post(
        "/api/v1/token/", {"username": "alice", "password": "correct-horse-42"}
    ).data["token"]
    again = api.post(
        "/api/v1/token/", {"username": "alice", "password": "correct-horse-42"}
    ).data["token"]
    assert first == again
    assert Token.objects.filter(user=user).count() == 1


@pytest.mark.parametrize("scheme", ["Bearer", "Token"])
def test_api_links_usable_with_key(api, user, scheme):
    key = api.post(
        "/api/v1/token/", {"username": "alice", "password": "correct-horse-42"}
    ).data["token"]
    api.credentials(HTTP_AUTHORIZATION=f"{scheme} {key}")
    assert api.get("/api/v1/links/").status_code == 200
    created = api.post(
        "/api/v1/links/", {"destination": "https://example.com/keyed"}
    )
    assert created.status_code == 201
    assert Link.objects.filter(user=user, destination="https://example.com/keyed").exists()


def test_invalid_key_is_rejected(api):
    an_invalid = "0" * 40
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {an_invalid}")
    assert api.get("/api/v1/links/").status_code in (401, 403)


def test_revoke_token_stops_access(api, user):
    key = api.post(
        "/api/v1/token/", {"username": "alice", "password": "correct-horse-42"}
    ).data["token"]
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {key}")

    assert api.delete("/api/v1/token/").status_code == 204
    assert not Token.objects.filter(user=user).exists()
    assert api.get("/api/v1/links/").status_code in (401, 403)


def test_revoke_without_authentication(api):
    response = api.delete("/api/v1/token/")
    assert response.status_code in (401, 403)


def test_profile_reports_token_presence(api, user):
    api.force_authenticate(user=user)
    assert api.get("/api/v1/me/").data["has_api_token"] is False
    api.post("/api/v1/token/", {"username": "alice", "password": "correct-horse-42"})
    assert api.get("/api/v1/me/").data["has_api_token"] is True


def test_obtain_token_is_rate_limited(api):
    """After the 10/hour budget for credential exchanges, DRF throttles."""
    from django.core.cache import cache

    codes = [
        api.post("/api/v1/token/", {"username": "alice", "password": "nope"}).status_code
        for _ in range(20)
    ]
    assert codes[-1] == 429
    cache.clear()


# ---- Web profile-page API-key management -------------------------------------


def test_profile_page_can_create_and_revoke_key(client, user):
    client.login(username="alice", password="correct-horse-42")

    # Create: the key is shown once, in the page.
    shown = client.post("/accounts/profile/", {"action": "create_api_key"})
    assert shown.status_code == 200
    token = Token.objects.get(user=user)
    assert token.key.encode() in shown.content
    assert client.get("/accounts/profile/").content.count(token.key.encode()) == 0

    # Revoke: gone from the database and the page offers creation again.
    revoked = client.post("/accounts/profile/", {"action": "revoke_api_key"})
    assert revoked.status_code == 200
    assert not Token.objects.filter(user=user).exists()


def test_profile_page_rejects_unknown_action(client, user):
    client.login(username="alice", password="correct-horse-42")
    response = client.post("/accounts/profile/", {"action": "drop_tables"})
    assert response.status_code == 200
    assert not Token.objects.filter(user=user).exists()


# ---- Documentation portal & MCP (apiwarden) -----------------------------------


def test_docs_portal_serves_spec_from_disk(api):
    index = api.get("/api/docs/index.json")
    assert index.status_code == 200
    body = index.json()
    assert body["title"] == "Shorty API"
    assert "/api/v1/links/" in [op["path"] for op in body["operations"]]


@pytest.mark.parametrize(
    "path",
    ["llms.txt", "revision.json", "openapi/shorty.yaml", "types/shorty.ts"],
)
def test_docs_agent_endpoints(api, path):
    assert api.get(f"/api/docs/{path}").status_code == 200


def test_mcp_server_lists_its_tools(api):
    """Agents boot MCP with a JSON-RPC initialize / tools-list handshake."""
    import json

    response = api.post(
        "/api/docs/mcp",
        data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        ),
        content_type="application/json",
    )
    assert response.status_code == 200
    tools = response.json()
    # Either plain JSON-RPC result or an SSE-wrapped frame; both carry tool
    # names — drill down generically.
    text = response.content.decode()
    assert "search_operations" in text or "list_apis" in text


def test_raw_schema_lists_token_endpoint(api):
    import yaml

    response = api.get("/api/schema/")  # OpenAPI YAML from drf-spectacular
    schema = yaml.safe_load(response.content)
    assert "/api/v1/token/" in schema["paths"]
    # Meta endpoints stay out of the contract.
    assert "/api/docs/" not in schema["paths"]
    assert "/api/schema/" not in schema["paths"]
