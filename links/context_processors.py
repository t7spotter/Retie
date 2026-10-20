"""Context shared by every template."""

from django.conf import settings


def site_url(request):
    return {"SITE_URL": settings.SITE_URL, "extra_domains": settings.EXTRA_DOMAINS}


def site_flags(request):
    return {"LANDING_ENABLED": settings.LANDING_ENABLED}
