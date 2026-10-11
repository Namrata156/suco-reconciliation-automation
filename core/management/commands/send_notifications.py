"""
Delivers the alert outbox (core.Notification): HELD / QUEUED rows are mailed
when mail is enabled (ALERTS_EMAIL_ENABLED + a mail server); failures retry
on later runs (3 attempts, backoff), then FAILED. Schedule every few minutes.

    manage.py send_notifications
    manage.py send_notifications --drop-held-older-than 2   # old held alerts -> DROPPED_STALE, not mailed
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Count

from core.models import Notification
from core.notifications import deliver_pending, drop_held_older_than


class Command(BaseCommand):
    help = "Sends pending alert emails from the Notification outbox."

    def add_arguments(self, parser):
        parser.add_argument("--drop-held-older-than", type=int, metavar="DAYS",
                            help="Mark HELD alerts older than DAYS as DROPPED_STALE instead of sending them")
        parser.add_argument("--limit", type=int, default=500)

    def handle(self, *args, **options):
        if options["drop_held_older_than"] is not None:
            n = drop_held_older_than(options["drop_held_older_than"])
            self.stdout.write(f"Dropped {n} held alert(s) older than {options['drop_held_older_than']} day(s)")
        r = deliver_pending(limit=options["limit"])
        if not r.enabled:
            self.stdout.write(self.style.WARNING(
                "Mail is not enabled (ALERTS_EMAIL_ENABLED=false or no EMAIL_HOST): nothing sent; alerts stay HELD"))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Sent {r.sent}, retry later {r.retry_later}, failed {r.failed}"))
            for e in r.errors[:20]:
                self.stdout.write(self.style.ERROR(f"  {e}"))
        counts = dict(Notification.objects.values_list("status").annotate(n=Count("id")))
        self.stdout.write("Outbox: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "empty"))
