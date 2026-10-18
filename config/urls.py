"""Root URL configuration: website, API, admin, and redirect handling."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("config.api_urls")),
    path("", include("links.urls")),
    path("", include("accounts.urls")),
]
