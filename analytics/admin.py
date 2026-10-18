from django.contrib import admin

from .models import ClickEvent


@admin.register(ClickEvent)
class ClickEventAdmin(admin.ModelAdmin):
    list_display = ["link", "created_at", "device", "referrer"]
    list_filter = ["device"]
    raw_id_fields = ["link"]
    date_hierarchy = "created_at"
