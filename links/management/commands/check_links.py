"""Check living destinations and record per-link health badges."""

import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = (
        "Verify each link's destination still answers (HEAD, GET fallback) "
        "and record ok/error badges shown on the dashboard. Requires "
        "LINK_HEALTH_ENABLED=true. Schedule from cron/compose."
    )

    def add_arguments(self, parser):
        parser.add_argument("--user", help="Only check this username's links.")
        parser.add_argument("--code", help="Check a single short code.")
        parser.add_argument("--sleep", type=float, default=0.5,
                            help="Seconds between requests (default 0.5).")
        parser.add_argument("--limit", type=int, default=0,
                            help="Stop after N links (0 = all).")

    def handle(self, *args, **options):
        from django.conf import settings

        from links.health import look_up
        from links.models import Link

        if not settings.LINK_HEALTH_ENABLED:
            raise CommandError(
                "Link health checking is disabled (LINK_HEALTH_ENABLED=false)."
            )

        links = Link.objects.order_by("last_checked_at", "created_at")
        if options["user"]:
            links = links.filter(user__username=options["user"])
        if options["code"]:
            links = links.filter(code=options["code"])
        if options["limit"]:
            links = links[: options["limit"]]

        checked = {0: 0, 1: 0}
        for link in links.select_related("user"):
            status, detail = look_up(link.destination, settings.SITE_URL)
            link.health_status = status
            link.health_detail = detail
            link.last_checked_at = timezone.now()
            link.save(update_fields=["health_status", "health_detail", "last_checked_at", "updated_at"])
            checked[1 if status == "ok" else 0] += 1
            self.stdout.write(f"/{link.code} {status:5} {detail}")
            time.sleep(max(0.0, options["sleep"]))

        self.stdout.write(
            self.style.SUCCESS(
                f"Checked {checked[0] + checked[1]} link(s): "
                f"{checked[1]} ok, {checked[0]} error."
            )
        )
