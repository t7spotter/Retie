from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.utils import timezone
from urllib.parse import urlsplit

User = get_user_model()

# Short codes use an unambiguous lowercase alphabet (no 0/O, 1/l/I) so a
# misread character is less likely to hit a different link.
CODE_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
CODE_LENGTH = 6

# Aliases users may not claim because public routes and API paths must win.
RESERVED_ALIASES = {
    "admin",
    "api",
    "static",
    "accounts",
    "login",
    "logout",
    "signup",
    "dashboard",
    "profile",
    "password",
    "reset",
    "about",
    "terms",
    "privacy",
    "docs",
    "schema",
    "media",
}


def generate_short_code(length: int = CODE_LENGTH) -> str:
    """Return a cryptographically secure short code."""
    import secrets

    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


class Link(models.Model):
    """A shortened URL owned by one user."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="links",
    )
    code = models.CharField(
        max_length=32,
        unique=True,
        blank=True,
        db_index=True,
        help_text="The short-code part of the public URL. Left empty, one is "
        "generated automatically.",
    )
    destination = models.URLField(max_length=2048)
    title = models.CharField(max_length=200, blank=True, default="")
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self) -> str:
        return self.code

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at <= timezone.now()

    @property
    def is_live(self) -> bool:
        """True when the link currently redirects."""
        return self.is_active and not self.is_expired

    @property
    def status(self) -> str:
        if not self.is_active:
            return "inactive"
        if self.is_expired:
            return "expired"
        return "active"


def validate_destination(url: str) -> str:
    """Return a normalised http(s) URL or raise ValidationError.

    We validate explicitly rather than relying on URLField's default behaviour
    so the rules are identical for forms, serializers, and tests.
    """
    # Forms coerce schemeless input ("example.com") to http://; we require an
    # explicit scheme so destinations are unambiguous.
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValidationError("Enter a valid http:// or https:// URL.")

    validator = URLValidator(schemes=["http", "https"])
    try:
        validator(url)
    except ValidationError:
        # A field-level validator must raise a plain message, not a
        # field-keyed dict; Django's form machinery adds the field itself.
        raise ValidationError("Enter a valid http:// or https:// URL.")
    return url


def validate_alias(code: str) -> str:
    """Reject aliases that clash with application routes or are malformed."""
    import re

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,30}", code):
        raise ValidationError("Aliases are 2-31 characters of a-z, 0-9, or hyphen.")
    if code in RESERVED_ALIASES:
        raise ValidationError("That alias is reserved.")
    return code
