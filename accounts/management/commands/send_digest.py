"""Weekly digest email: 7-day link activity for opted-in users.

Cron (Mondays 09:00):
    0 9 * * 1 cd /srv/retie && .venv/bin/python manage.py send_digest
Docker:
    0 9 * * 1 docker compose run --rm app python manage.py send_digest
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Send the weekly activity digest to opted-in users."

    def handle(self, *args, **options):
        User = get_user_model()
        week_ago = timezone.now() - timedelta(days=7)

        recipients = (
            User.objects.filter(user_settings__weekly_digest=True)
            .exclude(email="")
            .prefetch_related("user_settings")
        )
        sent = skipped = 0
        for user in recipients:
            # Non-bot clicks in the last 7 days per link, top 5 first.
            counts = []
            for link in user.links.all():
                count = link.clicks.filter(is_bot=False, created_at__gte=week_ago).count()
                counts.append((link, count))
            counts.sort(key=lambda pair: pair[1], reverse=True)
            top = [pair for pair in counts[:5] if pair[1] > 0]
            total = sum(pair[1] for pair in counts)

            if total == 0:
                skipped += 1
                continue

            from django.conf import settings

            lines = [
                f"Hi {user.username},",
                "",
                "Your Retie week:",
            ]
            for link, count in top:
                title = link.title or f"/{link.code}"
                lines.append(f"  {title:<32} {count:>6} clicks")
            lines.extend(["", f"Total non-bot clicks in the last 7 days: {total}"])
            lines.append(f"Open your dashboard: {settings.SITE_URL}/dashboard/")

            mail.send_mail(
                subject=f"Retie weekly digest: {total} clicks",
                message="\n".join(lines),
                from_email=None,  # DEFAULT_FROM_EMAIL
                recipient_list=[user.email],
            )
            sent += 1

        self.stdout.write(f"Digests sent: {sent} (skipped {skipped} with no clicks).")
