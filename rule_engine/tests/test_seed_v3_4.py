"""
Rule set v3.4 (owner decision 2026-11-11, CBS credit file plan): v3.3 plus
R07r (Refund Reversal Confirmation, SSK = Remitter -> Manual Credit
Required, credited through the rev file, no SSKUPI reply), placed just
before R07a; R06's action text says the RRC 501 reply comes first. Nothing
else changes. Seeded by `seed_rule_sets` (fresh DBs) and data migration 0008
(existing DBs), both activating v3.4 and retiring v3.3.
"""
from __future__ import annotations

import importlib
from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

from core.models import PaymentModule
from exception_management.actions import DEFAULT_ACTION_BY_RULE, ActionType
from rule_engine.models import Rule, RuleSet, RuleSetStatus
from rule_engine.seed_data import v3_3, v3_4

RULE_FIELDS = ("code", "name", "condition", "status_category", "action", "priority", "response_code_id",
               "tat_type", "tat_days", "tat_day_basis", "tat_cutoff_time", "rbi_compensation_after_tat",
               "reason_template", "source", "notes")


class SeedDataTests(TestCase):
    def test_r07r_sits_just_before_r07a(self):
        codes = [r["code"] for r in v3_4.RULES]
        self.assertEqual(codes, [c for c in (r["code"] for r in v3_3.RULES) for c in
                                 (("R07r", c) if c == "R07a" else (c,))])
        self.assertEqual(codes.index("R07r") + 1, codes.index("R07a"))
        self.assertLess(codes.index("R06"), codes.index("R07r"))

    def test_r07r(self):
        r = next(r for r in v3_4.RULES if r["code"] == "R07r")
        self.assertEqual(r["condition"], {"all": [
            {"fact": "adjustment.ssk_side", "op": "eq", "value": True},
            {"fact": "adjustment.adjtype", "op": "in", "value": ["Refund Reversal Confirmation"]}]})
        self.assertEqual((r["status"], r["priority"], r["tat_type"], r["tat_days"], r["tat_day_basis"]),
                         ("MANUAL_CREDIT_REQUIRED", "HIGH", "DAYS", 1, "CALENDAR"))
        self.assertNotIn("response_code", r)  # no SSKUPI reply
        self.assertIn("credit file", r["action"])
        self.assertEqual(DEFAULT_ACTION_BY_RULE["R07r"], ActionType.REFUND_CUSTOMER)

    def test_only_r06_wording_changes_otherwise(self):
        old = {r["code"]: r for r in v3_3.RULES}
        for new in v3_4.RULES:
            if new["code"] == "R07r":
                continue
            with self.subTest(rule=new["code"]):
                if new["code"] == "R06":
                    changed = ("action", "notes", "status")
                    self.assertEqual({k: v for k, v in new.items() if k not in changed},
                                     {k: v for k, v in old["R06"].items() if k not in changed})
                    self.assertEqual((old["R06"]["status"], new["status"]),
                                     ("MANUAL_CREDIT_REQUIRED", "RRC_REPLY_CREDIT_ON_CONFIRMATION"))
                    self.assertIn("RRC 501", new["action"])
                    self.assertIn("R07r", new["action"])
                else:
                    self.assertEqual(new, old[new["code"]])
        self.assertIsNot(v3_4.RULES[0], v3_3.RULES[0])
        self.assertNotIn("R07r", [r["code"] for r in v3_3.RULES])  # v3.3 frozen

    def test_metadata(self):
        self.assertEqual((v3_4.VERSION_LABEL, v3_4.SUPERSEDES), ("v3.4", "v3.3"))
        self.assertEqual(str(v3_4.EFFECTIVE_FROM), "2026-11-11")
        self.assertLessEqual(len(v3_4.SOURCE_DOCUMENT), 255)
        self.assertEqual(v3_4.RESPONSE_CODES, v3_3.RESPONSE_CODES)
        self.assertEqual(v3_4.ORACLE_ALIASES, {"R07r": "R07a"})
        self.assertEqual(v3_4.EXPECTED_COUNTS, v3_3.EXPECTED_COUNTS)


class CommandTests(TestCase):
    def setUp(self):
        self.out = StringIO()
        call_command("seed_rule_sets", stdout=self.out)

    def test_v3_4_active_v3_3_retired(self):
        v33, v34 = RuleSet.objects.get(version_label="v3.3"), RuleSet.objects.get(version_label="v3.4")
        self.assertEqual((v33.status, v34.status), (RuleSetStatus.RETIRED, RuleSetStatus.ACTIVE))
        self.assertIn("rule set v3.4 created and activated with 25 rules", self.out.getvalue())
        r07r = v34.rules.get(code="R07r")
        self.assertIsNone(r07r.response_code)
        self.assertEqual(r07r.evaluation_order + 1, v34.rules.get(code="R07a").evaluation_order)
        same = [c for c in v33.rules.values_list("code", flat=True) if c != "R06"]
        for code in same:
            with self.subTest(rule=code):
                self.assertEqual(Rule.objects.filter(rule_set=v33, code=code).values(*RULE_FIELDS[1:]).get(),
                                 Rule.objects.filter(rule_set=v34, code=code).values(*RULE_FIELDS[1:]).get())
        self.assertEqual(v34.rules.get(code="R06").response_code_id, v33.rules.get(code="R06").response_code_id)


class MigrationTests(TestCase):
    def _run(self, name, state_after):
        migration = importlib.import_module(f"rule_engine.migrations.{name}")
        state = MigrationExecutor(connection).loader.project_state(("rule_engine", state_after))
        migration.forwards(state.apps, None)

    def _chain(self):
        self._run("0002_seed_rule_table_v3_1", "0001_initial")
        self._run("0005_seed_rule_table_v3_2", "0004_remove_next_day_recheck")
        self._run("0006_seed_rule_table_v3_3", "0005_seed_rule_table_v3_2")

    def _v3_4(self):
        self._run("0008_seed_rule_table_v3_4", "0007_rename_manual_credit_description")

    def test_no_op_without_upi_module(self):
        self._v3_4()
        self.assertFalse(RuleSet.objects.exists())

    def test_activates_v3_4_and_retires_v3_3(self):
        PaymentModule.objects.create(code="UPI", name="UPI")
        self._chain()
        self._v3_4()
        v33, v34 = RuleSet.objects.get(version_label="v3.3"), RuleSet.objects.get(version_label="v3.4")
        self.assertEqual((v33.status, v34.status), ("RETIRED", "ACTIVE"))
        self.assertEqual(v34.created_by, "migration rule_engine.0008")
        v34.validate_for_activation()
        for rule in v34.rules.all():
            rule.clean()
        self.assertEqual([r.code for r in v34.rules.order_by("evaluation_order")], [r["code"] for r in v3_4.RULES])
        self.assertIsNone(v34.rules.get(code="R07r").response_code_id)
        self._v3_4()  # idempotent
        self.assertEqual((RuleSet.objects.count(), Rule.objects.count()), (4, 97))

    def test_does_not_retire_another_active_version(self):
        upi = PaymentModule.objects.create(code="UPI", name="UPI")
        self._chain()
        RuleSet._base_manager.filter(payment_module=upi, status="ACTIVE").update(version_label="v9")
        self._v3_4()
        self.assertFalse(RuleSet.objects.filter(version_label="v3.4").exists())
