from rest_framework import serializers

from .models import Link


class LinkSerializer(serializers.ModelSerializer):
    short_url = serializers.SerializerMethodField()
    click_count = serializers.IntegerField(read_only=True, default=0)
    status = serializers.CharField(read_only=True)
    last_click_at = serializers.DateTimeField(read_only=True)
    tags = serializers.SerializerMethodField()
    # Write-only gate password: `password` sets a new hash, `remove_password`
    # clears one. Reads never expose whether a link is protected.
    password = serializers.CharField(
        write_only=True, required=False, allow_blank=True, trim_whitespace=True
    )
    remove_password = serializers.BooleanField(write_only=True, required=False)

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
            "permanent",
            "public_stats",
            "password",
            "remove_password",
            "password",
            "remove_password",
            "created_at",
            "updated_at",
            "click_count",
            "last_click_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        # DRF's BooleanField defaults to False, which would silently create
        # *inactive* links when is_active is omitted. Mirror the model default.
        extra_kwargs = {"is_active": {"default": True}, "permanent": {"default": False}, "public_stats": {"default": False}}

    def get_tags(self, obj: Link) -> list:
        return list(obj.tags.order_by("name").values_list("name", flat=True))

    def create(self, validated_data):
        password = validated_data.pop("password", "") or ""
        remove = validated_data.pop("remove_password", False)
        link = super().create(validated_data)
        self._apply_password(link, password, remove)
        return link

    def update(self, instance, validated_data):
        password = validated_data.pop("password", "") or ""
        remove = validated_data.pop("remove_password", False)
        link = super().update(instance, validated_data)
        self._apply_password(link, password, remove)
        return link

    @staticmethod
    def _apply_password(link: Link, password: str, remove: bool) -> None:
        from django.contrib.auth.hashers import make_password

        if password:
            link.password_hash = make_password(password)
        elif remove:
            link.password_hash = ""
        else:
            return
        link.save(update_fields=["password_hash", "updated_at"])

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
