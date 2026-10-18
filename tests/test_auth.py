"""Registration, login, logout, and page-level access control."""

from django.conf import settings
from django.urls import reverse

import pytest

pytestmark = pytest.mark.django_db


def test_registration_creates_user_and_logs_in(client):
    response = client.post(
        reverse("register"),
        {
            "username": "newuser",
            "email": "new@example.com",
            "password1": "correct-horse-42",
            "password2": "correct-horse-42",
        },
    )
    assert response.status_code == 302
    assert response.url == reverse("dashboard")
    assert response.wsgi_request.user.is_authenticated


def test_registration_rejects_mismatched_passwords(client):
    response = client.post(
        reverse("register"),
        {
            "username": "newuser",
            "email": "new@example.com",
            "password1": "correct-horse-42",
            "password2": "different",
        },
    )
    assert response.status_code == 200
    assert response.context["form"].errors


def test_registration_rejects_bad_email(client):
    response = client.post(
        reverse("register"),
        {
            "username": "newuser",
            "email": "not-an-email",
            "password1": "correct-horse-42",
            "password2": "correct-horse-42",
        },
    )
    assert response.context["form"].errors


def test_registration_disabled(settings, client, db):
    settings.REGISTRATION_ENABLED = False
    response = client.get(reverse("register"))
    assert response.status_code == 302
    assert response["Location"] == reverse("login")
    response = client.post(reverse("register"), {"username": "x"})
    assert response.status_code == 302


def test_login_and_logout(client, django_user_model):
    django_user_model.objects.create_user(
        username="carol", password="s3cure-pass"
    )
    response = client.post(
        reverse("login"),
        {"username": "carol", "password": "s3cure-pass"},
    )
    assert response.url == reverse("dashboard")

    client.post(reverse("logout"))
    response = client.get(reverse("dashboard"))
    assert response.status_code == 302


def test_login_wrong_password(client, django_user_model):
    django_user_model.objects.create_user(
        username="carol", password="s3cure-pass"
    )
    response = client.post(
        reverse("login"),
        {"username": "carol", "password": "wrong"},
    )
    assert response.status_code == 200
    assert response.context["form"].errors


@pytest.mark.parametrize(
    "url",
    ["dashboard", "profile", "password_change", "link_create"],
)
def test_private_pages_require_login(client, url):
    assert client.get(reverse(url)).status_code == 302


def test_login_rate_limit(monkeypatch, client, django_user_model):
    """A client IP that exceeds the login limit gets a 403 eventually."""
    from django.conf import settings as s
    from django.core.cache import cache

    django_user_model.objects.create_user(username="carol", password="s3cure-pass")
    seen = [client.post(reverse("login"), {"username": "carol", "password": "wrong"}).status_code
            for _ in range(40)]
    assert seen[-1] == 403
    cache.clear()
