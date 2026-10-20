"""Google sign-in: state binding, verified-email identity, 2FA precedence.

Authlib's network half (token exchange, JWKS lookup) is stubbed with a
fake `authorize_access_token` returning userinfo claims, the way real
validated ID tokens do. The flow itself (authorize_redirect writing
state + nonce into the session) runs for real; the CSRF/identity rules
under test here are exactly what the view checks before trusting it.
"""

import base64
import time
import urllib.parse

from django.conf import settings
from django.contrib.auth import get_user_model

import pytest

pytestmark = pytest.mark.django_db

STATE_KEY = "_state_google_{state}"


@pytest.fixture
def google_ready(monkeypatch, settings):
    """Configured settings + client instance with seeded metadata."""
    settings.GOOGLE_OAUTH_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
    settings.GOOGLE_OAUTH_CLIENT_SECRET = "test-secret"
    from accounts.oauth import google_client

    client = google_client()
    client.server_metadata.clear()
    client.server_metadata.update(
        {
            "issuer": "https://accounts.google.com",
            "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_endpoint": "https://oauth2.googleapis.com/token",
            "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
            "id_token_signing_alg_values_supported": ["RS256"],
            "_loaded_at": time.time(),
        }
    )
    return client


def _userinfo(email="federated@example.com", verified=True, name="Fed Erated"):
    return {"userinfo": {"email": email, "email_verified": verified, "name": name}}


def _stub_token(app, monkeypatch, userinfo):
    monkeypatch.setattr(app, "authorize_access_token", lambda request, **kw: dict(userinfo))


def _begin_flow(client):
    """Run step 1 for real (no network thanks to seeded metadata).

    Returns the state value authlib put into the session.
    """
    response = client.get("/accounts/login/google/")
    assert response.status_code == 302
    parse = urllib.parse.parse_qs(urllib.parse.urlparse(response["Location"]).query)
    return parse["state"][0]


def test_buttons_hidden_and_routes_inert_when_not_configured(client, settings):
    settings.GOOGLE_OAUTH_CLIENT_ID = ""
    settings.GOOGLE_OAUTH_CLIENT_SECRET = ""
    body = client.get("/accounts/login/").content.decode()
    assert "Continue with Google" not in body
    bounced = client.get("/accounts/login/google/", follow=True)
    assert bounced.status_code == 200


def test_start_redirects_to_google_with_session_state(client, google_ready):
    response = client.get("/accounts/login/google/")
    assert response.status_code == 302
    assert response["Location"].startswith("https://accounts.google.com/o/oauth2/v2/auth")
    state = _begin_flow(client)
    assert client.session.get(STATE_KEY.format(state=state))


def test_callback_without_state_is_rejected(client, google_ready, monkeypatch):
    _stub_token(google_ready, monkeypatch, _userinfo())
    response = client.get("/accounts/login/google/callback/?code=whatever")
    assert response.status_code == 302
    assert not get_user_model().objects.filter(email__iexact="federated@example.com").exists()


def test_callback_registers_new_verified_account(client, google_ready, monkeypatch):
    _stub_token(google_ready, monkeypatch, _userinfo())
    state = _begin_flow(client)
    response = client.get(f"/accounts/login/google/callback/?state={state}&code=c")
    assert response.status_code == 302

    user = get_user_model().objects.get(email__iexact="federated@example.com")
    assert not user.has_usable_password()
    assert user.first_name == "Fed" and user.last_name == "Erated"
    assert user.username.startswith("federated")
    assert response.wsgi_request.user.is_authenticated  # no 2FA → logged in
    # State is consumed: a replay of the same callback bounces.
    assert not client.session.get(STATE_KEY.format(state=state))


def test_callback_username_suffix_on_collision(client, google_ready, monkeypatch, user):
    # A password account already owns username "federated"; a Google signup
    # with a colliding local-part must not clobber or reuse it.
    get_user_model().objects.create_user(username="federated", password="x-passw0rd")
    _stub_token(google_ready, monkeypatch, _userinfo(name="Fed Erated"))
    state = _begin_flow(client)
    client.get(f"/accounts/login/google/callback/?state={state}&code=c")
    assert get_user_model().objects.filter(username="federated").count() == 1
    new = get_user_model().objects.get(email__iexact="federated@example.com")
    assert new.username == "federated-1"


def test_callback_logs_in_existing_matching_email(client, google_ready, monkeypatch, user):
    _stub_token(google_ready, monkeypatch, _userinfo(email="ALICE@example.com", name="Alice"))
    state = _begin_flow(client)
    response = client.get(f"/accounts/login/google/callback/?state={state}&code=c")
    assert response.status_code == 302
    assert response.wsgi_request.user == user  # case-insensitive email match
    assert get_user_model().objects.filter(username="alice").count() == 1  # no duplicate


def test_registration_disabled_blocks_unknown_email(client, google_ready, monkeypatch, settings):
    settings.REGISTRATION_ENABLED = False
    _stub_token(google_ready, monkeypatch, _userinfo())
    state = _begin_flow(client)
    response = client.get(f"/accounts/login/google/callback/?state={state}&code=c", follow=True)
    assert not response.wsgi_request.user.is_authenticated
    assert "sign-up is disabled" in response.content.decode().lower()


def test_unverified_email_never_signs_in(client, google_ready, monkeypatch):
    _stub_token(google_ready, monkeypatch, _userinfo(verified=False))
    state = _begin_flow(client)
    response = client.get(f"/accounts/login/google/callback/?state={state}&code=c", follow=True)
    # The flash is set on the redirect response; assert it in queue.
    from django.contrib.messages import get_messages
    shown = [str(m) for m in get_messages(response.wsgi_request)]
    assert not response.wsgi_request.user.is_authenticated  # never logged in
    assert any("isn't verified" in m for m in shown)


def test_two_factor_user_still_needs_second_factor(client, google_ready, monkeypatch, user):
    from accounts.models import TwoFactor

    TwoFactor.objects.create(user=user, secret=base64.b32encode(b"x" * 10).decode(), enabled=True)
    _stub_token(google_ready, monkeypatch, _userinfo(email="alice@example.com", name="Alice"))
    state = _begin_flow(client)
    response = client.get(f"/accounts/login/google/callback/?state={state}&code=c")
    assert response.status_code == 302
    assert response["Location"].endswith("/accounts/two-factor/")
    assert client.session["two_factor_uid"] == user.pk
    assert not response.wsgi_request.user.is_authenticated


def test_google_cancel_consent_redirects_to_login(client, google_ready):
    response = client.get("/accounts/login/google/callback/?state=missing&error=access_denied")
    assert response.status_code == 302
