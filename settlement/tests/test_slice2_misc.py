"""Slice 2: O4 self-check, role permissions on the settlement tables,
read-only admin screens, and the reconcile_settlement command."""
from __future__ import annotations

from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.urls import reverse

from exception_management.roles import Role
from settlement.models import CheckStatus
from settlement.selfcheck import check_ntsl_date
from settlement.tests.base import AUTH_LINES, NtslTestBase, replace
from settlement.tests.recon_base import D, SettlementTestBase

SETTLEMENT_MODELS = ("ntslcategorymapversion", "ntslcategorymapentry", "ntslmappingevent", "ntslcyclecheck",
                     "settlementresult", "settlementcycleresult", "settlementtolerance", "settlementtoleranceevent",
                     "settlementcase")


class O4SelfCheckTests(NtslTestBase):
    def test_switching_fee_count_must_equal_remitter_count(self):
        self.load_cycle("1C", [(d, 2 if d == "Remitter U3 Approved NPCI Switching Fee" else n, dr, cr)
                               for d, n, dr, cr in AUTH_LINES])
        check = check_ntsl_date(self.module, D).checks[0]
        self.assertEqual(check.status, CheckStatus.FAIL)
        self.assertTrue(any("O4" in f and "count 2" in f and "RB 3" in f for f in check.failures), check.failures)

    def test_interchange_fee_count_is_not_checked(self):
        self.load_cycle("1C", [(d, 99 if d == "Remitter U3 Approved Fee" else n, dr, cr) for d, n, dr, cr in AUTH_LINES])
        self.assertEqual(check_ntsl_date(self.module, D).checks[0].status, CheckStatus.PASS)

    def test_rb_counts_with_the_remitter(self):
        lines = replace(AUTH_LINES, "Remitter U3 Approved Transaction Amount", debit="900.00")
        lines = [(d, 2 if d == "Remitter U3 Approved Transaction Amount" else n, dr, cr) for d, n, dr, cr in lines]
        lines.insert(3, ("Remitter U3 RB Approved Transaction Amount", 1, "100.00", "0"))
        self.load_cycle("1C", lines)
        self.assertEqual(check_ntsl_date(self.module, D).checks[0].status, CheckStatus.PASS)


class PermissionAndAdminTests(SettlementTestBase):
    def test_every_role_may_view_settlement_tables(self):
        for role in Role.ALL:
            codenames = set(Group.objects.get(name=role).permissions.filter(content_type__app_label="settlement")
                            .values_list("codename", flat=True))
            for model in SETTLEMENT_MODELS:
                self.assertIn(f"view_{model}", codenames, (role, model))
            self.assertFalse({c for c in codenames if c.startswith(("add_", "change_", "delete_"))}, role)
        admin_perms = set(Group.objects.get(name=Role.ADMINISTRATOR).permissions.values_list("codename", flat=True))
        checker_perms = set(Group.objects.get(name=Role.CHECKER).permissions.values_list("codename", flat=True))
        self.assertIn("propose_tolerance_change", admin_perms)
        self.assertIn("approve_tolerance_change", checker_perms)

    def test_admin_screens_read_only(self):
        self.build_day()
        result = self.reconcile().result
        auditor = self.user(Role.AUDITOR)
        self.client.force_login(auditor)
        for model in ("settlementresult", "settlementtolerance", "ntslcyclecheck"):
            r = self.client.get(reverse(f"admin:settlement_{model}_changelist"))
            self.assertEqual(r.status_code, 200, model)
        r = self.client.get(reverse("admin:settlement_settlementresult_change", args=[result.pk]))
        self.assertContains(r, "TALLIED")
        self.assertNotContains(r, 'name="_save"')
        self.assertEqual(self.client.get(reverse("admin:settlement_settlementtolerance_add")).status_code, 403)


class CommandTests(SettlementTestBase):
    def test_prints_cycle_table_and_status(self):
        self.build_day()
        out = StringIO()
        call_command("reconcile_settlement", "--date", D.isoformat(), stdout=out)
        text = out.getvalue()
        self.assertIn("TALLIED", text)
        self.assertIn("1C     REMITTER", text)
        self.assertIn("SOP check", text)

    def test_not_ready_prints_reasons(self):
        out = StringIO()
        call_command("reconcile_settlement", "--date", "2026-07-20", stdout=out)
        self.assertIn("NOT_READY", out.getvalue())
        self.assertIn("No NTSL lines", out.getvalue())
