"""Permanently delete links that have been in the trash for a while."""

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = (
        "Hard-delete links whose deleted_at is older than the retention "
        "window (default 30 days). Click history is removed with them. "
        "Run from cron or a scheduled `docker compose run app ...`."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Purge links trashed more than this many days ago (default 30).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print how many links would be purged without deleting.",
        )

    def handle(self, *args, **options):
        from links.models import Link

        cutoff = timezone.now() - timezone.timedelta(days=options["days"])
        trashed = Link.all_objects.filter(deleted_at__lt=cutoff)
        count = trashed.count()
        if options["dry_run"]:
            self.stdout.write(f"{count} link(s) trashed more than {options['days']} day(s) ago.")
            return
        for link in trashed:
            self.stdout.write(f"Purging /{link.code} (owner {link.user.username})")
            link.delete()
        self.stdout.write(self.style.SUCCESS(f"Purged {count} link(s)."))
