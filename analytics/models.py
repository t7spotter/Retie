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
    referrer = models.URLField(max_length=2048, blank=True, default="")
    device = models.CharField(
        max_length=16,
        choices=DEVICE_CHOICES,
        default=DEVICE_OTHER,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["link", "-created_at"]),
        ]
        verbose_name = "click event"
        verbose_name_plural = "click events"

    def __str__(self) -> str:
        return f"{self.link.code} click at {self.created_at:%Y-%m-%d %H:%M}"
