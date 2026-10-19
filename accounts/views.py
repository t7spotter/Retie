"""Authentication pages: registration, login, logout, profile, passwords.

Django's built-in password change/reset class views are used as-is with
custom templates (wired in accounts/urls.py).
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

import io

import pyotp
import segno

from links.views import rate_limited

from .forms import RegisterForm

LOGIN_LIMIT = rate_limited("login", limit=25, window_seconds=300)
REGISTER_LIMIT = rate_limited("register", limit=10, window_seconds=3600)


@REGISTER_LIMIT
def register(request):
    if not settings.REGISTRATION_ENABLED:
        messages.error(request, "Registration is disabled on this installation.")
        return redirect("login")
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Sessions are the site-wide authentication mechanism; logging in
            # after registration matches Django's documented flow.
            auth_login(request, user)
            messages.success(request, "Welcome! Your account is ready.")
            return redirect("dashboard")
    else:
        form = RegisterForm()
    return render(request, "registration/register.html", {"form": form})


SESSION_2FA_KEY = "two_factor_uid"


@LOGIN_LIMIT
def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            two = getattr(user, "two_factor", None)
            if two is not None and two.enabled:
                # Step 1 of 2: hold the identity in the session, not a login.
                request.session[SESSION_2FA_KEY] = user.pk
                return redirect("two_factor")
            auth_login(request, user)
            return redirect(request.GET.get("next") or "dashboard")
    else:
        form = AuthenticationForm()
    return render(request, "registration/login.html", {"form": form})


def _pending_2fa_user(request):
    """The user awaiting their second factor — None if the flag is invalid."""
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    uid = request.session.get(SESSION_2FA_KEY)
    if not uid:
        return None
    try:
        user = user_model.objects.get(pk=uid)
    except user_model.DoesNotExist:
        return None
    two = getattr(user, "two_factor", None)
    if two is None or not two.enabled:
        return None  # 2FA was switched off mid-flow: fall through to login
    return user


@rate_limited("two_factor", limit=10, window_seconds=900,
              key=lambda r: f"{r.META.get('REMOTE_ADDR','')}-{r.session.get(SESSION_2FA_KEY,'')}")
def two_factor_view(request):
    """Step 2 of 2: a 6-digit TOTP code or a single-use recovery code."""
    user = _pending_2fa_user(request)
    if user is None:
        return redirect("login")

    error = None
    if request.method == "POST":
        code = (request.POST.get("code") or "").strip().replace(" ", "")
        recovery = (request.POST.get("recovery") or "").strip()
        if _verify_totp(user, code) or (recovery and _use_recovery(user, recovery)):
            request.session.pop(SESSION_2FA_KEY, None)
            auth_login(request, user)
            return redirect(request.GET.get("next") or "dashboard")
        error = "That code did not match. Codes refresh every 30 seconds."
    return render(request, "registration/two_factor.html", {"error": error})


def _verify_totp(user, code: str) -> bool:
    from .models import TwoFactor

    if not code or not code.isdigit() or len(code) != 6:
        return False
    try:
        two = user.two_factor
    except TwoFactor.DoesNotExist:
        return False
    return pyotp.TOTP(two.secret).verify(code, valid_window=1)


def _use_recovery(user, raw: str) -> bool:
    import hashlib

    from .models import TwoFactor

    digest = hashlib.sha256(raw.strip().lower().encode()).hexdigest()
    try:
        code = user.two_factor.recovery_codes.get(code_hash=digest, used_at__isnull=True)
    except TwoFactor.DoesNotExist:
        return False
    code.use()
    return True


def _otpauth_url(user, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(
        name=user.username,
        issuer_name="Retie",
    )


@login_required
def two_factor_setup(request):
    """Enable or disable two-factor authentication.

    Enable: generate a pending secret, show its QR + manual key, confirm
    with a live code (proves the device was actually scanned). Disable:
    confirm with the current code. Recovery codes are displayed once.
    """
    from .models import TwoFactor, TwoFactorRecoveryCode

    fresh_codes = None
    if request.method == "POST":
        action = request.POST.get("action")
        two = TwoFactor.objects.filter(user=request.user).first()

        if action == "enable":
            code = (request.POST.get("code") or "").strip().replace(" ", "")
            secret = two.secret if (two and not two.enabled) else pyotp.random_base32()
            two, _ = TwoFactor.objects.get_or_create(
                user=request.user, defaults={"secret": secret}
            )
            if two.enabled:
                messages.error(request, "Two-factor is already on.")
            elif pyotp.TOTP(two.secret).verify(code, valid_window=1):
                two.enabled = True
                two.save(update_fields=["enabled"])
                # 10 single-use recovery codes, hashed, shown exactly once.
                TwoFactorRecoveryCode.objects.filter(two_factor=two, used_at__isnull=True).delete()
                import hashlib as _hashlib
                import secrets as _secrets

                fresh_codes = [
                    _secrets.token_hex(2) + "-" + _secrets.token_hex(2)
                    for _ in range(10)
                ]
                for raw in fresh_codes:
                    TwoFactorRecoveryCode.objects.create(
                        two_factor=two,
                        code_hash=_hashlib.sha256(raw.lower().encode()).hexdigest(),
                    )
                messages.success(request, "Two-factor enabled. Store the recovery codes below now.")
            else:
                messages.error(request, "That code did not match — scan the QR and try again.")

        elif action == "disable":
            code = (request.POST.get("code") or "").strip().replace(" ", "")
            if two and two.enabled and _verify_totp(request.user, code):
                TwoFactorRecoveryCode.objects.filter(two_factor=two).delete()
                two.delete()
                messages.info(request, "Two-factor disabled.")
            else:
                messages.error(request, "Wrong code — two-factor stays on.")

    # A fresh pending secret so the QR always matches the confirm attempt.
    from .models import TwoFactor as _TF

    two = _TF.objects.filter(user=request.user).first()
    if two is None:
        two = _TF.objects.create(user=request.user, secret=pyotp.random_base32())
    return render(
        request,
        "pages/two_factor_setup.html",
        {
            "two": two,
            "fresh_codes": fresh_codes,
        },
    )


@login_required
def two_factor_qr(request):
    """SVG QR for the authenticator app; requires the secret to be pending."""
    from .models import TwoFactor

    two = get_object_or_404(TwoFactor, user=request.user, enabled=False)
    buffer = io.BytesIO()
    segno.make(_otpauth_url(request.user, two.secret), error="m").save(
        buffer, kind="svg", scale=6, border=2, dark="#0e353c", light="#ffffff"
    )
    response = HttpResponse(buffer.getvalue(), content_type="image/svg+xml")
    response["Cache-Control"] = "no-store"
    return response


@require_POST
def logout_view(request):
    """POST-only logout so link prefetching cannot log people out."""
    auth_logout(request)
    return redirect("landing" if settings.LANDING_ENABLED else "login")


@login_required
def profile(request):
    from links.models import Link

    link_count = Link.objects.filter(user=request.user).count()

    # API-key management: the key is shown once at creation time, then only
    # its existence ("has an API key") — standard practice so a leaked copy
    # of the page can't expose the credential again later.
    new_token = None
    two_enabled = bool(getattr(request.user, "two_factor", None)) and request.user.two_factor.enabled

    if request.method == "POST":
        action = request.POST.get("action")
        from rest_framework.authtoken.models import Token

        if action == "create_api_key":
            token, _ = Token.objects.get_or_create(user=request.user)
            new_token = token.key
            messages.success(request, "API key created. It is shown below once only.")
        elif action == "revoke_api_key":
            Token.objects.filter(user=request.user).delete()
            messages.info(request, "API key revoked. Any scripts using it stopped working.")

    return render(
        request,
        "pages/profile.html",
        {"link_count": link_count, "new_token": new_token},
    )
