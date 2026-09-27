"""
UPI SettlementCycle reference data per NPCI "Annexure I -- Revised
Settlement Cycles" (approved by the project owner 2026-09-27): the
`seed_settlement_cycles` command (fresh DBs) and
core/migrations/0003_correct_upi_settlement_cycles.py (existing DBs) must
hold the same values, and those values must match the circular.
"""
from __future__ import annotations

import importlib
from datetime import time
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from core.management.commands.seed_settlement_cycles import UPI_SETTLEMENT_CYCLES
from core.models import PaymentModule, SettlementCycle, SettlementCycleType

# Annexure I, verbatim: cycle, from, to, RTGS settlement time, content.
_ANNEXURE_I = [
    ("1C", "21:00", "00:00", "04:00", "AUTH"),
    ("2C", "00:00", "05:00", "07:00", "AUTH"),
    ("3C", "05:00", "07:00", "09:00", "AUTH"),
    ("4C", "07:00", "09:00", "11:00", "AUTH"),
    ("5C", "09:00", "11:00", "13:00", "AUTH"),
    ("6C", "11:00", "13:00", "15:00", "AUTH"),
    ("7C", "13:00", "15:00", "17:00", "AUTH"),
    ("8C", "15:00", "17:00", "19:00", "AUTH"),
    ("9C", "17:00", "19:00", "21:00", "AUTH"),
    ("10C", "19:00", "21:00", "23:00", "AUTH"),
    ("DC1", "00:00", "16:00", "17:00", "DISPUTE"),
    ("DC2", "16:00", "00:00", "04:00", "DISPUTE"),
]


def _t(hhmm: str) -> time:
    return time.fromisoformat(hhmm)


class SettlementCycleSeedTests(TestCase):
    def test_seed_creates_all_12_cycles_matching_annexure_i(self):
        call_command("seed_settlement_cycles", stdout=StringIO())

        rows = {c.cycle_code: c for c in SettlementCycle.objects.filter(payment_module__code="UPI")}
        self.assertEqual(len(rows), 12)
        for code, start, end, rtgs, cycle_type in _ANNEXURE_I:
            cycle = rows[code]
            self.assertEqual(
                (cycle.cycle_type, cycle.start_time, cycle.end_time, cycle.rtgs_settlement_time),
                (cycle_type, _t(start), _t(end), _t(rtgs)),
                code,
            )

    def test_seed_is_idempotent_and_never_modifies_existing_rows(self):
        upi = PaymentModule.objects.create(code="UPI", name="UPI")
        SettlementCycle.objects.create(
            payment_module=upi,
            cycle_code="1C",
            cycle_type=SettlementCycleType.AUTH,
            start_time=time(0, 0),
            end_time=time(2, 0),
        )
        out = StringIO()
        call_command("seed_settlement_cycles", stdout=out)
        call_command("seed_settlement_cycles", stdout=StringIO())

        self.assertEqual(SettlementCycle.objects.count(), 12)
        one_c = SettlementCycle.objects.get(cycle_code="1C")
        self.assertEqual((one_c.start_time, one_c.end_time), (time(0, 0), time(2, 0)))
        self.assertIn("UPI-1C differs from Annexure I", out.getvalue())
        self.assertIn("11 created, 1 already present", out.getvalue())

    def test_migration_0003_values_match_seed_command(self):
        migration = importlib.import_module("core.migrations.0003_correct_upi_settlement_cycles")
        seed = {code: (str(v[0]), *v[1:]) for code, v in UPI_SETTLEMENT_CYCLES.items()}
        self.assertEqual(migration.ANNEXURE_I_UPI_CYCLES, seed)
