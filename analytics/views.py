"""Analytics pages. Click logging itself happens in links.views.redirect_view."""

import json

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

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

    context = {
        "link": link,
        "bots": include_bots,
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
