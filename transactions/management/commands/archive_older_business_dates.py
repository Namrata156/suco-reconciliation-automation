"""
Moves staging rows for every business date older than the latest loaded
one into the History tables. Thin wrapper over
`transactions.services.archival.archive_older_business_dates` -- see that
module for the trigger rule and scope (staging only).
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from transactions.services.archival import archive_older_business_dates


class Command(BaseCommand):
    help = (
        "Archive staging rows whose file's business date is older than the latest "
        "LOADED business date for the payment module. Fact and NTSL settlement "
        "tables are not touched. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument("--payment-module", default="UPI")
        parser.add_argument("--batch-size", type=int, default=5000)
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would be archived; write nothing"
        )

    def handle(self, *args, **options):
        report = archive_older_business_dates(
            payment_module_code=options["payment_module"],
            dry_run=options["dry_run"],
            batch_size=options["batch_size"],
        )
        verb = "Would archive" if report.dry_run else "Archived"

        if report.latest_business_date is None:
            self.stdout.write(f"No LOADED files for {report.payment_module}; nothing to archive.")
            return

        self.stdout.write(
            f"{report.payment_module}: latest business date {report.latest_business_date} stays in staging."
        )
        for table, per_date in report.rows_by_table.items():
            dates = ", ".join(f"{d}: {n:,}" for d, n in per_date.items())
            self.stdout.write(f"  {table:<28} {dates}")
        for table, n in report.undated_rows_by_table.items():
            self.stdout.write(
                self.style.WARNING(f"  {table:<28} {n:,} rows skipped (file has no business date yet)")
            )
        self.stdout.write(self.style.SUCCESS(f"{verb} {report.total_rows:,} staging rows."))
