"""Model Context Protocol (MCP) server for Retie.

A small, stateless implementation of the Streamable HTTP transport: a single
endpoint (/mcp/) speaks JSON-RPC 2.0 and exposes link-management tools to
MCP clients (Claude Desktop/Claude Code, Cursor, OpenCode, ...).

Authentication reuses the REST API's keys (Authorization: Bearer <key> or
Token <key>), so an agent acts as exactly one user, ownership scoping is
enforced everywhere, and a key can be revoked instantly on the profile page.
No new dependencies: the transport is plain HTTP + JSON.
"""

import json

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Max, Q
from django.http import HttpResponse
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive, make_aware
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from analytics import queries
from .authentication import MultiSchemeTokenAuthentication
from .forms import LinkForm
from .models import Link, Tag, generate_short_code

MAX_LIST_PAGE_SIZE = 50
MAX_ANALYTICS_DAYS = 90


class ToolError(Exception):
    """A tool failed for a reason the client should see (bad input, no
    permission, not found). Surfaced as an MCP isError result, not an
    internal server error."""


# -- Tool handlers: every queryset is scoped to the authenticated user. -----


def _user_link(user, code: str) -> Link:
    try:
        return Link.objects.get(code=code, user=user)
    except Link.DoesNotExist:
        raise ToolError(f"No link with code {code!r}.")


def _parse_expires(raw, field: str = "expires_at") -> object:
    """Accept an ISO-8601 datetime ('2026-01-31T14:30' or with seconds/TZ)."""
    parsed = parse_datetime(raw)
    if parsed is None:
        raise ToolError(
            f"{field} {raw!r} is not a valid ISO 8601 datetime "
            "(e.g. '2026-01-31T14:30')."
        )
    if settings.USE_TZ and is_naive(parsed):
        parsed = make_aware(parsed)
    return parsed


def _form_errors(form) -> str:
    return "; ".join(
        f"{field}: {', '.join(errors)}" for field, errors in form.errors.items()
    )


def tool_create_link(user, args: dict) -> dict:
    expires = _parse_expires(args["expires_at"]) if args.get("expires_at") else None
    password = str(args.get("password") or "")
    form = LinkForm(
        data={
            "destination": args.get("destination", ""),
            "title": args.get("title", ""),
            "code": args.get("alias", ""),
            "tags": args.get("tags", ""),
            "notes": args.get("notes", ""),
            "permanent": "on" if args.get("permanent") else "",
            "public_stats": "on" if args.get("public_stats") else "",
            "public_hide_countries": "on" if args.get("public_hide_countries") else "",
            "password": password,
        },
        user=user,
    )
    if not form.is_valid():
        raise ToolError(_form_errors(form))
    cleaned = form.cleaned_data
    custom_alias = bool(cleaned.get("code"))
    remaining = 5
    while True:
        payload = {
            "destination": cleaned["destination"],
            "title": cleaned["title"],
            "is_active": True,
            "permanent": bool(cleaned.get("permanent", False)),
            "public_stats": bool(cleaned.get("public_stats", False)),
            "public_hide_countries": bool(cleaned.get("public_hide_countries", False)),
            "notes": cleaned.get("notes", ""),
        }
        raw_referrers = args.get("allowed_referrers")
        if raw_referrers:
            from .models import normalize_referrer_domains

            payload["allowed_referrers"] = "\n".join(
                normalize_referrer_domains(", ".join(raw_referrers) if isinstance(raw_referrers, list) else raw_referrers)
            )
        if cleaned.get("password"):
            from django.contrib.auth.hashers import make_password

            payload["password_hash"] = make_password(cleaned["password"])
        if args.get("variant_b"):
            from .models import validate_destination
            payload["variant_b"] = validate_destination(str(args["variant_b"]))
        if args.get("domain"):
            from django.conf import settings
            from urllib.parse import urlsplit
            _domain = str(args["domain"]).strip().lower()
            _default_host = (urlsplit(settings.SITE_URL).hostname or "").lower()
            if _domain != _default_host and _domain not in settings.EXTRA_DOMAINS:
                raise ToolError("domain must be one of EXTRA_DOMAINS (or the SITE_URL host).")
            payload["domain"] = _domain
        for qr_key in ("qr_dark", "qr_light"):
            if args.get(qr_key):
                import re
                color = str(args[qr_key]).strip()
                if not re.fullmatch(r"#?[0-9a-fA-F]{6}", color):
                    raise ToolError(f"{qr_key} must be a 6-digit hex color like #0e353c.")
                payload[qr_key] = f"#{color.lstrip('#').lower()}"
        goes_live = _parse_expires(args["goes_live_at"], "goes_live_at") if args.get("goes_live_at") else None
        if goes_live is not None:
            payload["goes_live_at"] = goes_live
        payload["code"] = cleaned.get("code") or generate_short_code()
        if expires is not None:
            payload["expires_at"] = expires
        try:
            link = Link.objects.create(user=user, **payload)
            for name in form.tag_names:
                tag, _ = Tag.objects.get_or_create(user=user, name=name)
                link.tags.add(tag)
        except Exception:
            if custom_alias:
                raise ToolError("That alias is already in use.")
            remaining -= 1
            if not remaining:
                raise ToolError("Could not generate a unique code; try again.")
            continue
        break
    return {
        "code": link.code,
        "short_url": link.short_url,
        "destination": link.destination,
        "is_active": link.is_active,
        "permanent": link.permanent,
        "public_stats": link.public_stats,
        "public_hide_countries": link.public_hide_countries,
        "allowed_referrers": [d for d in link.allowed_referrers.split("\n") if d],
        "notes": link.notes,
        "expires_at": link.expires_at,
        "goes_live_at": link.goes_live_at,
        "variant_b": link.variant_b,
        "qr_dark": link.qr_dark,
        "qr_light": link.qr_light,
        "domain": link.domain,
    }


