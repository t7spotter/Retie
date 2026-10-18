"""Session-authenticated REST API: permissions, CRUD, analytics, stats."""

from django.utils import timezone

import pytest

from rest_framework.test import APIClient

from links.models import Link
from tests.conftest import make_link

pytestmark = pytest.mark.django_db


@pytest.fixture
def api(db):
    return APIClient()


@pytest.fixture
def auth_api(api, user):
    api.force_authenticate(user=user)
    return api


def _detail(code):
    return f"/api/v1/links/{code}/"


# ---- Authentication & permissions -----------------------------------------


def test_requires_authentication(api):
    # DRF may answer 401 (challenge available) or 403 (no challenge).
    assert api.get("/api/v1/links/").status_code in (401, 403)
    assert api.get(_detail("anything")).status_code in (401, 403, 404)
    assert api.post("/api/v1/links/", {"destination": "https://x.com"}).status_code in (401, 403)
    assert api.get("/api/v1/stats/").status_code in (401, 403)
    assert api.get("/api/v1/me/").status_code in (401, 403)


def test_users_see_only_own_links(auth_api, user, other_user):
    mine = make_link(user, code="mine1")
    theirs = make_link(other_user, code="theirs1")

    codes = {row["code"] for row in auth_api.get("/api/v1/links/").data["results"]}
    assert codes == {"mine1"}

    assert auth_api.get(_detail("theirs1")).status_code == 404


def test_users_cannot_modify_others_links(auth_api, other_user):
    theirs = make_link(other_user, code="protected1")
    assert auth_api.patch(_detail("protected1"), {"title": "x"}).status_code == 404
    assert auth_api.delete(_detail("protected1")).status_code == 404
    assert Link.objects.filter(code="protected1").exists()


# ---- CRUD ------------------------------------------------------------------


def test_create_link_generates_code(auth_api, user):
    response = auth_api.post("/api/v1/links/", {"destination": "https://example.com/page"})
    assert response.status_code == 201
    assert len(response.data["code"]) == 6
    link = Link.objects.get(code=response.data["code"])
    assert link.user == user


def test_create_with_custom_alias(auth_api, user):
    response = auth_api.post(
        "/api/v1/links/", {"destination": "https://example.com", "code": "sprint-42"}
    )
    assert response.status_code == 201
    assert response.data["short_url"].endswith("/sprint-42")


def test_create_conflicting_alias_returns_400(auth_api, user, other_user):
    make_link(other_user, code="busy")
    response = auth_api.post(
        "/api/v1/links/", {"destination": "https://example.com", "code": "busy"}
    )
    assert response.status_code == 400


def test_create_rejects_invalid_destination(auth_api, user):
    response = auth_api.post("/api/v1/links/", {"destination": "ftp://x"})
    assert response.status_code == 400
    assert "destination" in response.data


def test_create_rejects_reserved_alias(auth_api, user):
    response = auth_api.post(
        "/api/v1/links/", {"destination": "https://example.com", "code": "admin"}
    )
    assert response.status_code == 400


def test_create_rejects_past_expiration(auth_api, user):
    response = auth_api.post(
        "/api/v1/links/",
        {
            "destination": "https://example.com",
            "expires_at": "2020-01-01T10:00:00Z",
        },
    )
    assert response.status_code == 400


def test_partial_update_and_full_update(auth_api, user):
    make_link(user, code="upd1")
    assert auth_api.patch(_detail("upd1"), {"title": "patched"}).data["title"] == "patched"
    data = auth_api.put(
        _detail("upd1"),
        {"destination": "https://example.com/new", "code": "upd1", "is_active": True},
    ).data
    assert data["destination"] == "https://example.com/new"


def test_delete(auth_api, user):
    make_link(user, code="k1")
    assert auth_api.delete(_detail("k1")).status_code == 204
    assert not Link.objects.filter(code="k1").exists()


def test_activate_deactivate(auth_api, user):
    make_link(user, code="tog2")
    assert auth_api.post(_detail("tog2") + "deactivate/").data["is_active"] is False
    assert auth_api.post(_detail("tog2") + "activate/").data["is_active"] is True


def test_short_url_uses_configured_site_url(auth_api, user, settings):
    settings.SITE_URL = "https://go.example.org"
    response = auth_api.post(
        "/api/v1/links/", {"destination": "https://example.com", "code": "domtest"}
    )
    assert response.data["short_url"] == "https://go.example.org/domtest"


# ---- Analytics, stats, profile ---------------------------------------------


def test_link_analytics_endpoint(auth_api, user, other_user):
    link = make_link(user, code="ana10")
    from analytics.models import ClickEvent

    ClickEvent.objects.create(link=link, device="desktop")

    data = auth_api.get(_detail("ana10") + "analytics/").data
    assert data["total_clicks"] == 1
    assert len(data["clicks_over_time"]) == 30

    make_link(other_user, code="ana11")
    assert auth_api.get(_detail("ana11") + "analytics/").status_code == 404


def test_stats_endpoint(auth_api, user):
    link = make_link(user, code="stat1")
    from analytics.models import ClickEvent

    ClickEvent.objects.create(link=link)
    stats = auth_api.get("/api/v1/stats/").data
    assert stats["total_links"] == 1
    assert stats["total_clicks"] == 1
    assert stats["clicks_last_30_days"] == 1


def test_profile_endpoint(auth_api, user):
    data = auth_api.get("/api/v1/me/").data
    assert data["username"] == "alice"
    assert data["email"] == "alice@example.com"


# ---- Pagination -------------------------------------------------------------


def test_pagination_is_on_by_default(auth_api, user, settings):
    from django.conf import settings as s

    for i in range(30):
        make_link(user, code=f"pg{i:03d}")
    body = auth_api.get("/api/v1/links/").data
    assert body["count"] == 30
    assert len(body["results"]) == s.REST_FRAMEWORK["PAGE_SIZE"]
