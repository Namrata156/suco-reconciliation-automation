"""
Seeds the Karnataka 2026 bank holidays (22 rows, RBI Bengaluru regional
office) into BankHoliday. Idempotent and create-only (see core.seeding).

Existing DBs and the test DB already get the same rows from data migration
core/0005: holidays are bank-wide (no PaymentModule), so unlike the
settlement-cycle seed there is no test-baseline collision to avoid. The
command exists for a DB where the rows were removed, and as the pattern for
adding next year's list.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from core.seed_data import bank_holidays_karnataka_2026 as data
from core.seeding import seed_bank_holidays


class Command(BaseCommand):
    help = "Seeds Karnataka 2026 bank holidays (RBI Bengaluru) for the working-day calendar. Idempotent."

    def handle(self, *args, **options):
        created, existing, warnings = seed_bank_holidays(data)
        for warning in warnings:
            self.stdout.write(self.style.WARNING(warning))
        self.stdout.write(
            self.style.SUCCESS(
                f"Bank holidays {data.REGION} {data.YEAR}: {created} created, {existing} already present "
                "(not yet confirmed by ops)."
            )
        )
