"""Drain the click-webhook outbox.

Usage: manage.py flush_webhooks [--limit N]

POSTs pending webhook payloads to their owner's configured URL, oldest
first. Successful POSTs are deleted; failures increment the attempt
counter and are dropped after PendingWebhook.MAX_ATTEMPTS. Safe to run
from cron every minute.
"""

import urllib.request

from django.core.management.base import BaseCommand

from accounts.models import PendingWebhook, UserSettings


class Command(BaseCommand):
    help = "POST pending click-webhooks to their endpoints."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **options):
        limit = options["limit"]
        sent = dropped = 0
        pending = PendingWebhook.objects.select_related("user").order_by("created_at")[:limit]
        webhook_urls = dict(  # user_id -> webhook_url, one query
            UserSettings.objects.filter(webhook_url__gt="", user_id__in=[p.user_id for p in pending])
            .values_list("user_id", "webhook_url")
        )
        for row in pending:
            url = webhook_urls.get(row.user_id)
            if not url:
                dropped += 1
                row.delete()
                continue
            try:
                request = urllib.request.Request(
                    url,
                    data=row.payload.encode("utf-8"),
                    headers={"Content-Type": "application/json", "User-Agent": "Retie-Webhook/1.0"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    response.read()
                row.delete()
                sent += 1
            except Exception as exc:  # noqa: BLE001 — any failure is a retry
                row.attempts += 1
                row.last_error = str(exc)[:250]
                if row.attempts >= PendingWebhook.MAX_ATTEMPTS:
                    row.delete()
                    dropped += 1
                else:
                    row.save(update_fields=["attempts", "last_error"])

        self.stdout.write(f"webhooks sent={sent} dropped={dropped}")
