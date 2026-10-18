"""Context shared by every template."""

from django.conf import settings


def site_url(request):
    return {"SITE_URL": settings.SITE_URL}


def site_flags(request):
    return {"LANDING_ENABLED": settings.LANDING_ENABLED}
