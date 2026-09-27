"""
Seeds UPI's 12 `SettlementCycle` rows from the NPCI circular's "Annexure I
-- Revised Settlement Cycles" (approved by the project owner 2026-09-27):
1C-10C are AUTH-only settlement cycles, DC1/DC2 are dispute-only.

Run once per fresh DB, before `seed_file_type_configs` (whose cycle-wise
configs reference these cycle codes via `cycle_set`). A management command
rather than a data migration for the same reason as `seed_file_type_configs`
(see its docstring): inserts from a migration would land in the test DB
baseline and collide with tests that create their own
`PaymentModule(code="UPI")`. Existing DBs were corrected to these values by
core/migrations/0003_correct_upi_settlement_cycles.py.

Idempotent and create-only, matching `seed_file_type_configs`: existing
rows are never modified (cycle rows are admin-editable). Any existing row
that differs from Annexure I is reported as a warning so drift is visible.
"""
from __future__ import annotations

from datetime import time

from django.core.management.base import BaseCommand

from core.models import PaymentModule, SettlementCycle, SettlementCycleType

AUTH = SettlementCycleType.AUTH
DISPUTE = SettlementCycleType.DISPUTE

# cycle_code -> (cycle_type, start_time, end_time, rtgs_settlement_time).
# Annexure I "Duration" column is implied by start/end (e.g. 1C 21:00-00:00
# = 3h, DC1 00:00-16:00 = 16h); end < start means the window crosses
# midnight.
UPI_SETTLEMENT_CYCLES = {
    "1C": (AUTH, time(21, 0), time(0, 0), time(4, 0)),
    "2C": (AUTH, time(0, 0), time(5, 0), time(7, 0)),
    "3C": (AUTH, time(5, 0), time(7, 0), time(9, 0)),
    "4C": (AUTH, time(7, 0), time(9, 0), time(11, 0)),
    "5C": (AUTH, time(9, 0), time(11, 0), time(13, 0)),
    "6C": (AUTH, time(11, 0), time(13, 0), time(15, 0)),
    "7C": (AUTH, time(13, 0), time(15, 0), time(17, 0)),
    "8C": (AUTH, time(15, 0), time(17, 0), time(19, 0)),
    "9C": (AUTH, time(17, 0), time(19, 0), time(21, 0)),
    "10C": (AUTH, time(19, 0), time(21, 0), time(23, 0)),
    "DC1": (DISPUTE, time(0, 0), time(16, 0), time(17, 0)),
    "DC2": (DISPUTE, time(16, 0), time(0, 0), time(4, 0)),
}


class Command(BaseCommand):
    help = (
        "Seeds UPI's 12 SettlementCycle rows (1C-10C AUTH, DC1/DC2 disputes) per "
        "NPCI Annexure I. Idempotent -- existing rows are never modified; "
        "differences from Annexure I are reported as warnings."
    )

    def handle(self, *args, **options):
        upi, _ = PaymentModule.objects.get_or_create(code="UPI", defaults={"name": "UPI"})

        created, existing = 0, 0
        for code, (cycle_type, start, end, rtgs) in UPI_SETTLEMENT_CYCLES.items():
            expected = dict(
                cycle_type=cycle_type, start_time=start, end_time=end, rtgs_settlement_time=rtgs
            )
            cycle, was_created = SettlementCycle.objects.get_or_create(
                payment_module=upi, cycle_code=code, defaults=expected
            )
            if was_created:
                created += 1
                continue
            existing += 1
            drift = {
                field: (getattr(cycle, field), value)
                for field, value in expected.items()
                if getattr(cycle, field) != value
            }
            if drift:
                self.stdout.write(
                    self.style.WARNING(
                        f"UPI-{code} differs from Annexure I (left unchanged): "
                        + ", ".join(f"{f} is {have}, expected {want}" for f, (have, want) in drift.items())
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"SettlementCycle seed complete: {created} created, {existing} already present."
            )
        )
