"""Configuration-dependent behaviour and request-host safety."""

import pytest

pytestmark = pytest.mark.django_db


def test_short_url_ignores_host_header(client_user, user):
    """Links are always built from SITE_URL; a spoofed Host must not leak."""
    from django.test.utils import override_settings

    from tests.conftest import make_link

    with override_settings(ALLOWED_HOSTS=["evil.example.net"]):
        make_link(user, code="host1")
        response = client_user.get("/dashboard/", HTTP_HOST="evil.example.net")
    assert response.status_code == 200
    assert b"evil.example.net/h" not in response.content


def test_spoofed_host_gets_blocked(settings):
    """ALLOWED_HOSTS is enforced by Django before any view logic."""
    from django.test import Client
    from django.test.utils import override_settings

    with override_settings(ALLOWED_HOSTS=["trusted.example.com"]):
        client = Client()
        response = client.get("/", HTTP_HOST="evil.example.net")
        assert response.status_code == 400
