"""The LANDING_ENABLED feature flag: whether "/" is a marketing page or an
app-only doorway (auth users -> dashboard, visitors -> login, never marketing).
"""

import pytest

pytestmark = pytest.mark.django_db


def test_landing_shown_to_visitors_by_default(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Get started" in response.content  # marketing CTA
    # Visitors can pick their theme before signing up.
    assert b"data-theme-toggle" in response.content


def test_landing_marks_signed_in_users_to_dashboard(client, user):
    client.login(username="alice", password="correct-horse-42")
    response = client.get("/")
    assert response.status_code == 302
    assert response.url == "/dashboard/"


def test_flag_off_visitor_gets_login_not_marketing(client, settings):
    settings.LANDING_ENABLED = False
    response = client.get("/")
    assert response.status_code == 302
    assert response.url == "/accounts/login/"


def test_flag_off_signed_in_user_gets_dashboard(client, user, settings):
    settings.LANDING_ENABLED = False
    client.login(username="alice", password="correct-horse-42")
    response = client.get("/")
    assert response.status_code == 302
    assert response.url == "/dashboard/"


def test_flag_off_landing_page_not_directly_renderable(client, settings):
    """Even going out of one's way, '/' never serves marketing content."""
    settings.LANDING_ENABLED = False
    response = client.get("/", follow=True)
    assert response.status_code == 200
    assert b"Get started" not in response.content


def test_flag_off_logout_redirects_to_login(client, user, settings):
    settings.LANDING_ENABLED = False
    client.login(username="alice", password="correct-horse-42")
    response = client.post("/accounts/logout/")
    assert response.status_code == 302
    assert response.url == "/accounts/login/"


def test_flag_on_logout_redirects_to_landing(client, user):
    client.login(username="alice", password="correct-horse-42")
    response = client.post("/accounts/logout/")
    assert response.status_code == 302
    assert response.url == "/"


def test_flag_off_login_page_logo_leaves_no_marketing_link(client, settings):
    settings.LANDING_ENABLED = False
    response = client.get("/accounts/login/")
    assert response.status_code == 200
    assert b'href="/"' not in response.content


def test_unknown_code_still_404s_with_flag_off(client, user, settings):
    """/code-style short URLs keep working; the flag only touches "/"."""
    from tests.conftest import make_link

    settings.LANDING_ENABLED = False
    make_link(user, code="stillok")
    assert client.get("/stillok").status_code == 302
    assert client.get("/nosuchcode").status_code == 404
