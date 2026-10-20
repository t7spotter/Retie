"""Analytics pages. Click logging itself happens in links.views.redirect_view."""

import csv
import json

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from . import queries
from links.models import Link


@login_required
def link_analytics(request, code):
    """Per-link analytics: totals, 30-day chart, referrers, devices, activity.

    Known bot clicks are excluded by default; `?bots=1` includes them.
    """
    link = get_object_or_404(Link, code=code, user=request.user)
    include_bots = request.GET.get("bots") == "1"
    series = queries.clicks_over_time(request.user, days=30, link=link, include_bots=include_bots)
    referrers = queries.referrer_breakdown(request.user, link=link, limit=10, include_bots=include_bots)
    max_referrer_count = max((row["count"] for row in referrers), default=0)
    variants = queries.variant_breakdown(request.user, link=link, include_bots=include_bots) if link.variant_b else {}

    context = {
        "link": link,
        "bots": include_bots,
        "variant_a_count": variants.get("a", 0),
        "variant_b_count": variants.get("b", 0),
        "total_clicks": link.clicks.count(),
        "bot_clicks": link.clicks.filter(is_bot=True).count(),
        "total_clicks_30": sum(point["clicks"] for point in series),
        "series": series,
        "series_json": json.dumps(series),
        "referrers": referrers,
        "max_referrer_count": max_referrer_count,
        "devices": queries.device_breakdown(request.user, link=link, include_bots=include_bots),
        "countries": queries.country_breakdown(request.user, link=link, limit=10, include_bots=include_bots),
        "recent_clicks": link.clicks.order_by("-created_at")[:10],
    }
    return render(request, "analytics/detail.html", context)


def public_stats(request, code):
    """Shareable, no-login stats page for a link the owner opted in.

    Deliberately aggregates only: totals, 30-day series, devices,
    referrers, countries. Never the destination's identity, click list,
    or per-click rows. Inactive/expired/trashed links 404 so a paused
    link doesn't keep advertising its numbers. Rate-limited per IP
    (120 page views/hour).
    """
    link = get_object_or_404(
        Link,
        code=code,
        is_active=True,
        public_stats=True,
        deleted_at__isnull=True,
    )
    if link.expires_at and link.expires_at <= timezone.now():
        raise Http404()

    from links.views import rate_limited

    @rate_limited("public_stats", 120, 3600)
    def render_page(_request):
        return _render_stats(_request, link)

    return render_page(request)


def _render_stats(request, link):
    series = queries.clicks_over_time(None, days=30, link=link)
    referrers = queries.referrer_breakdown(None, link=link, limit=5)
    max_referrer_count = max((row["count"] for row in referrers), default=0)

    context = {
        "link": link,
        "show_countries": not link.public_hide_countries,
        "countries": [] if link.public_hide_countries else queries.country_breakdown(None, link=link, limit=5),
        "total_clicks": link.clicks.filter(is_bot=False).count(),
        "total_clicks_30": sum(point["clicks"] for point in series),
        "series": series,
        "series_json": json.dumps(series),
        "referrers": referrers,
        "max_referrer_count": max_referrer_count,
        "devices": queries.device_breakdown(None, link=link),
    }
    return render(request, "analytics/public.html", context)


@login_required
def link_analytics_csv(request, code):
    """Download one link's aggregate analytics as CSV (non-bot counts).

    Long-form rows: kind meta/daily/referrer/device/country, label, value.
    Owner-only; trashed links export nothing.
    """
    link = get_object_or_404(Link, code=code, user=request.user, deleted_at__isnull=True)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="analytics-{link.code}.csv"'
    writer = csv.writer(response)
    writer.writerow(["kind", "label", "value"])
    writer.writerow(["meta", "code", link.code])
    writer.writerow(["meta", "destination", link.destination])
    if link.title:
        writer.writerow(["meta", "title", link.title])

    series = queries.clicks_over_time(request.user, days=30, link=link)
    for point in series:
        writer.writerow(["daily", point["date"], point["clicks"]])

    for row in queries.referrer_breakdown(request.user, link=link, limit=20):
        writer.writerow(["referrer", row["referrer"], row["count"]])
    for for_device, count in queries.device_breakdown(request.user, link=link).items():
        writer.writerow(["device", for_device, count])
    for row in queries.country_breakdown(request.user, link=link, limit=20):
        writer.writerow(["country", row["country"], row["count"]])
    if link.variant_b:
        for variant, count in sorted(queries.variant_breakdown(request.user, link=link).items()):
            writer.writerow(["variant", variant, count])
    writer.writerow(["meta", "total_clicks_non_bot", link.clicks.filter(is_bot=False).count()])
    return response
