from django.urls import path

from analytics import views as analytics_views
from . import views

urlpatterns = [
    path("", views.landing, name="landing"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/links/table/", views.link_table, name="link_table"),
    path("dashboard/links/new/", views.link_create, name="link_create"),
    path("dashboard/links/<str:code>/edit/", views.link_edit, name="link_edit"),
    path("dashboard/links/<str:code>/toggle/", views.link_toggle, name="link_toggle"),
    path("dashboard/links/<str:code>/delete/", views.link_delete, name="link_delete"),
    path("dashboard/links/<str:code>/analytics/", analytics_views.link_analytics, name="link_analytics"),
    # This must stay last: it is the catch-all public redirect route.
    path("<str:code>", views.redirect_view, name="redirect"),
]
