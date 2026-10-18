from django.contrib import admin

from .models import Link


@admin.register(Link)
class LinkAdmin(admin.ModelAdmin):
    list_display = ["code", "user", "destination", "status", "created_at"]
    search_fields = ["code", "destination", "user__username"]
    list_filter = ["is_active"]
