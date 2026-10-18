"""Views for the website: landing, dashboard, link CRUD, and redirects."""

from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import LinkForm
from .models import Link

LINKS_PER_PAGE = 10


def _is_htmx(request) -> bool:
    return bool(request.headers.get("HX-Request"))


FEATURES = [
    {"icon": "link", "title": "Short links, your domain",
     "text": "Create tidy short URLs on the domain you own — no third-party service in the loop."},
    {"icon": "bar-chart-3", "title": "Simple click analytics",
     "text": "Daily clicks, referrers, and device categories over the last 30 days. No IP storage."},
    {"icon": "shield-check", "title": "Security by design",
     "text": "CSRF protection, secure cookies, validated URLs, rate limiting, and per-user isolation."},
    {"icon": "calendar-clock", "title": "Expiry & scheduling",
     "text": "Give any link an absolute expiration date — expired links stop resolving cleanly."},
    {"icon": "moon", "title": "Light & dark themes",
     "text": "A polished, responsive interface that respects your system preference and switches on demand."},
    {"icon": "server", "title": "Runs anywhere",
     "text": "One Docker compose file, PostgreSQL included, Gunicorn in front — deploy in minutes."},
]

# Static illustration for the product preview on the landing page.
PREVIEW_ITEMS = [
    {"icon": "github", "title": "GitHub repository", "code": "gh-proj"},
    {"icon": "video", "title": "Launch demo video", "code": "demo"},
    {"icon": "calendar", "title": "Pricing page", "code": "pricing"},
]


def landing(request):
    """Root URL. With the landing page enabled it is the marketing entry;
    otherwise it is a plain doorway: signed-in users go to the dashboard,
    everyone else to login — the marketing page never shows.
    """
    if not settings.LANDING_ENABLED:
        return redirect("dashboard" if request.user.is_authenticated else "login")
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(
        request,
        "pages/landing.html",
        {"features": FEATURES, "preview_items": PREVIEW_ITEMS},
    )


def rate_limited(scope: str, limit: int, window_seconds: int, key=None):
    """Decorator: cap requests per time window using Django's cache.

    The default LocMem cache is per-process, so with multiple Gunicorn
    workers each worker counts independently. Rates are therefore set per
    worker; this is a documented trade-off (see README).
    """
    if key is None:
        key = lambda request: request.META.get("REMOTE_ADDR", "unknown")  # noqa: E731

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            cache_key = f"ratelimit:{scope}:{key(request)}"
            try:
                hits = cache.incr(cache_key)
            except ValueError:
                cache.add(cache_key, 1, window_seconds)
                hits = 1
            if hits > limit:
                raise PermissionDenied("Too many requests. Please slow down.")
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


def _filtered_links(user, request):
    """Links table queryset honouring the `q` and `status` query parameters."""
    links = user.links.all()
    q = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or "all"

    if q:
        term = Q(title__icontains=q) | Q(destination__icontains=q) | Q(code__icontains=q)
        links = links.filter(term)

    if status == "active":
        links = links.filter(is_active=True).exclude(expires_at__lte=timezone.now())
    elif status == "inactive":
        links = links.filter(is_active=False)
    elif status == "expired":
        links = links.filter(expires_at__lte=timezone.now())

    return links.annotate(
        click_count=Count("clicks"), last_click_at=Max("clicks__created_at")
    ).order_by("-created_at")


def _table_page(request, links):
    paginator = Paginator(links, LINKS_PER_PAGE)
    return {
        "page_obj": paginator.get_page(request.GET.get("page")),
        "query": (request.GET.get("q") or "").strip(),
        "status": request.GET.get("status") or "all",
    }


def _short_url(code: str) -> str:
    return f"{settings.SITE_URL}/{code}"


def _dashboard_context(request):
    import json

    from analytics import queries

    links = _filtered_links(request.user, request)
    series = queries.clicks_over_time(request.user, days=14)
    return {
        "stats": queries.user_totals(request.user),
        "series": series,
        "series_total": sum(point["clicks"] for point in series),
        "series_json": json.dumps(series),
        "recent_clicks": queries.recent_clicks(request.user, limit=5),
        **_table_page(request, links),
    }


@login_required
def dashboard(request):
    return render(
        request,
        "pages/dashboard.html",
        {"form": LinkForm(), **_dashboard_context(request)},
    )


@login_required
def link_table(request):
    """HTMX partial: only the links table (search, filter, pagination)."""
    return render(
        request,
        "links/_table.html",
        _table_page(request, _filtered_links(request.user, request)),
    )


@login_required
@rate_limited("link_create", limit=60, window_seconds=3600, key=lambda r: r.user.pk)
def link_create(request):
    """Create a link. HTMX requests get the form partial back and the table
    refreshes itself via the `links-changed` event; plain posts redirect."""
    if request.method != "POST":
        return redirect("dashboard")

    form = LinkForm(request.POST)
    form.user = request.user
    created = form.is_valid()
    if created:
        form.save()

    if _is_htmx(request):
        context = {"form": LinkForm() if created else form, "created": created}
        if created:
            context["created_url"] = _short_url(form.instance.code)
        response = render(request, "links/_form.html", context)
        if created:
            response["HX-Trigger"] = "links-changed"
        return response

    if created:
        messages.success(request, f"Short link /{form.instance.code} created.")
        return redirect("dashboard")
    return render(
        request,
        "pages/dashboard.html",
        {"form": form, "panel_only_form": True, **_dashboard_context(request)},
    )


@login_required
def link_edit(request, code):
    link = get_object_or_404(Link, code=code, user=request.user)
    if request.method == "POST":
        form = LinkForm(request.POST, instance=link)
        form.user = request.user
        if form.is_valid():
            form.save()
            messages.success(request, "Link updated.")
            return redirect("dashboard")
    else:
        form = LinkForm(instance=link)
    return render(request, "pages/link_edit.html", {"form": form, "link": link})


@login_required
def link_toggle(request, code):
    """Activate/deactivate. Returns the row partial for HTMX, else redirects."""
    link = get_object_or_404(Link, code=code, user=request.user)
    if request.method == "POST":
        link.is_active = not link.is_active
        link.save(update_fields=["is_active", "updated_at"])
        if _is_htmx(request):
            return render(request, "links/_row.html", {"link": link})
        messages.success(request, "Link updated.")
    return redirect("dashboard")


@login_required
def link_delete(request, code):
    link = get_object_or_404(Link, code=code, user=request.user)
    if request.method == "POST":
        link.delete()
        if _is_htmx(request):
            from django.http import HttpResponse

            response = HttpResponse(b"")
            response["HX-Trigger"] = "links-changed"
            return response
        messages.success(request, "Link deleted.")
    return redirect("dashboard")


def redirect_view(request, code):
    """Public short-code dispatch.

    Unknown, deactivated, and expired links get a 404 instead of a redirect.
    Redirects use 302 so browsers never cache them and click counts stay
    accurate.
    """
    link = get_object_or_404(Link, code=code)

    if not link.is_active:
        raise Http404()
    if link.expires_at and link.expires_at <= timezone.now():
        raise Http404()

    from analytics.queries import record_click

    record_click(link, request)
    return HttpResponseRedirect(
        link.destination,
        status=302,
        headers={"Cache-Control": "no-store"},
    )
