"""
NPCI file design Amendment 1, option B (owner 2026-10-22/23): the wording
(description) of a response code used by the active rule set may be changed
through maker-checker -- it is shown on cases and sent to NPCI as
specifyother. The flag, a known numeric code, the notes and the rule links
stay locked. Seeding finds codes by flag + numeric code, so re-running
seed_rule_sets after a wording change creates no duplicate.
"""
from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.db import transaction

from exception_management import code_changes
from exception_management.services import MissingInput
from exception_management.tests.base import CaseTestBase
from rule_engine.models import CodeFlag, ResponseCode, RuleSetImmutableError
from rule_engine.seed_data import v3_2
from rule_engine.seeding import find_seeded_code

# The owner's target wording (Amendment 1). Only tests apply it here; on the
# real database the PM proposes it through the app's maker-checker.
OWNER_WORDING = {
    ("DRC", "102"): "Online Reversal",
    ("DRC", "103"): "Manually Credited",
    ("DRC", "104"): "Not Effected to CBS",
    ("RET", "115"): "Account does not exist",
    ("RRC", "501"): "Manually Credited",
    ("TCC", "102"): "Online Credited",
}


class LockedWordingTests(CaseTestBase):
    def setUp(self):
        super().setUp()
        self.m, self.chk = self.maker(), self.checker()

    def code(self, flag, number):
        return ResponseCode.objects.get(payment_module=self.module, flag=flag, code=number)

    def reword(self, rc, text):
        return code_changes.approve_code_change(
            code_changes.propose_code_change(rc, self.m, new_description=text, reason="owner wording"), self.chk)

    def test_owner_wording_applies_through_maker_checker(self):
        for (flag, number), text in OWNER_WORDING.items():
            with self.subTest(code=f"{flag} {number}"):
                rc = self.code(flag, number)
                self.assertTrue(rc.is_locked())
                rules_before = sorted(rc.rules_as_default.values_list("pk", flat=True))
                req = self.reword(rc, text)
                rc.refresh_from_db()
                self.assertEqual((rc.description, rc.code, rc.flag), (text, number, flag))
                self.assertEqual(sorted(rc.rules_as_default.values_list("pk", flat=True)), rules_before)
                self.assertEqual(req.events.last().changes, {"description": [req.old_description, text]})

    def test_wording_of_unknown_code_and_fill_in_together(self):
        closed = ResponseCode.objects.get(payment_module=self.module, flag=CodeFlag.RET, description="Account closed")
        code_changes.approve_code_change(code_changes.propose_code_change(
            closed, self.m, new_code="114", new_description="Account is closed", reason="NPCI list"), self.chk)
        closed.refresh_from_db()
        self.assertEqual((closed.code, closed.description), ("114", "Account is closed"))

    def test_what_stays_locked(self):
        rc = self.code("DRC", "102")
        with self.assertRaisesRegex(MissingInput, "only its wording"):
            code_changes.propose_code_change(rc, self.m, new_code="105", reason="x")  # known number
        with self.assertRaisesRegex(MissingInput, "only its wording"):
            code_changes.propose_code_change(rc, self.m, new_notes="x", reason="x")
        with self.assertRaisesRegex(MissingInput, "already exists"):  # unique per flag
            code_changes.propose_code_change(rc, self.m, new_description="Not effected to CBS", reason="x")
        rc.description = "Changed without approval"
        with self.assertRaises(RuleSetImmutableError), transaction.atomic():
            rc.save()
        with self.assertRaises(RuleSetImmutableError), transaction.atomic():
            ResponseCode.objects.filter(pk=rc.pk).update(description="x")
        rc.refresh_from_db()
        rc.notes, rc._approved_change = "x", True  # the model's own guard, under the approved path
        with self.assertRaisesRegex(RuleSetImmutableError, "notes"), transaction.atomic():
            rc.save()

    def test_reseeding_after_rewording_creates_no_duplicate(self):
        for (flag, number), text in OWNER_WORDING.items():
            self.reword(self.code(flag, number), text)
        closed = ResponseCode.objects.get(payment_module=self.module, flag=CodeFlag.RET, description="Account closed")
        self.reword(closed, "Closed account")  # a code whose number is still unknown
        before = ResponseCode.objects.count()
        out = StringIO()
        call_command("seed_rule_sets", stdout=out)
        self.assertEqual(ResponseCode.objects.count(), before)
        for (flag, number), text in OWNER_WORDING.items():
            self.assertEqual(self.code(flag, number).description, text)  # wording kept
        self.assertEqual(find_seeded_code(self.module, "RET", None, "Account closed").description, "Closed account")
        for flag, number, description, _s, _n in v3_2.RESPONSE_CODES:
            with self.subTest(code=description):
                self.assertIsNotNone(find_seeded_code(self.module, flag, number, description))

    def test_renamed_twice_unknown_code_still_found(self):
        closed = ResponseCode.objects.get(payment_module=self.module, flag=CodeFlag.RET, description="Account closed")
        self.reword(closed, "Closed account")
        closed.refresh_from_db()
        self.reword(closed, "Account has been closed")
        self.assertEqual(find_seeded_code(self.module, "RET", None, "Account closed").pk, closed.pk)
