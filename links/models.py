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


class Tag(models.Model):
    """A per-user label for grouping links ("projects", "marketing", ...)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tags")
    name = models.CharField(max_length=32)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="unique_tag_per_user")
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


def normalize_referrer_domains(raw: str) -> list:
    """Parse a comma/semicolon separated referrer allow-list string.

    Returns lowercase, scheme-stripped domain tokens (e.g.
    "news.example.com"). Hostnames only — no paths, no schemes; anything
    else raises ValidationError. At most 20 entries.
    """
    import re

    domains = []
    for part in (raw or "").replace(";", ",").split(","):
        token = part.strip().lower()
        if not token:
            continue
        # Strip a scheme and any path if someone pastes a full URL.
        if "://" in token:
            token = token.split("://", 1)[1]
        token = token.split("/", 1)[0]
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", token):
            raise ValidationError(
                f"Referrer domain {token!r} is not a valid public hostname."
            )
        if token not in domains:
            domains.append(token)
    if len(domains) > 20:
        raise ValidationError("At most 20 referrer domains per link.")
    return domains


def normalize_tag_names(raw: str) -> list:
    """Parse a comma/semicolon separated tags string user-typed.

    Returns unique, lowercase names; raises ValidationError on a malformed
    tag. At most 5 tags per link keep the UI honest.
    """
    import re

    names = []
    for part in (raw or "").replace(";", ",").split(","):
        name = part.strip().lower()
        if not name:
            continue
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", name):
            raise ValidationError(
                f"Tag {name!r}: use 1-32 characters of a-z, 0-9, hyphen or underscore."
            )
        if name not in names:
            names.append(name)
    if len(names) > 5:
        raise ValidationError("Use at most 5 tags per link.")
    return names


class ActiveManager(models.Manager):
    """Default manager that hides soft-deleted (trashed) links."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


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
    # 301 vs 302: analytics links must stay 302 (browsers cache 301s and
    # stop reporting clicks); a link whose migration is done may pin 301.
    permanent = models.BooleanField(
        default=False,
        help_text="Respond with a permanent 301 redirect (cachable by browsers).",
    )
    # Optional gate: short links only reach their destination after the
    # visitor answers the password once per browser session. Stored as
    # a Django password hash — never in the clear.
    password_hash = models.CharField(max_length=255, blank=True, default="")
    # Public shareable stats page at /stats/<code>; shows aggregate counts
    # only, never destinations, recent clicks, or per-click detail.
    public_stats = models.BooleanField(default=False)
    # Optional extra privacy: drop the country list from the public page.
    public_hide_countries = models.BooleanField(
        default=False,
        help_text="Hide the country breakdown on the public stats page.",
    )
    # Optional go-live moment: while in the future the link does not
    # redirect yet, regardless of is_active.
    goes_live_at = models.DateTimeField(null=True, blank=True)
    # Hotlink protection: when non-empty, redirects are served only when
    # the visitor's referrer host matches one of these domains (subdomains
    # included). Direct traffic (no referrer header) is always allowed —
    # typed URLs and QR codes never carry a referrer.
    allowed_referrers = models.TextField(blank=True, default="")
    tags = models.ManyToManyField(Tag, blank=True, related_name="links")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Trash/undo: a soft-deleted link keeps its code (so restoring a link
    # deleted by mistake works) but stops responding everywhere.
    deleted_at = models.DateTimeField(null=True, blank=True, default=None)

    objects = ActiveManager()  # hides trashed links from every normal lookup
    all_objects = models.Manager()  # includes trashed links (trash view, purge)

    # Link health checker (manage.py check_links, opt-in).
    health_status = models.CharField(
        max_length=10,
        choices=[("ok", "ok"), ("error", "error")],
        blank=True,
        default="",
        db_index=True,
    )
    health_detail = models.CharField(max_length=200, blank=True, default="")
    last_checked_at = models.DateTimeField(null=True, blank=True)
    # Private owners-only note (never rendered on any public page).
    notes = models.TextField(blank=True, default="")

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
    def is_scheduled(self) -> bool:
        """True when the link is waiting for its go-live moment."""
        if self.goes_live_at is None:
            return False
        return timezone.now() < self.goes_live_at

    @property
    def is_live(self) -> bool:
        """True when the link currently redirects."""
        return self.is_active and not self.is_expired and not self.is_scheduled

    @property
    def status(self) -> str:
        if not self.is_active:
            return "inactive"
        if self.is_expired:
            return "expired"
        if self.is_scheduled:
            return "scheduled"
        return "active"


def _host_is_blocked(host: str) -> bool:
    """True when the destination host matches the configured blocklist.

    Entries match the host itself and any subdomain, so "evil.example"
    also blocks "login.evil.example". Empty entries are ignored.
    """
    from django.conf import settings

    host = host.lower()
    for entry in settings.BLOCKED_DESTINATIONS:
        entry = entry.strip().lower().lstrip("*.")
        entry = entry.removeprefix(".")
        if entry and (host == entry or host.endswith("." + entry)):
            return True
    return False


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

    if _host_is_blocked(parsed.hostname or ""):
        raise ValidationError("That destination domain is not allowed on this server.")
    return url


def validate_alias(code: str) -> str:
    """Reject aliases that clash with application routes or are malformed."""
    import re

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,30}", code):
        raise ValidationError("Aliases are 2-31 characters of a-z, 0-9, or hyphen.")
    if code in RESERVED_ALIASES:
        raise ValidationError("That alias is reserved.")
    return code
