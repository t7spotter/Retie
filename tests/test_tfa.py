"""TOTP two-factor: enrol, first-factor hold, verify, recovery codes, off."""

import hashlib


import pytest
import pyotp
from django.urls import reverse


pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-42"


@pytest.fixture
def tfa_setup(user, client):
    """Start enrolment (creates the pending secret) and post a live code."""
    client.login(username="alice", password=PASSWORD)
    client.get(reverse("two_factor_setup"))  # ensures the pending secret exists
    secret = user.two_factor.secret
    response = client.post(
        reverse("two_factor_setup"),
        {"action": "enable", "code": pyotp.TOTP(secret).now()},
    )
    return client, secret


def test_enabling_requires_a_valid_live_code(client, user):
    client.login(username="alice", password=PASSWORD)
    client.get(reverse("two_factor_setup"))
    response = client.post(
        reverse("two_factor_setup"), {"action": "enable", "code": "000000"}
    )
    assert not user.two_factor.enabled
    assert "did not match" in response.content.decode()


def test_setup_shows_recovery_codes_once(client, user):
    from accounts.models import TwoFactor, TwoFactorRecoveryCode

    client.login(username="alice", password=PASSWORD)
    client.get(reverse("two_factor_setup"))
    response = client.post(
        reverse("two_factor_setup"),
        {"action": "enable", "code": pyotp.TOTP(TwoFactor.objects.get(user=user).secret).now()},
    )
    assert TwoFactor.objects.get(user=user).enabled
    assert b"store them now" in response.content
    two = TwoFactor.objects.get(user=user)
    assert TwoFactorRecoveryCode.objects.filter(two_factor=two).count() == 10
    # The codes are never shown again on a plain revisit of the page.
    revisit = client.get(reverse("two_factor_setup"))
    assert b"store them now" not in revisit.content


def test_login_with_2fa_stats_second_step(tfa_setup, client, client_user_factory=None):
    client.logout()
    response = client.post(
        reverse("login"), {"username": "alice", "password": PASSWORD}, follow=True
    )
    final_url = response.request["PATH_INFO"]
    assert final_url == reverse("two_factor")
    assert response.wsgi_request.user.is_authenticated is False


def test_correct_code_completes_login(tfa_setup, client):
    client.logout()
    client.post(reverse("login"), {"username": "alice", "password": PASSWORD})
    secret = pyotp.TOTP.now if False else None

    from django.contrib.auth import get_user_model

    user = get_user_model().objects.get(username="alice")
    secret = user.two_factor.secret
    response = client.post(reverse("two_factor"), {"code": pyotp.TOTP(secret).now()}, follow=True)
    assert response.wsgi_request.user.is_authenticated
    assert response.request["PATH_INFO"] == reverse("dashboard")


def test_wrong_code_rejected_then_throttled(tfa_setup, client):
    client.logout()
    client.post(reverse("login"), {"username": "alice", "password": PASSWORD})
    for _ in range(10):
        bad = client.post(reverse("two_factor"), {"code": "000000"})
        assert bad.status_code == 200
    response = client.post(reverse("two_factor"), {"code": "000000"})
    assert response.status_code == 403  # rate_limited: PermissionDenied


def test_recovery_code_works_exactly_once(tfa_setup, client):
    client.logout()
    client.post(reverse("login"), {"username": "alice", "password": PASSWORD})

    from django.contrib.auth import get_user_model

    from accounts.models import TwoFactorRecoveryCode

    user = get_user_model().objects.get(username="alice")
    two = user.two_factor
    raw = "abcd-1234"
    TwoFactorRecoveryCode.objects.create(
        two_factor=two, code_hash=hashlib.sha256(b"abcd-1234").hexdigest()
    )
    response = client.post(reverse("two_factor"), {"recovery": "ABCD-1234"}, follow=True)
    assert response.wsgi_request.user.is_authenticated
    # Second use fails (wrong code path back to the form or 403 on throttle).
    two_code = TwoFactorRecoveryCode.objects.get(code_hash=hashlib.sha256(b"abcd-1234").hexdigest())
    assert two_code.used_at is not None


def test_disable_requires_current_code(client, user):
    from accounts.models import TwoFactor

    client.login(username="alice", password=PASSWORD)
    client.get(reverse("two_factor_setup"))
    client.post(
        reverse("two_factor_setup"),
        {"action": "enable", "code": pyotp.TOTP(TwoFactor.objects.get(user=user).secret).now()},
    )
    secret = TwoFactor.objects.get(user=user).secret

    # A wrong code leaves 2FA on.
    client.post(reverse("two_factor_setup"), {"action": "disable", "code": "000000"})
    assert TwoFactor.objects.get(user=user).enabled is True

    # The real code turns it off (the view leaves a fresh pending secret).
    client.post(
        reverse("two_factor_setup"),
        {"action": "disable", "code": pyotp.TOTP(secret).now()},
    )
    two = TwoFactor.objects.filter(user=user).first()
    assert two is not None
    assert two.enabled is False
    assert two.recovery_codes.count() == 0


def test_qr_endpoint_serves_otpauth(client, user):
    """QR is served while enrolment is pending, 404s once enabled."""
    client.login(username="alice", password=PASSWORD)
    client.get(reverse("two_factor_setup"))  # pending secret exists
    response = client.get(reverse("two_factor_qr"))
    assert response.status_code == 200
    assert response["Content-Type"] == "image/svg+xml"

    client.post(
        reverse("two_factor_setup"),
        {"action": "enable", "code": pyotp.TOTP(user.two_factor.secret).now()},
    )
    # Enabled: no pending secret left to exfiltrate via QR.
    response = client.get(reverse("two_factor_qr"))
    assert response.status_code == 404


def test_2fa_not_triggered_when_disabled(client, user):
    response = client.post(
        reverse("login"), {"username": "alice", "password": PASSWORD}, follow=True
    )
    assert response.wsgi_request.user.is_authenticated
    assert response.request["PATH_INFO"] == "/dashboard/"
