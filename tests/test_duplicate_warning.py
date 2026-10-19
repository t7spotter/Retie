"""Duplicate destination warning on create/edit."""

import pytest

pytestmark = pytest.mark.django_db


def test_create_duplicate_shows_warning_and_does_not_save(client_user, user):
    user.links.create(code="first", destination="https://example.com/same")
    response = client_user.post(
        "/dashboard/links/new/",
        {"destination": "https://example.com/same", "is_active": "on"},
        follow=True,
    )
    body = response.content.decode()
    assert "/first" in body
    assert "already points" in body
    # Not created yet.
    assert not user.links.filter(destination="https://example.com/same", code="first").count() > 1


def test_confirm_create_saves_anyway(client_user, user):
    user.links.create(code="first", destination="https://example.com/same")
    response = client_user.post(
        "/dashboard/links/new/",
        {"destination": "https://example.com/same", "is_active": "on", "confirm_duplicate": "on"},
    )
    assert response.status_code == 302
    links = user.links.filter(destination="https://example.com/same")
    assert links.count() == 2


def test_trashed_duplicate_never_warns(client_user, user):
    from django.utils import timezone

    user.links.create(code="oldie", destination="https://example.com/trashy", deleted_at=timezone.now())
    response = client_user.post(
        "/dashboard/links/new/",
        {"destination": "https://example.com/trashy", "is_active": "on"},
    )
    assert response.status_code == 302  # created without a warning


def test_edit_to_another_links_destination_warns_then_saves(client_user, user):
    user.links.create(code="other", destination="https://example.com/dup")
    link = user.links.create(code="victim", destination="https://example.com/own")

    response = client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {"destination": "https://example.com/dup", "code": "victim", "is_active": "on"},
    )
    body = response.content.decode()
    assert "/other" in body and "already points" in body
    link.refresh_from_db()
    assert link.destination == "https://example.com/own"  # unchanged until confirmed

    client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {
            "destination": "https://example.com/dup",
            "code": "victim",
            "is_active": "on",
            "confirm_duplicate": "on",
        },
    )
    link.refresh_from_db()
    assert link.destination == "https://example.com/dup"


def test_edit_own_link_same_destination_no_warning(client_user, user):
    link = user.links.create(code="selfy", destination="https://example.com/exact")
    response = client_user.post(
        f"/dashboard/links/{link.code}/edit/",
        {"destination": "https://example.com/exact", "code": "selfy", "is_active": "on"},
        follow=True,
    )
    assert "already points" not in response.content.decode()


def test_api_and_mcp_do_not_block_duplicates(client_user, user):
    user.links.create(code="one", destination="https://example.com/again")
    from rest_framework.authtoken.models import Token
    from rest_framework.test import APIClient

    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    response = api.post("/api/v1/links/", {"destination": "https://example.com/again"})
    assert response.status_code == 201
