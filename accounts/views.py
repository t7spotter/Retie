"""Authentication pages: registration, login, logout, profile, passwords.

Django's built-in password change/reset class views are used as-is with
custom templates (wired in accounts/urls.py).
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

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


@LOGIN_LIMIT
def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            auth_login(request, form.get_user())
            return redirect(request.GET.get("next") or "dashboard")
    else:
        form = AuthenticationForm()
    return render(request, "registration/login.html", {"form": form})


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
