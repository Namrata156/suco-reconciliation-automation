"""
Fills the daily volume summary (Phase 6 R4a; transactions.models.volume) for
business dates loaded before it existed, or after files changed without a
new run. Every run fills its own date, so this is a one-off / catch-up.

    manage.py fill_daily_volume                       every date with facts
    manage.py fill_daily_volume --date 2026-07-14
    manage.py fill_daily_volume --from 2026-07-01 --to 2026-07-31
    manage.py fill_daily_volume --missing-only        only dates not summarised yet

Replaces the chosen dates' rows; safe to run again.
"""
from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from core.models import PaymentModule
from transactions.models import DailyVolumeDate, TransactionFact
from transactions.services.volume import refresh_daily_volume


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CommandError(f"Not a date (YYYY-MM-DD): {value}") from None


class Command(BaseCommand):
    help = "Fill the Phase 6 daily volume summary from the current facts."

    def add_arguments(self, parser):
        parser.add_argument("--payment-module", default="UPI")
        parser.add_argument("--date", help="One business date (YYYY-MM-DD)")
        parser.add_argument("--from", dest="date_from", help="First business date (YYYY-MM-DD)")
        parser.add_argument("--to", dest="date_to", help="Last business date (YYYY-MM-DD)")
        parser.add_argument("--missing-only", action="store_true", help="Skip dates already summarised")

    def handle(self, *args, **options):
        module = PaymentModule.objects.filter(code__iexact=options["payment_module"]).first()
        if module is None:
            raise CommandError(f"Unknown payment module {options['payment_module']!r}")
        dates = set(TransactionFact.objects.filter(payment_module=module).order_by()
                    .values_list("business_date", flat=True).distinct())
        if options["date"]:
            dates = {_date(options["date"])}
        if options["date_from"]:
            dates = {d for d in dates if d >= _date(options["date_from"])}
        if options["date_to"]:
            dates = {d for d in dates if d <= _date(options["date_to"])}
        if options["missing_only"]:
            dates -= set(DailyVolumeDate.objects.filter(payment_module=module).values_list("business_date",
                                                                                            flat=True))
        if not dates:
            self.stdout.write("No business date to fill.")
            return
        for d in sorted(dates):
            r = refresh_daily_volume(module, d, source="manage.py fill_daily_volume")
            self.stdout.write(f"{module.code} {d}: {r.facts:,} rows in {r.rows} summary rows")
        self.stdout.write(self.style.SUCCESS(f"Filled {len(dates)} business date(s)."))
