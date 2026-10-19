"""TOTP two-factor authentication state and recovery codes."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models


class UserSettings(models.Model):
    """Per-user notification preferences."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_settings"
    )
    weekly_digest = models.BooleanField(
        default=False,
        help_text="Email a weekly summary of link activity every Monday.",
    )

    class Meta:
        verbose_name_plural = "user settings"

    def __str__(self):
        return f"settings for {self.user}"
from django.utils import timezone

User = get_user_model()


class TwoFactor(models.Model):
    """OneToOne extension to the user carrying the TOTP secret.

    The secret is stored as configured (plain, exactly like DRF API keys and
    the reference implementation of most Django 2FA plugins); DB-level
    compromise is out of scope for this threat model and is documented in
    the README.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="two_factor")
    secret = models.CharField(max_length=32)
    enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"2FA for {self.user.username}"


class TwoFactorRecoveryCode(models.Model):
    """Single-use fallback codes for when the authenticator is unavailable.

    Only a SHA-256 hash is stored; codes are displayed once at enrolment,
    exactly like API keys.
    """

    two_factor = models.ForeignKey(
        TwoFactor, on_delete=models.CASCADE, related_name="recovery_codes"
    )
    code_hash = models.CharField(max_length=64)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "two factor recovery codes"

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    def use(self):
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])
