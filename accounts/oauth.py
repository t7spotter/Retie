"""Google sign-in: the OpenID Connect client and account provisioning.

Authlib runs the standardized flow end to end: `authorize_redirect` stores
state **and nonce** in the session, the callback redeems the code and then
*validates the Google ID token cryptographically* — signature against
Google's published JWKS, issuer, our client_id as audience, and the
per-request nonce — before the userinfo claims are trusted. Nothing is
accepted on bare JSON assertion (no tokeninfo calls, no trust-on-first-use).

Identity rule: the email claim must carry `email_verified=True`. That is
what makes "sign in an existing account by email address" safe against
takeover — Google itself proved the address holder controls the account.
Known accounts are signed in (password or not); unknown ones are
provisioned with an unusable password, but only when
`REGISTRATION_ENABLED` allows signing up on this installation.

Configure with GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET; both
empty keeps every button and route inert (a friendly redirect to the
password form).
"""

import re

from authlib.integrations.django_client import OAuth

from django.conf import settings

# The redirect URIs whitelisted in the Google Cloud console are:
#   {SITE_URL}/accounts/login/google/callback/  (+ localhost variants)
# Authority/control of each endpoint comes from Google's own discovery
# document — pinned endpoints would let a config error silently swap to a
# look-alike host, discovery keeps it exact.
_GoogleOAuth = OAuth()

_built = False
_LO = "https://accounts.google.com/.well-known/openid-configuration"


def google_client():
    """The configured authlib app, registering it on first use."""
    global _built
    if not _built:
        _GoogleOAuth.register(
            "google",
            client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
            client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
            server_metadata_url=_LO,
            # Pinned protocol URLs keep the start step network-free in the
            # test suite (metadata is only used for ID-token validation).
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            access_token_url="https://oauth2.googleapis.com/token",
            client_kwargs={"scope": "openid email profile"},
        )
        _built = True
    return _GoogleOAuth.google


def oauth_available() -> bool:
    """Whether Google sign-in is configured for this installation."""
    return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)


def _base_username(email: str) -> str:
    """A readable username seed from the email's local-part."""
    return re.sub(r"[^a-z0-9_.-]", "", email.split("@")[0].lower())[:20] or "google-user"


def user_from_google_claims(claims):
    """Resolve (or provision) the account for verified Google userinfo.

    Returns the User, or `None` when the email is unverified (refuse:
    the caller shows a generic message) or registration is disabled for a
    brand-new address.
    """
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    email = (claims.get("email") or "").strip().lower()
    if not email or claims.get("email_verified") is not True:
        return None

    user = user_model.objects.filter(email__iexact=email).order_by("pk").first()
    if user is not None:
        return user
    if not settings.REGISTRATION_ENABLED:
        return None

    # Provision: username derived from the email local-part, guaranteed
    # unique with a numeric suffix; no password (Google only).
    username, suffix = _base_username(email), 0
    while user_model.objects.filter(username__iexact=username).exists():
        suffix += 1
        username = f"{_base_username(email)}-{suffix}"
    friendly = (claims.get("name") or "").strip().split()
    return user_model.objects.create_user(
        username=username,
        email=email,
        password=None,
        first_name=(friendly[0] if friendly else ""),
        last_name=(friendly[-1] if len(friendly) > 1 else ""),
    )
