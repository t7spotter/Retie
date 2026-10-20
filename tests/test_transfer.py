"""Ownership transfer: hand a link to another user, with confirmation."""

import pytest
from django.contrib.auth import get_user_model
from tests.conftest import make_link

from links.models import Link, Tag

pytestmark = pytest.mark.django_db

User = get_user_model()


def _as_other(other_user):
    """A second browser session logged in as the recipient."""
    from django.test import Client

    client = Client()
    client.login(username=other_user.username, password="sticky-notes-99")
    return client


def test_transfer_flow_with_confirmation(client_user, user, other_user):
    link = make_link(user, code="giveit")
    # Step 1: confirmation page.
    response = client_user.post(
        f"/dashboard/links/{link.code}/transfer/", {"new_owner": "bob"}
    )
    body = response.content.decode()
    assert "Transfer ownership" in body
    assert "bob" in body
    link.refresh_from_db()
    assert link.user == user  # not moved yet

    # Step 2: confirm.
    response = client_user.post(
        f"/dashboard/links/{link.code}/transfer/",
        {"new_owner": str(other_user.pk), "confirm_transfer": "on"},
    )
    assert response.status_code == 302
    link.refresh_from_db()
    assert link.user == other_user
    # Bob sees it on his dashboard; the old owner does not.
    assert "giveit" in _as_other(other_user).get("/dashboard/").content.decode()


def test_unknown_recipient_shows_error(client_user, user, other_user):
    link = make_link(user, code="nobody")
    response = client_user.post(
        f"/dashboard/links/{link.code}/transfer/", {"new_owner": "ghost"}
    )
    body = response.content.decode()
    assert "No user found" in body
    link.refresh_from_db()
    assert link.user == user


def test_transfer_to_self_rejected(client_user, user):
    link = make_link(user, code="mine2")
    response = client_user.post(
        f"/dashboard/links/{link.code}/transfer/", {"new_owner": user.username}
    )
    assert "You already own this link" in response.content.decode()


def test_only_owner_can_transfer(client_user, user, other_user):
    make_link(user, code="hoard")
    response = _as_other(other_user).post(
        "/dashboard/links/hoard/transfer/", {"new_owner": "bob"}
    )
    assert response.status_code == 404
    assert Link.objects.get(code="hoard").user == user


def test_transfer_by_email_and_tags_rehomed(client_user, user, other_user):
    link = make_link(user, code="tagged")
    tag, _ = Tag.objects.get_or_create(user=user, name="marketing")
    link.tags.add(tag)
    response = client_user.post(
        f"/dashboard/links/{link.code}/transfer/",
        {"new_owner": other_user.email, "confirm_transfer": "on"},
    )
    assert response.status_code == 302
    link.refresh_from_db()
    assert link.user == other_user
    assert list(link.tags.values_list("name", flat=True)) == ["marketing"]
    assert list(link.tags.values_list("user", flat=True)) == [other_user.pk]
