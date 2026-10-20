from django.db import models

from links.models import Link


class ClickEvent(models.Model):
    """One recorded redirect through a link.

    Deliberately coarse: no IP addresses are stored. The referrer is whatever
    the browser sent, and devices are bucketed from the user agent string.
    """

    DEVICE_DESKTOP = "desktop"
    DEVICE_MOBILE = "mobile"
    DEVICE_TABLET = "tablet"
    DEVICE_OTHER = "other"

    DEVICE_CHOICES = [
        (DEVICE_DESKTOP, "Desktop"),
        (DEVICE_MOBILE, "Mobile"),
        (DEVICE_TABLET, "Tablet"),
        (DEVICE_OTHER, "Other"),
    ]

    link = models.ForeignKey(
        Link,
        on_delete=models.CASCADE,
        related_name="clicks",
        # CASCADE means deleting a link removes its click history too, so
        # analytics never outlive the links they describe.
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    # A/B variant actually served on this click ("a" = the main
    # destination, always recorded even when the link has no test).
    variant = models.CharField(max_length=1, choices=[("a", "A"), ("b", "B")], default="a")
    referrer = models.URLField(max_length=2048, blank=True, default="")
    device = models.CharField(
        max_length=16,
        choices=DEVICE_CHOICES,
        default=DEVICE_OTHER,
    )
    # Coarse two-letter ISO country resolved from the visitor IP *at click
    # time*; the IP itself is never stored. Blank when geo data is not
    # configured or the address is private/unknown.
    country = models.CharField(max_length=2, blank=True, default="")
    # Coarse bot flag from the user-agent at click time. Excluded from
    # analytics by default (toggle `?bots=1` includes them everywhere).
    is_bot = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["link", "-created_at"]),
        ]
        verbose_name = "click event"
        verbose_name_plural = "click events"

    def __str__(self) -> str:
        return f"{self.link.code} click at {self.created_at:%Y-%m-%d %H:%M}"
