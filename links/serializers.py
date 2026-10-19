from rest_framework import serializers

from .models import Link


class LinkSerializer(serializers.ModelSerializer):
    short_url = serializers.SerializerMethodField()
    click_count = serializers.IntegerField(read_only=True, default=0)
    status = serializers.CharField(read_only=True)
    last_click_at = serializers.DateTimeField(read_only=True)
    tags = serializers.SerializerMethodField()

    class Meta:
        model = Link
        fields = [
            "id",
            "code",
            "short_url",
            "destination",
            "title",
            "is_active",
            "expires_at",
            "status",
            "tags",
            "created_at",
            "updated_at",
            "click_count",
            "last_click_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_tags(self, obj: Link) -> list:
        return list(obj.tags.order_by("name").values_list("name", flat=True))

    def get_short_url(self, obj: Link) -> str:
        request = self.context.get("request")
        from django.conf import settings

        return f"{settings.SITE_URL}/{obj.code}"

    def validate_destination(self, value):
        from .models import validate_destination

        return validate_destination(value)

    def validate_code(self, value):
        from .models import validate_alias

        code = (value or "").strip().lower()
        if not code:
            return ""
        return validate_alias(code)

    def validate_expires_at(self, value):
        from django.utils import timezone

        if value and value <= timezone.now():
            raise serializers.ValidationError("Expiration must be in the future.")
        return value


class ClickPointSerializer(serializers.Serializer):
    """One day of a clicks-over-time series."""

    date = serializers.DateField()
    clicks = serializers.IntegerField()


class ReferrerSerializer(serializers.Serializer):
    referrer = serializers.CharField()
    count = serializers.IntegerField()


class LinkAnalyticsResponseSerializer(serializers.Serializer):
    link = LinkSerializer()
    total_clicks = serializers.IntegerField()
    clicks_over_time = ClickPointSerializer(many=True)
    referrers = ReferrerSerializer(many=True)
    devices = serializers.DictField(child=serializers.IntegerField())


class DashboardStatsSerializer(serializers.Serializer):
    total_links = serializers.IntegerField()
    active_links = serializers.IntegerField()
    total_clicks = serializers.IntegerField()
    clicks_last_30_days = serializers.IntegerField()
    clicks_over_time = ClickPointSerializer(many=True)
    last_click = serializers.DateTimeField(allow_null=True)


class ProfileSerializer(serializers.Serializer):
    username = serializers.CharField()
    email = serializers.EmailField()
    date_joined = serializers.DateTimeField()
    has_api_token = serializers.BooleanField()
