"""A/B split testing: variant B destination, rotation, per-variant stats."""

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from tests.conftest import make_link

from analytics.models import ClickEvent
from links.models import Link

pytestmark = pytest.mark.django_db


def test_rotation_serves_both_variants(client, user):
    link = make_link(user, code="abtest", variant_b="https://example.com/b")
    for _ in range(60):
        response = client.get("/abtest")
        assert response.status_code == 302
        assert response["Location"] in ("https://example.com/page", "https://example.com/b")
    variants = set(ClickEvent.objects.values_list("variant", flat=True))
    both = {c.variant for c in ClickEvent.objects.all()}
    assert "a" in both and "b" in both
    assert all(variant in ("a", "b") for variant in variants)


def test_no_variant_b_means_plain_redirect(client, user):
    make_link(user, code="plain")
    response = client.get("/plain")
    assert response["Location"] == "https://example.com/page"
    assert ClickEvent.objects.get().variant == "a"


def test_ab_link_always_302_not_301(client, user):
    # Permanent redirects would cache one arm of the test forever.
    link = make_link(user, code="ab301", permanent=True, variant_b="https://example.com/b")
    for _ in range(6):
        assert client.get("/ab301").status_code == 302
    link.refresh_from_db()
    assert link.permanent is True  # the flag itself is untouched


def test_variant_breakdown_and_page(client_user, user):
    link = make_link(user, code="abstats", variant_b="https://example.com/b")
    for variant in ("a", "a", "b"):
        ClickEvent.objects.create(link=link, device="desktop", variant=variant)
    body = client_user.get("/dashboard/links/abstats/analytics/").content.decode()
    assert "A/B split test" in body
    assert body.count("tabular-nums") >= 2  # counts render (smoke)

    from analytics import queries

    breakdown = queries.variant_breakdown(user, link=link)
    assert breakdown == {"a": 2, "b": 1}


def test_csv_includes_variant_rows(client_user, user):
    import csv
    import io

    link = make_link(user, code="abcsv", variant_b="https://example.com/b")
    ClickEvent.objects.create(link=link, device="desktop", variant="a")
    ClickEvent.objects.create(link=link, device="desktop", variant="b")
    response = client_user.get("/dashboard/links/abcsv/analytics.csv")
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    variant_rows = [r for r in rows if r[0] == "variant"]
    assert {"a": "1", "b": "1"} == {r[1]: r[2] for r in variant_rows}


def test_no_variant_card_without_test(client_user, user):
    make_link(user, code="novar")
    body = client_user.get("/dashboard/links/novar/analytics/").content.decode()
    assert "A/B split test" not in body


def test_edit_sets_and_clears_variant_b(client_user, user):
    link = make_link(user, code="abedit")
    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {
            "destination": link.destination,
            "code": link.code,
            "is_active": "on",
            "variant_b": "https://example.com/variant-b",
        },
    )
    link.refresh_from_db()
    assert link.variant_b == "https://example.com/variant-b"

    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {"destination": link.destination, "code": link.code, "is_active": "on"},
    )
    link.refresh_from_db()
    assert link.variant_b == ""


def test_edit_validates_variant_b_destination(client_user, user):
    link = make_link(user, code="abadb")
    response = client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {
            "destination": link.destination,
            "code": link.code,
            "is_active": "on",
            "variant_b": "ftp://example.com/nope",
        },
    )
    assert "variant_b" in response.context["form"].errors
    link.refresh_from_db()
    assert link.variant_b == ""


def test_api_variant_b_field(client_user, user):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    created = api.post(
        "/api/v1/links/",
        {"destination": "https://example.com/a", "variant_b": "https://example.com/b"},
    )
    assert created.status_code == 201
    assert created.json()["variant_b"] == "https://example.com/b"

    ended = api.patch(
        "/api/v1/links/" + created.json()["code"] + "/",
        {"variant_b": ""},
        format="json",
    )
    assert ended.json()["variant_b"] == ""


def test_mcp_variant_b_roundtrip(client_user, user):
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
                    "destination": "https://example.com/mcpa",
                    "variant_b": "https://example.com/mcpb",
                },
            },
        }
    )
    assert created["variant_b"] == "https://example.com/mcpb"
    updated = rpc(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 2,
            "params": {
                "name": "update_link",
                "arguments": {"code": created["code"], "variant_b": ""},
            },
        }
    )
    assert updated["variant_b"] == ""
