"""
manage.py check_npci_file --date 2026-07-14 --compare SSKUPI_files/SSKUPI14072026.csv [--rule-defaults | --file-id N]

Compares an NPCI file with a reference file (ops' own), column by column
(npci_files.compare). Output carries counts and masked values only.

  --file-id N       compare a file already generated (its stored bytes);
  --rule-defaults   VERIFICATION ONLY: build the date's file as if every case
                    carried its rule's suggested reply and a Checker had
                    approved it, ignoring files already generated
                    (generator.plan_file(rule_defaults=True)).
  (neither)         build what the next file would hold now (Amendment 2:
                    each case's current reply -- approved, submitted, or the
                    rule's suggestion -- minus the cases held back).
Both build modes save nothing: they run in a READ ONLY transaction.
The filename column is written with the reference file's own name, so the
comparison is about the data.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from core.models import PaymentModule
from npci_files.compare import compare
from npci_files.generator import plan_file
from npci_files.models import NpciFile


class Command(BaseCommand):
    help = "Compare a generated NPCI file (or what would be generated) with a reference file"

    def add_arguments(self, parser):
        parser.add_argument("--module", default="UPI")
        parser.add_argument("--date", required=True, help="YYYY-MM-DD")
        parser.add_argument("--compare", required=True, help="Path of the reference file")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--rule-defaults", action="store_true")
        mode.add_argument("--file-id", type=int)
        parser.add_argument("--include-text", action="store_true", help="Also compare the specifyother column")

    def handle(self, *args, **opts):
        try:
            file_date = date.fromisoformat(opts["date"])
        except ValueError as exc:
            raise CommandError("--date must be YYYY-MM-DD") from exc
        reference_path = Path(opts["compare"])
        if not reference_path.is_file():
            raise CommandError(f"No file {reference_path}")
        reference = reference_path.read_bytes()
        module = PaymentModule.objects.get(code=opts["module"])

        if opts["file_id"]:
            npci_file = NpciFile.objects.get(pk=opts["file_id"], payment_module=module)
            generated = npci_file.content.encode("ascii")
            self.stdout.write(f"{npci_file.file_name}: {npci_file.row_count} rows, sha256 {npci_file.sha256}")
        else:
            with transaction.atomic():
                if connection.vendor == "postgresql":
                    with connection.cursor() as cursor:
                        cursor.execute("SET TRANSACTION READ ONLY")
                plan = plan_file(module, file_date, rule_defaults=opts["rule_defaults"])
                generated = plan.render(reference_path.name).encode("ascii")
            self.stdout.write(f"{'rule defaults' if opts['rule_defaults'] else 'next file now'}: "
                              f"{len(plan.rows)} rows {plan.source_counts() or ''}, {len(plan.excluded)} not "
                              f"included {plan.excluded_counts() or ''}, {len(plan.already_sent)} already in a file")
            for warning in plan.warnings:
                self.stdout.write(f"warning: {warning}")
        result = compare(generated, reference, ignore=() if opts["include_text"] else ("specifyother",))
        for line in result.lines():
            self.stdout.write(line)
