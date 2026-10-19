"""Root URL configuration: website, API, admin, and redirect handling."""

from django.contrib import admin
from django.urls import include, path

from links import mcp

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("config.api_urls")),
    path("mcp/", mcp.mcp_endpoint),
    path("", include("links.urls")),
    path("", include("accounts.urls")),
]
