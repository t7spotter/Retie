from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("accounts/register/", views.register, name="register"),
    path("accounts/login/", views.login_view, name="login"),
    path("accounts/two-factor/", views.two_factor_view, name="two_factor"),
    path("accounts/two-factor/setup/", views.two_factor_setup, name="two_factor_setup"),
    path("accounts/two-factor/qr.svg", views.two_factor_qr, name="two_factor_qr"),
    path("accounts/logout/", views.logout_view, name="logout"),
    path("accounts/profile/", views.profile, name="profile"),
    path("accounts/export/", views.export_data, name="export_data"),
    path("accounts/delete/", views.delete_account, name="delete_account"),
    path(
        "password/change/",
        auth_views.PasswordChangeView.as_view(template_name="registration/password_change.html"),
        name="password_change",
    ),
    path(
        "password/change/done/",
        auth_views.PasswordChangeDoneView.as_view(
            template_name="registration/password_change_done.html"
        ),
        name="password_change_done",
    ),
    path(
        "password/reset/",
        auth_views.PasswordResetView.as_view(template_name="registration/password_reset.html"),
        name="password_reset",
    ),
    path(
        "password/reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="registration/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    path(
        "password/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="registration/password_reset_confirm.html"
        ),
        name="password_reset_confirm",
    ),
    path(
        "password/reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="registration/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
]
