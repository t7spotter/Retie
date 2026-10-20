"""Views for the website: landing, dashboard, link CRUD, and redirects."""

import csv
import io
import random

from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.urls import reverse

from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from urllib.parse import urlencode, urlsplit

from .forms import LinkForm
from .models import Link, normalize_tag_names

import segno

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
    """Links table queryset honouring the `q`, `tag` and `status` parameters."""
    links = user.links.all()
    q = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or "all"
    tag = (request.GET.get("tag") or "").strip().lower()

    if status == "trash":
        # Trash is fetched from all_objects; the default manager would hide it.
        links = Link.all_objects.filter(user=user, deleted_at__isnull=False)
    else:
        links = user.links.all()  # default manager: trash never leaks in

    if q:
        term = Q(title__icontains=q) | Q(destination__icontains=q) | Q(code__icontains=q)
        links = links.filter(term)

    if tag:
        links = links.filter(tags__name=tag)

    if status == "active":
        links = links.filter(is_active=True).exclude(expires_at__lte=timezone.now())
    elif status == "inactive":
        links = links.filter(is_active=False)
    elif status == "expired":
        links = links.filter(expires_at__lte=timezone.now())

    return links.prefetch_related("tags").annotate(
        click_count=Count("clicks"), last_click_at=Max("clicks__created_at")
    ).order_by("-created_at")


