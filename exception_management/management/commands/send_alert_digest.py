"""
Records the daily digest alerts (Makers: open / overdue HIGH, overdue, due
today, their own cases; Checkers: awaiting approval, needs review) in the
outbox; `send_notifications` mails them. At most one digest per user per day.
Schedule daily at ALERT_DIGEST_TIME (default 09:30 IST).

    manage.py send_alert_digest --dry-run    # counts per role, nothing recorded
    manage.py send_alert_digest
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from exception_management.alerts import send_digest
from exception_management.roles import Role


class Command(BaseCommand):
    help = "Records the daily exception-case digest for Makers and Checkers."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print the counts per role; record nothing")

    def handle(self, *args, **options):
        s = send_digest(dry_run=options["dry_run"])
        verb = "Would record" if options["dry_run"] else "Recorded"
        for role in (Role.MAKER, Role.CHECKER):
            counts = dict(s[role])
            n = counts.pop("recipients")
            self.stdout.write(f"{verb} {role} digest for {n} recipient(s) ({s['date']}): "
                              + ", ".join(f"{k} {v}" for k, v in counts.items()))
