"""DRF endpoints for links, per-link analytics, stats, profile, and API keys."""

from django.contrib.auth import authenticate
from django.db import IntegrityError
from django.db.models import Count, Max
from django.utils import timezone
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from analytics import queries
from drf_spectacular.utils import extend_schema, extend_schema_view
from .authentication import MultiSchemeTokenAuthentication
from .models import Link, generate_short_code
from .serializers import (
    DashboardStatsSerializer,
    LinkAnalyticsResponseSerializer,
    LinkSerializer,
    ProfileSerializer,
)


@extend_schema_view(
    list=extend_schema(summary="List the current user's links."),
    create=extend_schema(summary="Create a short link."),
    retrieve=extend_schema(summary="Retrieve one link by short code."),
    update=extend_schema(summary="Update a link."),
    partial_update=extend_schema(summary="Partially update a link."),
    destroy=extend_schema(summary="Delete a link (click history goes with it)."),
    activate=extend_schema(summary="Start redirecting this link again."),
    deactivate=extend_schema(summary="Stop redirecting this link."),
)
class LinkViewSet(viewsets.ModelViewSet):
    """CRUD on the current user's links. Lookup is by short code."""

    serializer_class = LinkSerializer
    lookup_field = "code"

    def get_queryset(self):
        queryset = (
            Link.objects.filter(user=self.request.user)
            .annotate(click_count=Count("clicks"), last_click_at=Max("clicks__created_at"))
            .order_by("-created_at")
        )
        tag = (self.request.query_params.get("tag") or "").strip().lower()
        if tag:
            queryset = queryset.filter(tags__name=tag)
        return queryset

    def get_throttles(self):
        # Only creation is rate-limited; it's the expensive/user-visible one.
        if self.action == "create":
            self.throttle_scope = "link_create"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Empty codes are generated and retried against the unique constraint;
        # a user-chosen alias that collides is a client error, not a retry.
        custom_alias = bool(data.get("code"))
        remaining = 5
        while True:
            payload = dict(data)
            payload["code"] = data.get("code") or generate_short_code()
            try:
                link = Link.objects.create(user=request.user, **payload)
            except IntegrityError:
                if custom_alias:
                    return Response(
                        {"code": ["That alias is already in use."]},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                remaining -= 1
                if not remaining:
                    return Response(
                        {"code": ["Could not generate a unique code."]},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    )
                continue
            break

        return Response(
            self.get_serializer(link).data, status=status.HTTP_201_CREATED
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        try:
            serializer.save()
        except IntegrityError:
            return Response(
                {"code": ["That alias is already in use."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def activate(self, request, code=None):
        link = self.get_object()
        link.is_active = True
        link.save(update_fields=["is_active", "updated_at"])
        return Response(self.get_serializer(link).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, code=None):
        link = self.get_object()
        link.is_active = False
        link.save(update_fields=["is_active", "updated_at"])
        return Response(self.get_serializer(link).data)


@extend_schema(responses=LinkAnalyticsResponseSerializer, summary="Per-link analytics.")
class LinkAnalyticsView(APIView):
    """Aggregated analytics for one of the current user's links."""

    def get(self, request, code):
        try:
            link = Link.objects.get(code=code, user=request.user)
        except Link.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        include_bots = request.query_params.get("bots") == "1"
        return Response(
            {
                "link": LinkSerializer(link).data,
                "total_clicks": link.clicks.count(),
                "bot_clicks": link.clicks.filter(is_bot=True).count(),
                "clicks_over_time": queries.clicks_over_time(
                    request.user, 30, link=link, include_bots=include_bots
                ),
                "referrers": queries.referrer_breakdown(
                    request.user, link=link, limit=8, include_bots=include_bots
                ),
                "devices": queries.device_breakdown(request.user, link=link, include_bots=include_bots),
                "countries": queries.country_breakdown(
                    request.user, link=link, include_bots=include_bots
                ),
            }
        )


@extend_schema(responses=DashboardStatsSerializer, summary="Dashboard totals for the current user.")
class DashboardStatsView(APIView):
    def get(self, request):
        stats = queries.user_totals(request.user)
        series = queries.clicks_over_time(request.user, days=30)
        return Response(
            {
                "total_links": stats["total_links"],
                "active_links": stats["active_links"],
                "total_clicks": stats["total_clicks"],
                "clicks_last_30_days": sum(p["clicks"] for p in series),
                "clicks_over_time": series,
                "last_click": stats["last_click"],
            }
        )


@extend_schema(responses=ProfileSerializer, summary="The current user's profile.")
class ProfileView(APIView):
    def get(self, request):
        return Response(
            {
                "username": request.user.username,
                "email": request.user.email,
                "date_joined": request.user.date_joined,
                "has_api_token": Token.objects.filter(user=request.user).exists(),
            }
        )


class APIKeySerializer(serializers.Serializer):
    """Request body for POST /api/v1/token/: username and password exchange."""

    username = serializers.CharField(max_length=150)
    password = serializers.CharField()


@extend_schema_view(
    post=extend_schema(
        operation_id="token_create",
        request=APIKeySerializer,
        responses={
            201: {"type": "object", "properties": {"token": {"type": "string"}}},
            401: {},
        },
        summary="Exchange username + password for an API key.",
    ),
    delete=extend_schema(
        operation_id="token_revoke",
        request=None,
        responses={204: None, 401: {}},
        summary="Revoke your API key (requires an authenticated request).",
    ),
)
class TokenView(APIView):
    """Self-service API keys, throttled so password guessing stays slow.

    POST creates (or re-issues) the key for valid credentials; the key is
    shown once and can be revoked with DELETE. There is deliberately no GET:
    a key can be read when created, after which only its presence is known.
    """

    # Credential exchange must work like the login page: no credentials
    # needed (scripts call it) and no CSRF dance (the submitted password
    # itself proves intent). Only TokenAuthentication is active here — never
    # the session — because DRF's SessionAuthentication would demand CSRF
    # tokens on both methods. Browsers revoke from the profile page's own
    # POST view, which does enforce CSRF.
    authentication_classes = [MultiSchemeTokenAuthentication]
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "token_auth"

    def get_permissions(self):
        if self.request.method == "DELETE":
            return [permissions.IsAuthenticated()]
        return []

    def post(self, request):
        serializer = APIKeySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            return Response(
                {"detail": "Invalid username or password."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key}, status=status.HTTP_201_CREATED)

    def delete(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
