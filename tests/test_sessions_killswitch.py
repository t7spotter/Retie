"""Session list / kill switch on the profile page."""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-42"


@pytest.fixture
def two_clients(client, user):
    """Alice signed twice: the returned `client` + a fresh second client."""
    from django.test import Client

    client.login(username="alice", password=PASSWORD)
    other = Client()
    other.login(username="alice", password=PASSWORD)
    return other


def _page(client):
    return client.get(reverse("profile"))


def test_profile_lists_other_session(client, user, two_clients):
    response = _page(client)
    body = response.content.decode()
    assert "This device" in body
    assert "Other browser" in body


def test_profile_without_others_shows_clean_list(client, user):
    client.login(username="alice", password=PASSWORD)
    body = _page(client).content.decode()
    assert "No other sign-ins" in body
    assert "Other browser" not in body


def test_sign_out_elsewhere(client, user, two_clients):
    other = two_clients
    response = client.post(reverse("profile"), {"action": "sign_out_elsewhere"}, follow=True)
    # The current device stays signed in; the other browser is out.
    assert client.get(reverse("profile")).status_code == 200
    assert other.get(reverse("profile")).status_code == 302  # bounced to login
    assert "Signed out of 1 other session" in response.content.decode()


def test_sessions_of_other_users_never_listed(client, user, other_user, two_clients):
    """Bob's session never shows up in alice's list, whatever its state."""
    from django.test import Client

    _ = two_clients  # gives alice a second session ("Other browser" x1)
    bob = Client()
    bob.login(username="bob", password="sticky-notes-99")
    body = _page(client).content.decode()
    # Only alice's two sessions appear, regardless of bob's presence.
    assert body.count("Other browser") == 1