def tool_list_links(user, args: dict) -> dict:
    qs = (
        Link.objects.filter(user=user)
        .annotate(click_count=Count("clicks"), last_click_at=Max("clicks__created_at"))
        .order_by("-created_at")
    )
    query = (args.get("query") or "").strip()
    if query:
        qs = qs.filter(
            Q(destination__icontains=query)
            | Q(code__icontains=query)
            | Q(title__icontains=query)
        )
    tag = (args.get("tag") or "").strip().lower()
    if tag:
        qs = qs.filter(tags__name=tag)
    per_page = MAX_LIST_PAGE_SIZE
    page = max(1, int(args.get("page") or 1))
    start = (page - 1) * per_page
    rows = qs[start : start + per_page + 1]
    links = [
        {
            "code": link.code,
            "short_url": link.short_url,
            "destination": link.destination,
            "title": link.title,
            "is_active": link.is_active,
            "expires_at": link.expires_at,
            "tags": sorted(link.tags.values_list("name", flat=True)),
            "clicks": link.click_count,
            "last_click_at": link.last_click_at,
        }
        for link in rows[:per_page]
    ]
    return {"page": page, "has_more": len(rows) > per_page, "links": links}


def tool_get_link(user, args: dict) -> dict:
    link = _user_link(user, args["code"])
    return {
        "code": link.code,
        "short_url": link.short_url,
        "destination": link.destination,
        "title": link.title,
        "is_active": link.is_active,
        "expires_at": link.expires_at,
        "permanent": link.permanent,
        "public_stats": link.public_stats,
        "public_hide_countries": link.public_hide_countries,
        "allowed_referrers": [d for d in link.allowed_referrers.split("\n") if d],
        "notes": link.notes,
        "expires_at": link.expires_at,
        "goes_live_at": link.goes_live_at,
        "variant_b": link.variant_b,
        "qr_dark": link.qr_dark,
        "qr_light": link.qr_light,
        "domain": link.domain,
        "total_clicks": link.clicks.count(),
    }


