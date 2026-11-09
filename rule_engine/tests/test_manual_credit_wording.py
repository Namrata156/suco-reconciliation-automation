"""
Owner decision 2026-11-09: DRC 103 and RRC 501 read "Manual credit" (was
"Manually credited"), done in code: the seed data and data migration
rule_engine/0007. TCC 103 is not touched; files already generated keep their
stored text.
"""
from __future__ import annotations

import importlib
from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

from core.models import PaymentModule
from rule_engine.models import ResponseCode, RuleSet, RuleSetStatus
from rule_engine.seed_data import v3_1

migration = importlib.import_module("rule_engine.migrations.0007_rename_manual_credit_description")
NOTE = migration.NOTE


def _forwards():
    state = MigrationExecutor(connection).loader.project_state(("rule_engine", "0006_seed_rule_table_v3_3"))
    migration.forwards(state.apps, None)


def _backwards():
    state = MigrationExecutor(connection).loader.project_state(("rule_engine", "0007_rename_manual_credit_description"))
    migration.backwards(state.apps, None)


class ManualCreditWordingTests(TestCase):
    def setUp(self):
        call_command("seed_rule_sets", stdout=StringIO())
        self.upi = PaymentModule.objects.get(code="UPI")

    def code(self, flag, code):
        return ResponseCode.objects.get(payment_module=self.upi, flag=flag, code=code)

    def test_fresh_seed_has_the_new_wording_and_the_rules_resolve(self):
        self.assertEqual(v3_1.MANUAL_CREDIT_NOTE, NOTE)  # the seed and the migration write the same note
        for flag, code in (("DRC", "103"), ("RRC", "501")):
            rc = self.code(flag, code)
            self.assertEqual(rc.description, "Manual credit")
            self.assertTrue(rc.notes.endswith(NOTE))
        self.assertEqual(self.code("TCC", "103").description, "Credited manually post-reconciliation")
        active = RuleSet.objects.get(payment_module=self.upi, status=RuleSetStatus.ACTIVE)
        rules = {r.code: r for r in active.rules.select_related("response_code")}
        self.assertEqual(str(rules["R02"].response_code), "DRC 103 - Manual credit")
        for code in ("R06", "R06b"):
            self.assertEqual(str(rules[code].response_code), "RRC 501 - Manual credit")
        self.assertFalse(ResponseCode.objects.filter(description__iexact="manually credited").exists())

    def test_migration_renames_old_wording_any_casing_and_is_idempotent(self):
        drc, rrc = self.code("DRC", "103"), self.code("RRC", "501")
        base = ResponseCode._base_manager  # test-only: bypass the change-request guard
        base.filter(pk=drc.pk).update(description="Manually Credited",  # dev's wording since 2026-10-26
                                      notes="SOP wording: customer account reversed manually post-reconciliation.")
        base.filter(pk=rrc.pk).update(description="Manually credited",
                                      notes="SOP wording: manually credited to customer post reconciliation.")
        tcc_before = self.code("TCC", "103").description
        _forwards()
        _forwards()  # twice: no change, the note is not added again
        seeded = dict((c[0], c) for c in v3_1.RESPONSE_CODES if c[1] in ("103", "501") and c[0] in ("DRC", "RRC"))
        for flag, code in (("DRC", "103"), ("RRC", "501")):
            rc = self.code(flag, code)
            self.assertEqual((rc.description, rc.notes), (seeded[flag][2], seeded[flag][4]))  # = a fresh seed
        self.assertEqual(self.code("TCC", "103").description, tcc_before)
        _backwards()
        self.assertEqual(self.code("DRC", "103").description, "Manually credited")
        self.assertNotIn(NOTE, self.code("DRC", "103").notes)

    def test_other_wording_is_left_alone(self):
        drc = self.code("DRC", "103")
        ResponseCode._base_manager.filter(pk=drc.pk).update(description="Reversed by hand")
        _forwards()
        self.assertEqual(self.code("DRC", "103").description, "Reversed by hand")

    def test_seeding_again_finds_the_renamed_codes(self):
        count = ResponseCode.objects.count()
        out = StringIO()
        call_command("seed_rule_sets", stdout=out)
        self.assertEqual(ResponseCode.objects.count(), count)
        self.assertNotIn("differs", out.getvalue())
