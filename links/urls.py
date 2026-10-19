from django.urls import path, re_path

from analytics import views as analytics_views
from . import views

urlpatterns = [
    path("", views.landing, name="landing"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/links/table/", views.link_table, name="link_table"),
    path("dashboard/links/import/", views.link_import, name="link_import"),
    path("dashboard/links/export.csv", views.link_export, name="link_export"),
    path("dashboard/links/new/", views.link_create, name="link_create"),
    path("dashboard/links/<str:code>/edit/", views.link_edit, name="link_edit"),
    path("dashboard/links/<str:code>/toggle/", views.link_toggle, name="link_toggle"),
    path("dashboard/links/<str:code>/delete/", views.link_delete, name="link_delete"),
    path("dashboard/links/<str:code>/restore/", views.link_restore, name="link_restore"),
    path("dashboard/links/<str:code>/purge/", views.link_purge, name="link_purge"),
    path("dashboard/links/<str:code>/analytics/", analytics_views.link_analytics, name="link_analytics"),
    # QR for a short code. Must stay above the catch-all redirect route.
    re_path(r"^(?P<code>[a-z0-9][a-z0-9-]{1,30})\.qr\.svg$", views.link_qr, name="link_qr"),
    # This must stay last: it is the catch-all public redirect route.
    path("<str:code>", views.redirect_view, name="redirect"),
]
