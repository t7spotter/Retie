"""Shared test fixtures: users with links, and helpers to make links fast."""

import pytest
from django.contrib.auth import get_user_model

from links.models import Link

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(
        username="alice",
        email="alice@example.com",
        password="correct-horse-42",
    )


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user(
        username="bob",
        email="bob@example.com",
        password="sticky-notes-99",
    )


@pytest.fixture
def client_user(client, user):
    """An authenticated test client for `user`."""
    client.login(username="alice", password="correct-horse-42")
    return client


def make_link(user, code="abc123", destination="https://example.com/page",
              is_active=True, expires_at=None, **kwargs):
    return Link.objects.create(
        user=user,
        code=code,
        destination=destination,
        is_active=is_active,
        expires_at=expires_at,
        **kwargs,
    )
