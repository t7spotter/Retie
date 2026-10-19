"""The /mcp/ MCP server: handshake, tool listing, tool calls, auth isolation."""

import json

import pytest
from rest_framework.authtoken.models import Token

pytestmark = pytest.mark.django_db

MCP = "/mcp/"


def rpc(client, token, method, params=None, request_id=1):
    payload = {"jsonrpc": "2.0", "method": method, "id": request_id}
    if params is not None:
        payload["params"] = params
    return client.post(
        MCP,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token.key}",
    )


@pytest.fixture
def token(user):
    return Token.objects.create(user=user)


def test_unauthenticated_requests_are_rejected(client):
    response = client.post(MCP, data='{"jsonrpc":"2.0","method":"initialize","id":1}',
                           content_type="application/json")
    assert response.status_code == 401
    assert "Bearer" in response["WWW-Authenticate"]


def test_initialize_handshake(client, token):
    response = rpc(client, token, "initialize",
                   {"protocolVersion": "2025-03-26", "capabilities": {}})
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["serverInfo"]["name"] == "retie"
    assert "tools" in result["capabilities"]


def test_initialized_notification_gets_202(client, token):
    response = client.post(
        MCP,
        data=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token.key}",
    )
    assert response.status_code == 202


def test_ping(client, token):
    assert rpc(client, token, "ping").json()["result"] == {}


def test_tools_list(client, token):
    result = rpc(client, token, "tools/list").json()["result"]
    names = {tool["name"] for tool in result["tools"]}
    assert {"create_link", "list_links", "get_link", "update_link",
            "delete_link", "get_link_analytics", "get_dashboard_stats"} <= names
    for tool in result["tools"]:
        assert tool["inputSchema"]["type"] == "object"


def test_create_and_get_link_roundtrip(client, token):
    created = rpc(client, token, "tools/call", {"name": "create_link",
                   "arguments": {"destination": "https://example.com/doc"}}).json()["result"]
    assert created.get("isError") is not True
    payload = json.loads(created["content"][0]["text"])
    assert payload["destination"] == "https://example.com/doc"
    code = payload["code"]

    fetched = json.loads(
        rpc(client, token, "tools/call",
            {"name": "get_link", "arguments": {"code": code}}).json()["result"]["content"][0]["text"]
    )
    assert fetched["code"] == code
    assert fetched["short_url"].endswith(f"/{code}")


def test_create_with_custom_alias_and_collision(client, token):
    args = {"destination": "https://example.com/a", "alias": "docs-mcp"}
    first = json.loads(rpc(client, token, "tools/call",
                           {"name": "create_link", "arguments": args}).json()["result"]["content"][0]["text"])
    assert first["code"] == "docs-mcp"
    second = rpc(client, token, "tools/call", {"name": "create_link", "arguments": args})
    result = second.json()["result"]
    assert result["isError"] is True
    assert ("already in use" in result["content"][0]["text"]
            or "already exists" in result["content"][0]["text"])


def test_create_rejects_non_http_destination(client, token):
    result = rpc(client, token, "tools/call", {"name": "create_link",
                 "arguments": {"destination": "ftp://example.com/x"}}).json()["result"]
    assert result["isError"] is True


def test_unknown_tool_is_tool_level_error(client, token):
    response = rpc(client, token, "tools/call", {"name": "nope", "arguments": {}})
    assert response.json()["error"]["code"] == -32602


def test_unknown_method(client, token):
    response = rpc(client, token, "wat/mah", {})
    assert response.json()["error"]["code"] == -32601


def test_malformed_json(client, token):
    response = client.post(MCP, data="{oops", content_type="application/json",
                           HTTP_AUTHORIZATION=f"Bearer {token.key}")
    assert response.json()["error"]["code"] == -32700


def test_tool_error_for_missing_link(client, token):
    result = rpc(client, token, "tools/call",
                 {"name": "get_link", "arguments": {"code": "ghost"}}).json()["result"]
    assert result["isError"] is True
    assert "ghost" in result["content"][0]["text"]


def test_ownership_isolation(client, token, other_user):
    """A second user's key cannot read, update or delete someone else's link."""
    other_token = Token.objects.create(user=other_user)
    created = json.loads(rpc(client, token, "tools/call", {"name": "create_link",
                         "arguments": {"destination": "https://example.com/1"}})
                         .json()["result"]["content"][0]["text"])
    code = created["code"]

    def other_call(name):
        return client.post(
            MCP,
            data=json.dumps({"jsonrpc": "2.0", "method": "tools/call", "id": 9,
                             "params": {"name": name, "arguments": {"code": code}}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {other_token.key}",
        )

    assert other_call("get_link").json()["result"]["isError"] is True
    assert other_call("delete_link").json()["result"]["isError"] is True


def test_update_and_analytics_flow(client, token, user, settings):
    from links.models import Link

    link = Link.objects.create(user=user, code="updme", destination="https://example.com/old")
    updated = json.loads(rpc(client, token, "tools/call", {"name": "update_link",
                         "arguments": {"code": "updme", "destination": "https://example.com/new",
                                       "is_active": False}}).json()["result"]["content"][0]["text"])
    assert updated["destination"] == "https://example.com/new"
    assert updated["is_active"] is False
    link.refresh_from_db()
    assert link.destination == "https://example.com/new"

    response = rpc(client, token, "tools/call",
                   {"name": "get_link_analytics", "arguments": {"code": "updme", "days": 7}})
    analytics = json.loads(response.json()["result"]["content"][0]["text"])
    assert len(analytics["clicks_over_time"]) == 7


def test_delete_link(client, token):
    created = json.loads(rpc(client, token, "tools/call", {"name": "create_link",
                         "arguments": {"destination": "https://example.com/bye"}})
                         .json()["result"]["content"][0]["text"])
    code = created["code"]
    deleted = json.loads(rpc(client, token, "tools/call",
                             {"name": "delete_link", "arguments": {"code": code}})
                         .json()["result"]["content"][0]["text"])
    assert deleted["deleted"] == code


def test_dashboard_stats_tool(client, token):
    result = json.loads(
        rpc(client, token, "tools/call", {"name": "get_dashboard_stats",
            "arguments": {}}).json()["result"]["content"][0]["text"]
    )
    assert result["total_links"] == 0
    assert isinstance(result["clicks_last_30_days"], int)
    assert "total_clicks" in result