def tool_update_link(user, args: dict) -> dict:
    link = _user_link(user, args["code"])
    updates = []
    if "destination" in args and args["destination"]:
        # Same raw-value scheme check the dashboard form applies.
        from .models import validate_destination  # local import: fine here

        validate_destination(args["destination"])
        link.destination = args["destination"]
        updates.append("destination")
    if "alias" in args and args["alias"]:
        from .models import validate_alias

        link.code = validate_alias(args["alias"].strip().lower())
        updates.append("code")
    if "title" in args:
        link.title = args["title"] or ""
        updates.append("title")
    if "expires_at" in args:
        link.expires_at = _parse_expires(args["expires_at"]) if args["expires_at"] else None
        updates.append("expires_at")
    if "goes_live_at" in args:
        link.goes_live_at = _parse_expires(args["goes_live_at"], "goes_live_at") if args["goes_live_at"] else None
        updates.append("goes_live_at")
    if "variant_b" in args:
        if args["variant_b"] in (None, ""):
            link.variant_b = ""
        else:
            from .models import validate_destination
            link.variant_b = validate_destination(str(args["variant_b"]))
        updates.append("variant_b")
    import re as _re
    if "qr_dark" in args:
        color = str(args["qr_dark"] or "").strip()
        if color and not _re.fullmatch(r"#?[0-9a-fA-F]{6}", color):
            raise ToolError("qr_dark must be a 6-digit hex color like #0e353c.")
        link.qr_dark = f"#{color.lstrip('#').lower()}" if color else ""
        updates.append("qr_dark")
    if "qr_light" in args:
        color = str(args["qr_light"] or "").strip()
        if color and not _re.fullmatch(r"#?[0-9a-fA-F]{6}", color):
            raise ToolError("qr_light must be a 6-digit hex color like #ffffff.")
        link.qr_light = f"#{color.lstrip('#').lower()}" if color else ""
        updates.append("qr_light")
    if "domain" in args:
        from django.conf import settings as dj_settings
        from urllib.parse import urlsplit as _urlsplit
        _domain = str(args["domain"] or "").strip().lower()
        if _domain:
            _default_host = (_urlsplit(dj_settings.SITE_URL).hostname or "").lower()
            if _domain != _default_host and _domain not in dj_settings.EXTRA_DOMAINS:
                raise ToolError("domain must be one of EXTRA_DOMAINS (or the SITE_URL host).")
        link.domain = _domain
        updates.append("domain")
    if "is_active" in args:
        link.is_active = bool(args["is_active"])
        updates.append("is_active")
    if "public_stats" in args:
        link.public_stats = bool(args["public_stats"])
        updates.append("public_stats")
    if "public_hide_countries" in args:
        link.public_hide_countries = bool(args["public_hide_countries"])
        updates.append("public_hide_countries")
    raw_referrers = args.get("allowed_referrers")
    if raw_referrers is not None:
        from .models import normalize_referrer_domains

        domains = normalize_referrer_domains(", ".join(raw_referrers) if isinstance(raw_referrers, list) else raw_referrers)
        link.allowed_referrers = "\n".join(domains)
        updates.append("allowed_referrers")
    if "notes" in args:
        link.notes = str(args["notes"] or "")[:2000]
        updates.append("notes")
    if "permanent" in args:
        link.permanent = bool(args["permanent"])
        updates.append("permanent")
    if args.get("password"):
        from django.contrib.auth.hashers import make_password

        link.password_hash = make_password(str(args["password"]))
        updates.append("password_hash")
    elif args.get("remove_password"):
        link.password_hash = ""
        updates.append("password_hash")
    if updates:
        try:
            link.save(update_fields=[*updates, "updated_at"])
        except Exception:
            raise ToolError("That alias is already in use.")
    return tool_get_link(user, args)


def tool_delete_link(user, args: dict) -> dict:
    link = _user_link(user, args["code"])
    link.delete()
    return {"deleted": args["code"]}


def tool_get_link_analytics(user, args: dict) -> dict:
    link = _user_link(user, args["code"])
    days = min(MAX_ANALYTICS_DAYS, max(1, int(args.get("days") or 30)))
    include_bots = bool(args.get("bots", False))
    return {
        "code": link.code,
        "total_clicks": link.clicks.count(),
        "bot_clicks": link.clicks.filter(is_bot=True).count(),
        "clicks_over_time": queries.clicks_over_time(user, days, link=link, include_bots=include_bots),
        "referrers": queries.referrer_breakdown(user, link=link, limit=8, include_bots=include_bots),
        "devices": queries.device_breakdown(user, link=link, include_bots=include_bots),
        "countries": queries.country_breakdown(user, link=link, include_bots=include_bots),
    }


def tool_get_dashboard_stats(user, args: dict) -> dict:
    stats = queries.user_totals(user)
    series = queries.clicks_over_time(user, days=30)
    return {
        "total_links": stats["total_links"],
        "active_links": stats["active_links"],
        "total_clicks": stats["total_clicks"],
        "clicks_last_30_days": sum(p["clicks"] for p in series),
        "last_click": stats["last_click"],
    }


# -- Tool metadata ----------------------------------------------------------

_OBJECT = {"type": "object"}

