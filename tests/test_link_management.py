"""Website link management: create, edit, toggle, delete, ownership, search."""

from django.urls import reverse
from django.utils import timezone

import pytest

from links.models import Link
from tests.conftest import make_link

pytestmark = pytest.mark.django_db


def test_dashboard_shows_and_keeps_links_private(client_user, user):
    make_link(user, code="own1")
    response = client_user.get(reverse("dashboard"))
    assert response.status_code == 200
    assert b"own1" in response.content


def test_create_link_via_dashboard(client_user, user):
    response = client_user.post(
        reverse("link_create"),
        {"destination": "https://example.com/x", "is_active": "on"},
    )
    assert response.status_code == 302
    link = Link.objects.get(user=user)
    assert link.destination == "https://example.com/x"
    assert len(link.code) == 6


def test_create_link_with_custom_alias(client_user, user):
    response = client_user.post(
        reverse("link_create"),
        {
            "destination": "https://example.com/x",
            "code": "board-meeting",
            "is_active": "on",
        },
    )
    assert Link.objects.get(user=user).code == "board-meeting"


def test_create_link_htmx_returns_form_fragment(client_user, user):
    response = client_user.post(
        reverse("link_create"),
        {"destination": "https://example.com/x", "is_active": "on"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    # The fragment tells htmx to refresh the links table.
    assert response["HX-Trigger"] == "links-changed"
    assert Link.objects.filter(user=user).count() == 1


def test_create_link_invalid_shows_errors_no_hx(client_user, user):
    response = client_user.post(
        reverse("link_create"), {"destination": "ftp://x", "is_active": "on"}
    )
    assert response.status_code == 200
    assert "destination" in response.context["form"].errors
    assert Link.objects.count() == 0


def test_create_link_invalid_htmx_form(client_user, user):
    response = client_user.post(
        reverse("link_create"), {"destination": "javascript:x"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert b"Enter a valid" in response.content
    assert Link.objects.count() == 0


def test_edit_link(client_user, user):
    link = make_link(user, code="edit1")
    response = client_user.post(
        reverse("link_edit", kwargs={"code": "edit1"}),
        {
            "destination": "https://example.com/changed",
            "title": "New title",
            "code": "edit1",
            "is_active": "on",
        },
    )
    assert response.status_code == 302
    link.refresh_from_db()
    assert link.destination == "https://example.com/changed"
    assert link.title == "New title"


def test_edit_rejects_invalid_destination(client_user, user):
    make_link(user, code="edit2")
    response = client_user.post(
        reverse("link_edit", kwargs={"code": "edit2"}),
        {"destination": "nope", "code": "edit2", "is_active": "on"},
    )
    assert "destination" in response.context["form"].errors


def test_toggle_link(client_user, user):
    link = make_link(user, code="tog1")
    response = client_user.post(reverse("link_toggle", kwargs={"code": "tog1"}))
    assert response.status_code == 302
    link.refresh_from_db()
    assert link.is_active is False

    # HTMX variant returns the refreshed row.
    response = client_user.post(
        reverse("link_toggle", kwargs={"code": "tog1"}),
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert b"tog1" in response.content


def test_delete_link(client_user, user):
    link = make_link(user, code="del1")
    response = client_user.post(reverse("link_delete", kwargs={"code": "del1"}))
    assert response.status_code == 302
    assert Link.objects.count() == 0


# ---- Ownership ------------------------------------------------------------


def test_cannot_edit_anothers_link(client_user, other_user):
    make_link(other_user, code="bobs1", destination="https://bob.example.com")
    response = client_user.get(reverse("link_edit", kwargs={"code": "bobs1"}))
    assert response.status_code == 404


def test_cannot_toggle_or_delete_anothers_link(client_user, other_user):
    make_link(other_user, code="bobs2")
    assert client_user.post(reverse("link_toggle", kwargs={"code": "bobs2"})).status_code == 404
    assert Link.objects.filter(code="bobs2").exists()
    assert client_user.post(reverse("link_delete", kwargs={"code": "bobs2"})).status_code == 404
    assert Link.objects.filter(code="bobs2").exists()


def test_another_users_links_are_invisible_in_own_dashboard(client_user, other_user):
    make_link(other_user, code="bobs3", title="Bob's secret")
    response = client_user.get(reverse("dashboard"))
    assert b"bobs3" not in response.content


# ---- Search / filter ------------------------------------------------------


def test_search_filters_links(client_user, user):
    make_link(user, code="findme", title="quarterly report")
    make_link(user, code="other2", title="unrelated")
    response = client_user.get(reverse("link_table"), {"q": "findme"})
    assert b"findme" in response.content
    assert b"other2" not in response.content


def test_status_filter(client_user, user):
    make_link(user, code="live1")
    make_link(user, code="dead1", is_active=False)
    response = client_user.get(reverse("link_table"), {"status": "active"})
    assert b"live1" in response.content
    assert b"dead1" not in response.content


def test_pagination(client_user, user):
    for i in range(12):
        make_link(user, code=f"page{i:03d}")
    response = client_user.get(reverse("link_table"), {"page": 2})
    assert response.status_code == 200
    # Order is newest first, so page 2 holds the oldest two links.
    assert b"page000" in response.content


def test_users_cannot_paginate_into_others_links(client_user, other_user):
    for i in range(15):
        make_link(other_user, code=f"bob{i:03d}")
    response = client_user.get(reverse("link_table"), {"page": 2})
    assert b"bob0" not in response.content
