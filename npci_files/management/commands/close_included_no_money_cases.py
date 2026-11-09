"""
manage.py close_included_no_money_cases [--module UPI] [--file-id N ...] [--dry-run]

Owner decision 2026-11-09: a no-money case (npci_files.auto_close.AUTO_CLOSE_REPLIES:
R01 DRC 102, R03 DRC 104, R04a TCC 102) closes by itself once its reply is in a
generated NPCI file. Files generated since then do it at generation; this
command does the same for the cases of files generated BEFORE (never run
automatically).

For every file of the module (oldest first; or only --file-id), the same
checks as at generation (auto_close.skip_reason): the row is the rule's
no-money reply, the case still carries it, not flagged for review, not held
back, status OPEN / IN_PROGRESS / PENDING_APPROVAL / APPROVED. Each closed
case gets a CLOSED_BY_SYSTEM event naming the file, with this command as the
source. One transaction per file; running it again closes nothing more.

--dry-run: the same checks and counts; nothing is written.
"""
from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from core.models import PaymentModule
from npci_files import auto_close
from npci_files.models import NpciFile

SOURCE = "manage.py close_included_no_money_cases"


def _fmt(counter) -> str:
    return ", ".join(f"{k}: {v}" for k, v in sorted(counter.items()))


class Command(BaseCommand):
    help = "Close the no-money cases (R01/R03/R04a) already included in NPCI files generated earlier"

    def add_arguments(self, parser):
        parser.add_argument("--module", default="UPI")
        parser.add_argument("--file-id", type=int, action="append", dest="file_ids",
                            help="Only this file (repeatable)")
        parser.add_argument("--dry-run", action="store_true", help="Report what would be closed; write nothing")

    def handle(self, *args, **opts):
        module = PaymentModule.objects.filter(code=opts["module"]).first()
        if module is None:
            raise CommandError(f"No payment module {opts['module']!r}")
        files = NpciFile.objects.filter(payment_module=module).order_by("generated_at", "pk")
        if opts["file_ids"]:
            files = files.filter(pk__in=opts["file_ids"])
            missing = set(opts["file_ids"]) - set(files.values_list("pk", flat=True))
            if missing:
                raise CommandError(f"No {module.code} NPCI file with id {', '.join(map(str, sorted(missing)))}")
        dry = opts["dry_run"]
        verb = "would close" if dry else "closed"
        rules = ", ".join(f"{r} {f} {c}" for r, (f, c) in auto_close.AUTO_CLOSE_REPLIES.items())
        self.stdout.write(f"{'DRY RUN, nothing is written. ' if dry else ''}No-money replies: {rules}")
        by_rule, by_status, skipped = Counter(), Counter(), Counter()
        for f in files:
            result = auto_close.close_included_cases(f, source=SOURCE, dry_run=dry)  # one transaction per file
            rule_counts = Counter(c.rule_code for c in result.closed)
            by_rule.update(rule_counts)
            by_status.update(result.status_before)
            skipped.update(result.skipped)
            line = f"  {f.file_name} (id {f.pk}, {f.row_count} rows): {verb} {len(result.closed)}"
            if result.closed:
                line += f" [{_fmt(rule_counts)}; status before: {_fmt(result.status_before)}]"
            if result.skipped:
                line += f"; not closed: {_fmt(result.skipped)}"
            self.stdout.write(line)
        self.stdout.write(f"Total {verb}: {sum(by_rule.values())}. By rule: {_fmt(by_rule) or '-'}. "
                          f"By status before: {_fmt(by_status) or '-'}.")
        self.stdout.write(f"Not closed, all files: {_fmt(skipped) or '-'}")