TOOLS = [
    {
        "name": "create_link",
        "description": (
            "Create a short link on this Retie instance. Returns the short "
            "code and full short URL. Either let a code be generated or "
            "provide a custom alias (2-31 chars: a-z, 0-9 or hyphen)."
        ),
        "inputSchema": {
            **_OBJECT,
            "required": ["destination"],
            "properties": {
                "destination": {
                    "type": "string",
                    "description": "Full destination URL, must start with http:// or https://.",
                },
                "variant_b": {
                    "type": "string",
                    "description": "Optional second destination (A/B split test, 50/50).",
                },
                "qr_dark": {"type": "string", "description": "QR code color (6-digit hex, e.g. #0e353c)."},
                "qr_light": {"type": "string", "description": "QR background color (6-digit hex)."},
                "alias": {"type": "string", "description": "Optional custom short code (alias)."},
                "title": {"type": "string", "description": "Optional human-readable title."},
                "tags": {"type": "string", "description": "Optional comma-separated tags."},
                "expires_at": {
                    "type": "string",
                    "description": "Optional ISO 8601 expiry after which the link stops redirecting.",
                },
                "goes_live_at": {
                    "type": "string",
                    "description": "Optional ISO 8601 go-live moment; the link 404s until then.",
                },
                "is_active": {"type": "boolean", "description": "Defaults to true."},
                "public_stats": {"type": "boolean", "description": "Publish a public stats page at /stats/<code>."},
        "public_hide_countries": {"type": "boolean", "description": "Hide the country list on the public stats page."},
                "notes": {"type": "string", "description": "Private owners-only note (max 2000 chars)."},
                "permanent": {"type": "boolean", "description": "Respond with a cachable 301 redirect instead of 302. Browsers then stop reporting clicks."},
                "password": {"type": "string", "description": "Optional gate password; visitors unlock once per browser session."},
            },
        },
        "handler": tool_create_link,
    },
    {
        "name": "list_links",
        "description": "List your links, newest first, with click counts.",
        "inputSchema": {
            **_OBJECT,
            "properties": {
                "query": {"type": "string", "description": "Filter by destination, alias or title."},
                "tag": {"type": "string", "description": "Only links with this tag."},
                "page": {"type": "integer", "description": "1-based page number."},
            },
        },
        "handler": tool_list_links,
    },
    {
        "name": "get_link",
        "description": "Get one of your links by short code.",
        "inputSchema": {
            **_OBJECT,
            "required": ["code"],
            "properties": {"code": {"type": "string", "description": "The short code / alias."}},
        },
        "handler": tool_get_link,
    },
    {
        "name": "update_link",
        "description": "Update fields of one of your links. Only provided fields change.",
        "inputSchema": {
            **_OBJECT,
            "required": ["code"],
            "properties": {
                "code": {"type": "string", "description": "Current short code of the link."},
                "destination": {"type": "string", "description": "New destination (http/https)."},
                "variant_b": {"type": ["string", "null"], "description": "New variant B destination (A/B test); empty string or null ends the test."},
                "qr_dark": {"type": "string", "description": "QR code color (6-digit hex)."},
                "qr_light": {"type": "string", "description": "QR background color (6-digit hex)."},
                "alias": {"type": "string", "description": "New short code."},
                "title": {"type": "string", "description": "New title ('' clears it)."},
                "expires_at": {
                    "type": ["string", "null"],
                    "description": "New expiry (ISO 8601); null removes the expiry.",
                },
                "goes_live_at": {
                    "type": ["string", "null"],
                    "description": "New go-live moment (ISO 8601); null makes the link live immediately.",
                },
                "is_active": {"type": "boolean", "description": "Start/stop redirecting."},
                "public_stats": {"type": "boolean", "description": "Publish/unpublish the public stats page."},
                "public_hide_countries": {"type": "boolean", "description": "Hide countries on the public stats page."},
                "allowed_referrers": {"type": "array", "items": {"type": "string"}, "description": "Replace the referrer allow-list; empty array clears it."},
                "permanent": {"type": "boolean", "description": "301 (true) instead of 302."},
                "password": {"type": "string", "description": "Set or replace the link password."},
                "remove_password": {"type": "boolean", "description": "Remove the link password."},
            },
        },
        "handler": tool_update_link,
    },
    {
        "name": "delete_link",
        "description": "Delete one of your links; its click history is removed too.",
        "inputSchema": {
            **_OBJECT,
            "required": ["code"],
            "properties": {"code": {"type": "string", "description": "The short code to delete."}},
        },
        "handler": tool_delete_link,
    },
    {
        "name": "get_link_analytics",
        "description": "Aggregated analytics for one of your links: clicks over time, referrers, devices, countries. Known bot clicks are excluded by default.",
        "inputSchema": {
            **_OBJECT,
            "required": ["code"],
            "properties": {
                "code": {"type": "string"},
                "days": {"type": "integer", "description": "Window in days, default 30, max 90."},
                "bots": {"type": "boolean", "description": "Include known bot clicks (default false)."},
            },
        },
        "handler": tool_get_link_analytics,
    },
    {
        "name": "get_dashboard_stats",
        "description": "Headline totals across all of your links (links, active, clicks, last click).",
        "inputSchema": {**_OBJECT, "properties": {}},
        "handler": tool_get_dashboard_stats,
    },
]

TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}

SERVER_INFO = {"name": "retie", "title": "Retie link shortener", "version": "1.0.0"}

INSTRUCTIONS = (
    "Retie is a self-hosted URL shortener. Tools operate only on the links "
    "belonging to the user whose API key authenticates the session. "
    "create_link returns the ready-to-share short_url."
)


# -- JSON-RPC plumbing -------------------------------------------------------


def _result(request_id, value) -> HttpResponse:
    return _rpc({"jsonrpc": "2.0", "id": request_id, "result": value})


def _error(request_id, code: int, message: str) -> HttpResponse:
    return _rpc({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def _rpc(payload: dict) -> HttpResponse:
    response = HttpResponse(
        json.dumps(payload, default=str), content_type="application/json"
    )
    response["Cache-Control"] = "no-store"
    return response


def _authenticate(request):
    """Resolve a user from Authorization: Bearer/Token <key>, or None."""
    try:
        drf_request = Request(request, authenticators=[MultiSchemeTokenAuthentication()])
        user = drf_request.user
    except AuthenticationFailed:
        return None
    if not user.is_authenticated:
        return None
    return user


def _dispatch(user, method: str, params: dict, request_id):
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": params.get("protocolVersion") or "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(
            request_id,
            {
                "tools": [
                    {
                        "name": tool["name"],
                        "description": tool["description"],
                        "inputSchema": tool["inputSchema"],
                    }
                    for tool in TOOLS
                ]
            },
        )
    if method == "tools/call":
        name = params.get("name")
        tool = TOOLS_BY_NAME.get(name)
        if tool is None:
            return _error(request_id, -32602, f"Unknown tool {name!r}.")
        if request_id is None:
            return HttpResponse(status=202)  # notification calls get no result
        try:
            value = tool["handler"](user, params.get("arguments") or {})
        except ToolError as error:
            return _result(
                request_id,
                {"content": [{"type": "text", "text": str(error)}], "isError": True},
            )
        except DjangoValidationError as error:
            return _result(
                request_id,
                {
                    "content": [{"type": "text", "text": "; ".join(error.messages)}],
                    "isError": True,
                },
            )
        return _result(
            request_id,
            {"content": [{"type": "text", "text": json.dumps(value, default=str, indent=2)}]},
        )
    if request_id is None:
        return HttpResponse(status=202)  # unknown notification: accept silently
    return _error(request_id, -32601, f"Method not found: {method!r}")


@csrf_exempt
@require_POST
def mcp_endpoint(request):
    """Streamable HTTP transport: all traffic is POST of one JSON-RPC message.

    GET/DELETE answer 405 (this is a stateless server; there is no SSE stream
    to open) and requests without a valid API key answer 401.
    """
    user = _authenticate(request)
    if user is None:
        response = HttpResponse(
            json.dumps({"error": "unauthorized",
                        "message": "Send Authorization: Bearer <api key>"}),
            content_type="application/json",
            status=401,
        )
        response["WWW-Authenticate"] = 'Bearer realm="mcp"'
        return response

    try:
        payload = json.loads(request.body or "")
    except ValueError:
        return _error(None, -32700, "Parse error: body is not valid JSON.")
    if not isinstance(payload, dict):
        return _error(None, -32600, "Invalid request: batching is not supported.")

    method = payload.get("method") or ""
    request_id = payload.get("id")
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    # Notifications (no "id") get an empty 202 acknowledgement.
    if method == "notifications/initialized":
        return HttpResponse(status=202)
    return _dispatch(user, method, params, request_id)