def _table_page(request, links):
    from analytics.models import ClickEvent
    from django.db.models import Count
    from django.db.models.functions import TruncDay
    from django.utils import timezone

    paginator = Paginator(links, LINKS_PER_PAGE)
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or "all"
    tag = (request.GET.get("tag") or "").strip().lower()
    parts = []
    if query:
        parts.append(("q", query))
    if status != "all":
        parts.append(("status", status))
    if tag:
        parts.append(("tag", tag))
    tag_names = list(request.user.tags.order_by("name").values_list("name", flat=True))

    # 14-day per-link click counts for the row sparkline. One aggregate
    # query over this user's (non-bot) clicks; days with no clicks stay 0.
    page = paginator.get_page(request.GET.get("page"))
    spark = {}
    page_links = list(page.object_list)
    if page_links:
        start = (timezone.now() - timezone.timedelta(days=13)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        rows = (
            ClickEvent.objects.filter(link__in=page_links, is_bot=False, created_at__gte=start)
            .annotate(day=TruncDay("created_at"))
            .values("link_id", "day")
            .annotate(n=Count("id"))
        )
        by_day = {}
        for row in rows:
            offset = (row["day"].date() - start.date()).days
            by_day.setdefault(row["link_id"], {})[offset] = row["n"]
        days = list(range(14))
        spark = {
            link.id: [by_day.get(link.id, {}).get(offset, 0) for offset in days]
            for link in page_links
        }

    return {
        "page_obj": page,
        "query": query,
        "status": status,
        "tag": tag,
        "tag_names": tag_names,
        "table_query": urlencode(parts),
        "spark": spark,
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


@rate_limited("qr", limit=120, window_seconds=60, key=lambda r: r.META.get("REMOTE_ADDR") or "")
def link_qr(request, code: str):
    """An SVG QR code pointing at your short URL.

    Public like the redirect itself — QRs get printed and posted where
    browser auth is impossible — but rate-limited so bots can't enumerate
    codes by brute-forcing this endpoint. no-store so a regenerated alias
    shows up immediately.
    """
    from django.shortcuts import get_object_or_404

    link = get_object_or_404(Link, code=code)
    # Owner styling wins; empty values fall back to the defaults.
    dark = link.qr_dark or "#0e353c"
    light = link.qr_light or "#ffffff"
    buffer = io.BytesIO()
    segno.make(f"{settings.SITE_URL}/{link.code}", error="m").save(
        buffer,
        kind="svg",
        scale=8,
        border=2,
        dark=dark,
        light=light,
    )
    response = HttpResponse(buffer.getvalue(), content_type="image/svg+xml")
    response["Cache-Control"] = "no-store"
    response["Content-Disposition"] = f"inline; filename=qr-{link.code}.svg"
    return response


@login_required
def link_import(request):
    """Bulk-create links from a CSV upload.

    Accepted layouts, in order of preference: (1) a header row naming
    columns like destination/long_url, alias/short_url/code, title —
    bit.ly and short.io exports match this; (2) header-less files with the
    destination URL in the first column.

    Invalid rows are reported, valid rows are imported: a partial import
    beats an all-or-nothing failure for a hand-collected CSV.
    """
    if request.method == "GET":
        return render(
            request,
            "pages/link_import.html",
            {"result": None, "error": None, "MAX_IMPORT_ROWS": MAX_IMPORT_ROWS},
        )

    if request.method != "POST":
        raise PermissionDenied

    upload = request.FILES.get("file")
    if upload is None or not upload.name.endswith(".csv"):
        return _import_result(request, error="Choose a .csv file to import.")

    try:
        text = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return _import_result(request, error="The file is not readable UTF-8/ASCII CSV.")

    rows = [row for row in csv.reader(io.StringIO(text)) if any((c or "").strip() for c in row)]
    if not rows:
        return _import_result(request, error="That CSV has no rows.")
    if len(rows) > MAX_IMPORT_ROWS:
        return _import_result(
            request, error=f"Import at most {MAX_IMPORT_ROWS} rows at a time."
        )

    header_map = _import_header_map(rows[0])
    data_rows = rows[1:] if header_map else rows
    column_keys = header_map or _DEFAULT_HEADER_MAP

    created, failed = 0, []
    for index, row in enumerate(data_rows, start=1):
        cells = {
            key: (row[pos].strip() if pos < len(row) else "")
            for key, pos in column_keys.items()
        }
        form = LinkForm(
            data={
                "destination": cells.get("destination") or "",
                "code": _coerce_alias(cells.get("alias") or ""),
                "title": cells.get("title") or "",
            },
        )
        form.user = request.user
        if form.is_valid():
            form.save()
            created += 1
        else:
            failed.append((index, _form_errors_text(form)))

    return _import_result(request, created=created, failed=failed, total=len(data_rows))


HEADER_ALIASES = {
    "destination": ("destination", "long_url", "long url", "longurl", "url", "original url"),
    "alias": ("alias", "code", "short_url", "short url", "shortlink", "short link", "slug"),
    "title": ("title", "name", "description"),
}
_ALL_HEADER_WORDS = {word for words in HEADER_ALIASES.values() for word in words}
_DEFAULT_HEADER_MAP = {"destination": 0}

# Bulk work is generous but bounded; re-uploads are cheap.
MAX_IMPORT_ROWS = 1000

_ERROR_PREVIEW_LIMIT = 25


def _looks_like_header(row) -> bool:
    return any((cell or "").strip().lower() in _ALL_HEADER_WORDS for cell in row)


def _import_header_map(first_row):
    """Map canonical CSV column names to positions, or None if no header."""
    lowered = [(cell or "").strip().lower() for cell in first_row]
    if not _looks_like_header(first_row):
        return None
    mapping = {}
    for canonical, names in HEADER_ALIASES.items():
        for name in names:
            if name in lowered:
                mapping[canonical] = lowered.index(name)
                break
    return mapping or None


def _coerce_alias(raw: str) -> str:
    """Other shorteners export the *full* short URL in their alias column
    ("https://bit.ly/zzz"); use the trailing path segment as the alias."""
    if "://" in raw:
        return urlsplit(raw).path.rstrip("/").rsplit("/", 1)[-1]
    return raw


def _form_errors_text(form) -> str:
    from links.mcp import _form_errors

    return _form_errors(form)


def _import_result(request, created=0, failed=None, total=0, error=None):
    failed = failed or []
    if not error and created + len(failed) == 0:
        error = "No rows were imported."
    response = render(
        request,
        "pages/link_import.html",
        {
            "result": {
                "created": created,
                "failed": failed[:_ERROR_PREVIEW_LIMIT],
                "hidden_errors": max(0, len(failed) - _ERROR_PREVIEW_LIMIT),
                "total": total,
            },
            "error": error,
            "MAX_IMPORT_ROWS": MAX_IMPORT_ROWS,
        },
    )
    if created:
        response["HX-Trigger"] = "links-changed"
    return response


@login_required
def link_export(request):
    """Every link of yours as CSV — a portable backup in one click."""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = "attachment; filename=retie-links.csv"
    writer = csv.writer(response)
    writer.writerow(["code", "destination", "title", "is_active", "expires_at", "created_at", "clicks"])
    links = (
        Link.objects.filter(user=request.user)
        .annotate(total_clicks=Count("clicks"))
        .order_by("-created_at")
    )
    for link in links:
        writer.writerow(
            [
                link.code,
                link.destination,
                link.title,
                "true" if link.is_active else "false",
                link.expires_at.isoformat() if link.expires_at else "",
                link.created_at.isoformat(),
                link.total_clicks,
            ]
        )
    return response


def _duplicate_destination_code(form):
    """Short code of an existing non-trashed link pointing at the same
    destination, or '' when the destination is unique (or it's the same
    row being edited)."""
    user = getattr(form, "user", None)
    destination = form.cleaned_data.get("destination")
    if not user or not destination:
        return ""
    from .models import Link

    existing = Link.objects.filter(
        user=user, destination=destination, deleted_at__isnull=True
    ).exclude(pk=getattr(form.instance, "pk", None) or None)
    link = existing.first()
    return link.code if link else ""


@login_required
@rate_limited("link_create", limit=60, window_seconds=3600, key=lambda r: r.user.pk)
def link_create(request):
    """Create a link. HTMX requests get the form partial back and the table
    refreshes itself via the `links-changed` event; plain posts redirect."""
    if request.method != "POST":
        return redirect("dashboard")

    form = LinkForm(request.POST)
    form.user = request.user
    duplicate_code = ""
    created = form.is_valid()
    if created:
        duplicate_code = _duplicate_destination_code(form)
        if duplicate_code and not form.cleaned_data.get("confirm_duplicate"):
            created = False  # hold the link until the user confirms
        else:
            form.save()

    if _is_htmx(request):
        context = {"form": LinkForm() if created else form, "created": created}
        if created:
            context["created_url"] = _short_url(form.instance.code)
        elif duplicate_code:
            context["duplicate_code"] = duplicate_code
        response = render(request, "links/_form.html", context)
        if created:
            response["HX-Trigger"] = "links-changed"
        return response

    if created:
        messages.success(request, f"Short link /{form.instance.code} created.")
        return redirect("dashboard")
    context = {"form": form, "panel_only_form": True, **_dashboard_context(request)}
    if duplicate_code:
        context["duplicate_code"] = duplicate_code
    return render(request, "pages/dashboard.html", context)


@login_required
def link_edit(request, code):
    link = get_object_or_404(Link, code=code, user=request.user)
    if request.method == "POST":
        form = LinkForm(request.POST, instance=link)
        form.user = request.user
        if form.is_valid():
            duplicate_code = _duplicate_destination_code(form)
            if duplicate_code and not form.cleaned_data.get("confirm_duplicate"):
                return render(
                    request,
                    "pages/link_edit.html",
                    {"form": form, "link": link, "duplicate_code": duplicate_code},
                )
            form.save()
            messages.success(request, "Link updated.")
            return redirect("dashboard")
    else:
        form = LinkForm(instance=link)
    return render(request, "pages/link_edit.html", {"form": form, "link": link})


@login_required
@rate_limited("transfer", limit=10, window_seconds=3600, key=lambda r: r.user.pk)
def link_transfer(request, code):
    """Hand a link to another user, in two steps.

    Step 1: POST with `new_owner` renders a confirmation page naming the
    recipient. Step 2: the same POST with `confirm_transfer=on` moves the
    link, re-homing its tags under the new owner.
    """
    from django.contrib import messages as dj_messages
    from django.contrib.auth import get_user_model

    User = get_user_model()
    link = get_object_or_404(Link, code=code, user=request.user)

    if request.method != "POST":
        return redirect("link_edit", code=code)

    new_owner_name = (request.POST.get("new_owner") or "").strip()
    confirm = request.POST.get("confirm_transfer") == "on"

    # Look the recipient up by username (case-insensitive) or email; the
    # confirmation page posts the pk back, so accept that too.
    lookup = Q(username__iexact=new_owner_name) | Q(email__iexact=new_owner_name)
    if new_owner_name.isdigit():
        lookup |= Q(pk=int(new_owner_name))
    new_owner = User.objects.filter(lookup).first()

    if not new_owner_name:
        return render(
            request,
            "pages/link_transfer.html",
            {"link": link, "new_owner": None, "error": "Enter a username or email."},
        )
    if new_owner is None:
        return render(
            request,
            "pages/link_transfer.html",
            {"link": link, "new_owner": None, "error": "No user found with that username or email."},
        )
    if new_owner == request.user:
        return render(
            request,
            "pages/link_transfer.html",
            {"link": link, "new_owner": None, "error": "You already own this link."},
        )

    if not confirm:
        # Step 1: show the confirmation page.
        return render(
            request,
            "pages/link_transfer.html",
            {"link": link, "new_owner": new_owner},
        )

    # Step 2 confirmed: move the link and re-home tags under the new owner.
    from .models import Tag

    with transaction.atomic():
        link.user = new_owner
        link.save(update_fields=["user", "updated_at"])
        tag_names = list(link.tags.values_list("name", flat=True))
        for name in tag_names:
            tag, _ = Tag.objects.get_or_create(user=new_owner, name=name)
        link.tags.set([t for t in Tag.objects.filter(user=new_owner, name__in=tag_names)])

    dj_messages.success(
        request, f"Transferred /{link.code} to {new_owner.username}."
    )
    return redirect("dashboard")


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
    """SOFT delete: move the link to the trash (restoreable for a while)."""
    link = get_object_or_404(Link, code=code, user=request.user)
    if request.method == "POST":
        link.deleted_at = timezone.now()
        link.save(update_fields=["deleted_at", "updated_at"])
        if _is_htmx(request):
            from django.http import HttpResponse

            response = HttpResponse(b"")
            response["HX-Trigger"] = "links-changed"
            return response
        messages.success(request, "Link moved to trash.")
    return redirect("dashboard")


@login_required
def link_bulk(request):
    """Apply one action to several of the current user's own links at once.

    Actions: activate, deactivate, trash, tag (with `tag` = comma/semi
    separated names). Silently ignores codes that exist but belong to
    someone else, are trashed, or don't exist — the queryset is scoped
    to the caller first, so no cross-user leakage is possible.
    """
    if request.method != "POST":
        raise PermissionDenied("Use POST.")

    action = request.POST.get("action", "")
    raw_codes = [c.strip() for c in request.POST.get("codes", "").split(",") if c.strip()]
    if not action or not raw_codes:
        messages.warning(request, "No links selected.")
        return redirect("dashboard")

    links = list(
        Link.objects.filter(
            user=request.user, code__in=raw_codes, deleted_at__isnull=True
        )
    )

    if action == "activate":
        updated = [link for link in links if not link.is_active]
        for link in updated:
            link.is_active = True
        if updated:
            Link.objects.bulk_update(updated, ["is_active"])
        messages.success(request, f"{len(updated)} link{'s' if len(updated) != 1 else ''} activated.")
    elif action == "deactivate":
        updated = [link for link in links if link.is_active]
        for link in updated:
            link.is_active = False
        if updated:
            Link.objects.bulk_update(updated, ["is_active"])
        messages.success(request, f"{len(updated)} link{'s' if len(updated) != 1 else ''} deactivated.")
    elif action == "trash":
        now = timezone.now()
        for link in links:
            link.deleted_at = now
        if links:
            Link.objects.bulk_update(links, ["deleted_at"])
        messages.info(request, f"{len(links)} link{'s' if len(links) != 1 else ''} moved to trash.")
    elif action == "tag":
        try:
            tag_names = normalize_tag_names(request.POST.get("tag", ""))
        except Exception:
            messages.error(request, "Invalid tag name.")
            return redirect("dashboard")
        if not tag_names:
            messages.warning(request, "No tag given.")
            return redirect("dashboard")
        from .models import Tag

        for tag_name in tag_names:
            tag, _ = Tag.objects.get_or_create(user=request.user, name=tag_name)
            for link in links:
                link.tags.add(tag)
        messages.success(request, f"Tagged {len(links)} link{'s' if len(links) != 1 else ''}.")
    else:
        messages.error(request, "Unknown bulk action.")
        return redirect("dashboard")

    return redirect("dashboard")


@login_required
def link_restore(request, code):
    link = get_object_or_404(
        Link.all_objects, code=code, user=request.user, deleted_at__isnull=False
    )
    if request.method == "POST":
        link.deleted_at = None
        try:
            with transaction.atomic():
                link.save(update_fields=["deleted_at", "updated_at"])
        except IntegrityError:
            messages.error(request, "Another link now owns that short code; rename it first.")
            return redirect("dashboard")
        if _is_htmx(request):
            from django.http import HttpResponse

            response = HttpResponse(b"")
            response["HX-Trigger"] = "links-changed"
            return response
        messages.success(request, "Link restored.")
    return redirect("dashboard")


@login_required
def link_purge(request, code):
    """Permanent delete (trash view only). Click history goes too."""
    link = get_object_or_404(
        Link.all_objects, code=code, user=request.user, deleted_at__isnull=False
    )
    if request.method == "POST":
        link.delete()
        if _is_htmx(request):
            from django.http import HttpResponse

            response = HttpResponse(b"")
            response["HX-Trigger"] = "links-changed"
            return response
        messages.success(request, "Link permanently deleted.")
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
    # Scheduled activation: not live until its go-live moment arrives.
    if link.goes_live_at and timezone.now() < link.goes_live_at:
        raise Http404()

    # Referrer allow-list (hotlink protection). Empty always passes; a
    # missing referrer always passes (typed URLs, QR codes); anything else
    # must match one of the listed hosts (subdomains included).
    if link.allowed_referrers:
        from urllib.parse import urlsplit

        allowed = [d for d in link.allowed_referrers.split("\n") if d]
        referer_host = ""
        referer = request.META.get("HTTP_REFERER", "")
        if referer:
            referer_host = (urlsplit(referer).hostname or "").lower()
        if referer_host:
            host_ok = any(
                referer_host == domain or referer_host.endswith("." + domain)
                for domain in allowed
            )
            if not host_ok:
                raise PermissionDenied("This short link only works from its approved sites.")

    # Password gate: locked links render an unlock form instead of
    # redirecting (and never count a click for the unlock page view).
    if link.password_hash and str(link.pk) not in request.session.get(
        "unlocked_links", []
    ):
        return _unlock_or_403(request, link)

    from analytics.queries import record_click, enqueue_click_webhook

    # A/B split test: when variant B exists, flip a fair coin; the chosen
    # destination is served and its variant is stored on the click event.
    target = link.destination
    variant = "a"
    if link.variant_b:
        variant = "b" if random.random() < 0.5 else "a"
        if variant == "b":
            target = link.variant_b

    click = record_click(link, request, variant=variant)
    # Fire-and-forget outbox row for the owner's click webhook (no-op when
    # no webhook URL is configured). Must never break the redirect.
    try:
        enqueue_click_webhook(click)
    except Exception:
        pass
    status = 301 if link.permanent else 302
    # A 301 would let browsers cache one arm of a split test for good; only
    # ever send it for links without an active test.
    if link.variant_b:
        status = 302
    return HttpResponseRedirect(
        target,
        status=status,
        headers={"Cache-Control": "no-store" if status == 302 else "max-age=3600"},
    )


def _unlock_or_403(request, link):
    """Serve the unlock form for a password-gated link.

    GET renders the form; POST checks the password against the stored
    hash and remembers the unlocked link in the visitor's session. Wrong
    answers are rate-limited per IP to make brute force slow.
    """
    from django.contrib.auth.hashers import check_password

    unlocked_before = request.session.get("unlocked_links", [])

    if request.method == "POST":
        # Brute-force brake: 20 wrong tries per IP per 15 minutes.
        ip = request.META.get("REMOTE_ADDR", "unknown")
        misses_key = f"unlock-misses:{ip}"
        misses = cache.get(misses_key, 0)
        if misses >= 20:
            raise PermissionDenied("Too many wrong passwords from your network.")

        password = request.POST.get("password", "")
        if check_password(password, link.password_hash):
            cache.delete(misses_key)
            request.session["unlocked_links"] = [*unlocked_before, str(link.pk)]
            # Round-trip through the short code so the redirect itself
            # records the click like any other visit.
            return HttpResponseRedirect(reverse("redirect", args=[link.code]))
        cache.set(misses_key, misses + 1, 15 * 60)
        messages.error(request, "Wrong password. Try again.")

    return render(request, "pages/unlock.html", {"link": link}, status=200)
