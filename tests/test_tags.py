"""Tags: per-user labels on links, dashboard chips, API and MCP filters."""

import json

import pytest

from tests.conftest import make_link

pytestmark = pytest.mark.django_db


def test_create_link_with_tags_via_dashboard_form(client_user):
    response = client_user.post(
        "/dashboard/links/new/",
        {"destination": "https://example.com/a", "code": "", "tags": "Docs, marketing"},
    )
    assert response.status_code == 302  # non-HTMX posts redirect
    from links.models import Link

    link = Link.objects.get(user__username="alice")
    assert sorted(link.tags.values_list("name", flat=True)) == ["docs", "marketing"]


def test_tags_are_normalised_and_reused(client_user, user):
    from links.models import Tag

    client_user.post(
        "/dashboard/links/new/", {"destination": "https://example.com/a", "tags": "docs, docs, marketing"}
    )
    client_user.post(
        "/dashboard/links/new/", {"destination": "https://example.com/b", "tags": "DOCS"}
    )
    # One Tag row per name per user, shared by both links.
    assert Tag.objects.filter(user=user).count() == 2


def test_too_many_tags_rejected(client_user):
    response = client_user.post(
        "/dashboard/links/new/",
        {"destination": "https://example.com/x", "tags": "a,b,c,d,e,f"},
    )
    assert "at most 5" in response.content.decode()


def test_invalid_tag_character_rejected(client_user):
    response = client_user.post(
        "/dashboard/links/new/",
        {"destination": "https://example.com/x", "tags": "red/blue"},
    )
    assert "a-z" in response.content.decode()


def test_edit_replaces_tags(client_user):
    from links.models import Link

    client_user.post(
        "/dashboard/links/new/", {"destination": "https://example.com/e1", "tags": "old"}
    )
    link = Link.objects.get(user__username="alice")
    old_code = link.code
    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {"destination": link.destination, "code": link.code, "tags": "new, second"},
    )
    link.refresh_from_db()
    assert link.code == old_code  # editing keeps the alias when sent
    assert sorted(link.tags.values_list("name", flat=True)) == ["new", "second"]


def test_dashboard_tag_filter(client_user):
    response = client_user.get("/dashboard/links/table/", {"tag": "work"})
    assert response.status_code == 200


def test_api_lists_tags_and_filters(client_user, user):
    from rest_framework.test import APIClient
    from rest_framework.authtoken.models import Token

    make_link(user, code="tagged-api")
    link = user.links.get(code="tagged-api")
    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {"destination": link.destination, "code": link.code, "tags": "release"},
    )
    api = APIClient()
    token = Token.objects.create(user=user)
    api.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    response = api.get("/api/v1/links/")
    data = response.json()
    results = data.get("results") if isinstance(data, dict) and "results" in data else data
    assert results[0]["tags"] == ["release"]

    response = api.get("/api/v1/links/", {"tag": "release"})
    data = response.json()
    results = data.get("results") if isinstance(data, dict) and "results" in data else data
    assert [entry["code"] for entry in results] == ["tagged-api"]


def test_mcp_list_filters_by_tag(client_user, user):
    from rest_framework.authtoken.models import Token

    make_link(user, code="mcp-tagged")
    token = Token.objects.create(user=user)

    def rpc(payload):
        return client_user.post(
            "/mcp/", data=json.dumps(payload), content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {token.key}",
        )

    created = json.loads(
        rpc({"jsonrpc": "2.0", "method": "tools/call", "id": 1,
             "params": {"name": "create_link",
                        "arguments": {"destination": "https://example.com/m", "tags": "launch"}}}
            ).json()["result"]["content"][0]["text"]
    )
    tagged = json.loads(
        rpc({"jsonrpc": "2.0", "method": "tools/call", "id": 2,
             "params": {"name": "list_links", "arguments": {"tag": "launch"}}}
            ).json()["result"]["content"][0]["text"]
    )
    assert [row["code"] for row in tagged["links"]] == [created["code"]]

    listed = json.loads(
        rpc({"jsonrpc": "2.0", "method": "tools/call", "id": 3,
             "params": {"name": "list_links", "arguments": {}}}
            ).json()["result"]["content"][0]["text"]
    )
    row = next(link for link in listed["links"] if link["code"] == created["code"])
    assert row["tags"] == ["launch"]
