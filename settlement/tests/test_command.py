"""manage.py check_ntsl."""
from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

from settlement.models import CheckStatus, NtslCycleCheck
from settlement.tests.base import AUTH_LINES, D, DC_LINES, NtslTestBase, replace


class CheckNtslCommandTests(NtslTestBase):
    def call(self, *args):
        out = StringIO()
        call_command("check_ntsl", *args, stdout=out)
        return out.getvalue()

    def test_prints_one_row_per_cycle_and_stores_results(self):
        self.load_cycle("1C", AUTH_LINES)
        self.load_cycle("2C", replace(AUTH_LINES, "Final Settlement Amount", debit="400.00"))
        self.load_cycle("DC1", DC_LINES)
        out = self.call("--date", D.isoformat())
        self.assertIn("NTSL self-check UPI 2026-07-14 (mapping v1)", out)
        self.assertRegex(out, r"1C\s+PASS")
        self.assertRegex(out, r"2C\s+FAIL")
        self.assertIn("Final Settlement Amount (credit - debit): computed -401.03, NTSL -400.00", out)
        self.assertIn("12 cycles: 2 PASS, 1 FAIL, 9 MISSING", out)
        current = NtslCycleCheck.objects.filter(business_date=D, is_current=True)
        self.assertEqual(current.count(), 12)
        self.assertEqual(current.filter(status=CheckStatus.PASS).count(), 2)

    def test_errors_when_nothing_is_loaded_or_date_is_bad(self):
        with self.assertRaisesMessage(CommandError, "No NTSL lines loaded"):
            self.call("--date", "2026-01-01")
        with self.assertRaises(CommandError):
            self.call("--date", "14-07-2026")
        with self.assertRaises(CommandError):
            self.call("--date", D.isoformat(), "--module", "IMPS")
