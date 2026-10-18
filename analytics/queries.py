"""Click recording and aggregation helpers.

All queries are scoped to a user's own links; nothing here exposes data
across accounts.
"""

from django.db.models import Count, Max, Q
from django.db.models.functions import TruncDay
from django.utils import timezone

from .models import ClickEvent

# Keywords used to bucket the user agent. Intentionally simple: we only need
# a coarse category, never a browser/version fingerprint.
_TABLET_HINTS = ("ipad", "tablet", "kindle", "playbook", "silk")
_MOBILE_HINTS = ("iphone", "ipod", "android", "windows phone", "bada")


def device_category(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    if not ua:
        return ClickEvent.DEVICE_OTHER
    if any(hint in ua for hint in _TABLET_HINTS):
        return ClickEvent.DEVICE_TABLET
    if any(hint in ua for hint in _MOBILE_HINTS):
        return ClickEvent.DEVICE_MOBILE
    return ClickEvent.DEVICE_DESKTOP


def record_click(link, request) -> ClickEvent:
    """Persist one redirect event for `link`.

    The referrer is only stored when sent by the browser; the IP address is
    never persisted.
    """
    return ClickEvent.objects.create(
        link=link,
        referrer=request.META.get("HTTP_REFERER", "")[:2048],
        device=device_category(request.META.get("HTTP_USER_AGENT", "")),
    )


def is_active_q():
    """Q object matching links that currently redirect."""
    return Q(is_active=True) & (Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))


def user_totals(user) -> dict:
    """Headline numbers for the dashboard, across all owned links."""
    return user.links.aggregate(
        total_links=Count("id", distinct=True),
        active_links=Count("id", distinct=True, filter=is_active_q()),
        total_clicks=Count("clicks"),
        last_click=Max("clicks__created_at"),
    )


def clicks_over_time(user, days: int = 30, link=None) -> list:
    """Clicks bucketed per day for the last `days` days.

    Returns [{"date": "YYYY-MM-DD", "clicks": n}] with zero counts filled in
    so charts show a continuous axis.
    """
    start = timezone.now() - timezone.timedelta(days=days - 1)
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)

    clicks = ClickEvent.objects.filter(created_at__gte=start)
    if link is not None:
        clicks = clicks.filter(link=link)
    else:
        clicks = clicks.filter(link__user=user)

    per_day = (
        clicks.annotate(day=TruncDay("created_at")).values("day").annotate(count=Count("id"))
    )
    by_day = {row["day"].date(): row["count"] for row in per_day}

    series = []
    for offset in range(days):
        day = start.date() + timezone.timedelta(days=offset)
        series.append({"date": day.isoformat(), "clicks": by_day.get(day, 0)})
    return series


def referrer_breakdown(user, link=None, limit: int = 8) -> list:
    clicks = ClickEvent.objects.filter(link__user=user)
    if link is not None:
        clicks = clicks.filter(link=link)
    return list(
        clicks.exclude(referrer="")
        .values("referrer")
        .annotate(count=Count("id"))
        .order_by("-count")[:limit]
    )


def device_breakdown(user, link=None) -> dict:
    """Map of device category -> click count for convenience in templates."""
    clicks = ClickEvent.objects.filter(link__user=user)
    if link is not None:
        clicks = clicks.filter(link=link)
    rows = clicks.values("device").annotate(count=Count("id")).order_by("-count")
    return {row["device"]: row["count"] for row in rows}


def recent_clicks(user, limit: int = 10) -> list:
    """Last few clicks across the user's links, newest first."""
    return list(
        ClickEvent.objects.filter(link__user=user)
        .select_related("link")
        .order_by("-created_at")[:limit]
    )
