"""
Django settings for the self-hosted URL shortener.

All deployment-specific configuration comes from environment variables so an
operator can run this on their own domain without touching the source code.
For local development, values fall back to safe defaults.
"""

from pathlib import Path

import environ

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Everything here can be set in a .env file (read first) or the process
# environment, so operators only configure what differs from these defaults.
environ.Env.read_env(BASE_DIR / ".env")
env = environ.Env()

DEBUG = env.bool("DEBUG", default=False)
SECRET_KEY = env.str(
    "SECRET_KEY", default="dev-only-insecure-secret-key-change-me"
)

# The canonical public URL of this installation. Used to build short links that
# appear in the UI and API responses. Never trust the Host header for this.
SITE_URL = env.str("SITE_URL", default="http://localhost:8000").rstrip("/")
if not SITE_URL.lower().startswith(("http://", "https://")):
    raise ImproperlyConfigured(
        f"SITE_URL must include the scheme — got {SITE_URL!r}. Set the full "
        "public origin, e.g. 'http://127.0.0.1:8000' or 'https://links.example.com'."
    )

# Destinations may never point at these hosts (or their subdomains). Use it to
# keep your shortener from laundering phishing/malware domains.
# Example: BLOCKED_DESTINATIONS=evil.example,bit.ly,banned.org
BLOCKED_DESTINATIONS = [
    e.lower() for e in env.list("BLOCKED_DESTINATIONS", default=[]) if e
]

# Opt-in link health checker (SSRF-splash guard: checking user-provided URLs
# from a server can be intentional exfiltration path; leave off unless you
# want it). When true, `manage.py check_links` verifies each destination and
# adds ok/error badges in the dashboard.
LINK_HEALTH_ENABLED = env.bool("LINK_HEALTH_ENABLED", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "[::1]"])
CSRF_TRUSTED_ORIGINS = [
    o for o in env.list("CSRF_TRUSTED_ORIGINS", default=[]) if o
]

# Show the marketing page at "/"? Set false for an app-only install: then "/"
# is a doorway that redirects signed-in users to the dashboard and everyone
# else to login, and nothing about the site leaks otherwise.
LANDING_ENABLED = env.bool("LANDING_ENABLED", default=True)

# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Third-party
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    "apiwarden",
    # Project apps
    "accounts",
    "links",
    "analytics",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "links.context_processors.site_url",
                "links.context_processors.site_flags",
            ],
            "debug": DEBUG,
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database

DATABASES = {
    "default": env.db(
        "DATABASE_URL", default="postgres://postgres:postgres@localhost:5432/shortener"
    ),
}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=0)

# Authentication

AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "landing"

# Password validation (Django's standard validators)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# DRF

REST_FRAMEWORK = {
    # Session auth for the browser keeps the API consistent with the website;
    # MultiSchemeTokenAuthentication allows operators/scripts to call the API
    # with an API key under both "Authorization: Bearer <key>" (what the docs
    # portal and generic clients send) and "Authorization: Token <key>"
    # (DRF convention). Keys come from POST /api/v1/token/ or the profile page.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "links.authentication.MultiSchemeTokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_RATES": {
        "link_create": "120/hour",
        "token_auth": "10/hour",
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Retie API",
    "DESCRIPTION": "REST API for this self-hosted URL shortener. All endpoints require authentication.",
    "VERSION": "1.0.0",
    # The documentation endpoints are meta, not API surface; keep them out of
    # the generated contract.
    "PREPROCESSING_HOOKS": ["config.settings.drop_meta_endpoints"],
    # Auth is a session cookie for the browser or an API key ("Token" /
    # "Bearer") obtained from POST /api/v1/token/ on the web profile page.
    "SECURITY": [{"sessionAuth": []}, {"apiKeyAuth": []}],
    "SECURITY_SCHEMES": {
        "sessionAuth": {
            "type": "apiKey",
            "in": "cookie",
            "name": "sessionid",
            "description": "Session-cookie auth used by the website and HTMX.",
        },
        "apiKeyAuth": {
            "type": "http",
            "scheme": "bearer",
            "description": (
                "API key. Send `Authorization: Bearer <key>` (or "
                "`Authorization: Token <key>`). Create a key with "
                "`POST /api/v1/token/` using your username and password, "
                "or from your profile page."
            ),
        },
    },
}


def drop_meta_endpoints(endpoints):
    import re

    return [
        endpoint
        for endpoint in endpoints
        if not re.match(r"^/api/(docs|schema)", endpoint[0])
    ]

# API documentation portal (apiwarden). The OpenAPI spec itself is generated
# by drf-spectacular (see SPECTACULAR_SETTINGS); regenerate the served copy
# with:
#   python manage.py spectacular --file api-docs/retie/openapi.yaml
# apiwarden serves that file as a live portal at /api/docs/. Set
# APIWARDEN_TOKEN to require a shared token on the portal; by default it is
# public, like the rest of the docs.
token = env.str("APIWARDEN_TOKEN", default="")  # omit entirely = public portal
APIWARDEN = {
    "root": BASE_DIR / "api-docs",
    "title": "Retie API",
    "servers": [SITE_URL],
    "watch": DEBUG,
}
if token:
    APIWARDEN["token"] = token

# Internationalization

LANGUAGE_CODE = "en-us"
TIME_ZONE = env.str("TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True

# Email (used by password reset). Defaults to the console backend for local
# development; operators set these to their SMTP credentials in production.

EMAIL_BACKEND = env.str(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = env.str("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env.str("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env.str("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL", default="noreply@example.com")

# Static files

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# WhiteNoise serves static files (incl. gzip) from the app process itself,
# so a single container works behind any reverse proxy. Hashed filenames are
# not used: the small asset set is cache-busted by reverse-proxy config.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

# Auth-related redirects must be absolute URLs; use the canonical site URL so
# password-reset emails contain correct links behind a proxy.

# Security headers / HTTPS
# These settings are safe to enable unconditionally: they cost nothing when the
# site is served over plain HTTP locally, and protect production.
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# SECURE_PROXY_SSL_HEADER must only be set when the proxy is *trusted* (e.g.
# a reverse proxy you operate, which always sets X-Forwarded-Proto). Provide
# both parts comma-separated, e.g. SECURE_PROXY_SSL_HEADER=HTTP_X_FORWARDED_PROTO,https
SECURE_PROXY_SSL_HEADER = None
_proxy_spec = env.str("SECURE_PROXY_SSL_HEADER", default="")
if _proxy_spec:
    _header, _, _value = _proxy_spec.partition(",")
    SECURE_PROXY_SSL_HEADER = (_header.strip(), _value.strip())

SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=False)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=False)

X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# Feature switches

# Operators can disable open registration entirely (e.g. single-user installs).
REGISTRATION_ENABLED = env.bool("REGISTRATION_ENABLED", default=True)

# Default primary key field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
