"""Analytics pages. Click logging itself happens in links.views.redirect_view."""

import json

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from . import queries
from links.models import Link


@login_required
def link_analytics(request, code):
    """Per-link analytics: totals, 30-day chart, referrers, devices, activity."""
    link = get_object_or_404(Link, code=code, user=request.user)
    series = queries.clicks_over_time(request.user, days=30, link=link)
    referrers = queries.referrer_breakdown(request.user, link=link, limit=10)
    max_referrer_count = max((row["count"] for row in referrers), default=0)

    context = {
        "link": link,
        "total_clicks": link.clicks.count(),
        "total_clicks_30": sum(point["clicks"] for point in series),
        "series": series,
        "series_json": json.dumps(series),
        "referrers": referrers,
        "max_referrer_count": max_referrer_count,
        "devices": queries.device_breakdown(request.user, link=link),
        "recent_clicks": link.clicks.order_by("-created_at")[:10],
    }
    return render(request, "analytics/detail.html", context)
