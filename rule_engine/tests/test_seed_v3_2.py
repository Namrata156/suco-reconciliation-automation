"""
Rule set v3.2 (docs/phase2_rule_table_v3_approved.txt "v3.2 AMENDMENT",
owner 2026-10-10): v3.1 with only R13 changed -- Pending Investigation,
MEDIUM, T+1 working day, no next-business-date-load TAT, no cross-day
check. Seeded by `seed_rule_sets` (fresh DBs) and data migration 0005
(existing DBs), both activating v3.2 and retiring v3.1.
"""
from __future__ import annotations

import importlib
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

from core.models import PaymentModule
from rule_engine.models import Rule, RuleSet, RuleSetStatus, TatType
from rule_engine.seed_data import v3_1, v3_2

DOC = Path(__file__).resolve().parents[2] / "docs" / "phase2_rule_table_v3_approved.txt"


class SeedDataTests(TestCase):
    def test_only_r13_differs_from_v3_1(self):
        self.assertEqual([r["code"] for r in v3_2.RULES], [r["code"] for r in v3_1.RULES])
        for old, new in zip(v3_1.RULES, v3_2.RULES):
            with self.subTest(rule=old["code"]):
                if old["code"] == "R13":
                    self.assertNotEqual(old, new)
                    self.assertEqual(old["condition"], new["condition"])  # same groups match
                else:
                    self.assertEqual(old, new)
        self.assertIsNot(v3_2.RULES[0], v3_1.RULES[0])  # copies: editing one can't change the other

    def test_r13_is_a_t_plus_1_review_item(self):
        r13 = next(r for r in v3_2.RULES if r["code"] == "R13")
        self.assertEqual((r13["status"], r13["priority"]), ("PENDING_INVESTIGATION", "MEDIUM"))
        self.assertEqual((r13["tat_type"], r13["tat_days"], r13["tat_day_basis"]), ("DAYS", 1, "WORKING"))
        self.assertNotIn("response_code", r13)
        self.assertFalse(r13.get("rbi_compensation_after_tat", False))
        self.assertIn("No automatic refund", r13["reason_template"])
        self.assertIn("not in this date's NPCI files", r13["reason_template"])
        self.assertFalse([r["code"] for r in v3_2.RULES if r["tat_type"] == "NEXT_BUSINESS_DATE_LOAD"])
        self.assertIn("NEXT_BUSINESS_DATE_LOAD", TatType.values)  # kept: retired v3.1 data uses it

    def test_counts_and_version_metadata(self):
        self.assertEqual(v3_2.EXPECTED_COUNTS, v3_1.EXPECTED_COUNTS)
        self.assertEqual(v3_2.EXPECTED_TOTALS, (77354, 79096, 76631))
        self.assertEqual((v3_2.VERSION_LABEL, v3_2.SUPERSEDES), ("v3.2", "v3.1"))
        self.assertEqual(str(v3_2.EFFECTIVE_FROM), "2026-10-10")
        self.assertIn("v3.2 AMENDMENT (2026-10-10)", DOC.read_text(encoding="utf-8"))


class CommandTests(TestCase):
    def setUp(self):
        self.out = StringIO()
        call_command("seed_rule_sets", stdout=self.out)
        self.upi = PaymentModule.objects.get(code="UPI")

    def test_v3_2_active_v3_1_retired(self):
        v31 = RuleSet.objects.get(version_label="v3.1")
        v32 = RuleSet.objects.get(version_label="v3.2")
        self.assertEqual((v31.status, v32.status), (RuleSetStatus.RETIRED, RuleSetStatus.ACTIVE))
        self.assertEqual(RuleSet.objects.filter(payment_module=self.upi, status="ACTIVE").count(), 1)
        self.assertEqual(v32.rules.count(), 24)
        self.assertIn("rule set v3.2 created and activated with 24 rules", self.out.getvalue())
        r13 = v32.rules.get(code="R13")
        self.assertEqual(
            (r13.status_category, r13.priority, r13.tat_type, r13.tat_days, r13.tat_day_basis),
            ("PENDING_INVESTIGATION", "MEDIUM", "DAYS", 1, "WORKING"),
        )
        self.assertEqual(v31.rules.get(code="R13").tat_type, "NEXT_BUSINESS_DATE_LOAD")  # history unchanged
        # Every other rule has the same content in both sets.
        fields = ("code", "evaluation_order", "name", "condition", "status_category", "action", "priority",
                  "response_code_id", "tat_type", "tat_days", "tat_day_basis", "tat_cutoff_time", "tat_note",
                  "rbi_compensation_after_tat", "reason_template", "source", "notes")
        old = list(v31.rules.exclude(code="R13").order_by("evaluation_order").values_list(*fields))
        new = list(v32.rules.exclude(code="R13").order_by("evaluation_order").values_list(*fields))
        self.assertEqual(old, new)

    def test_idempotent(self):
        out = StringIO()
        call_command("seed_rule_sets", stdout=out)
        self.assertNotIn("differs", out.getvalue())
        self.assertEqual((RuleSet.objects.count(), Rule.objects.count()), (2, 48))
        self.assertEqual(RuleSet.objects.get(status="ACTIVE").version_label, "v3.2")


class MigrationTests(TestCase):
    def _run(self, name, state_after):
        migration = importlib.import_module(f"rule_engine.migrations.{name}")
        state = MigrationExecutor(connection).loader.project_state(("rule_engine", state_after))
        migration.forwards(state.apps, None)

    def _v3_1(self):
        self._run("0002_seed_rule_table_v3_1", "0001_initial")

    def _v3_2(self):
        self._run("0005_seed_rule_table_v3_2", "0004_remove_next_day_recheck")

    def test_no_op_without_upi_module(self):
        self._v3_2()
        self.assertFalse(RuleSet.objects.exists())

    def test_activates_v3_2_and_retires_v3_1(self):
        PaymentModule.objects.create(code="UPI", name="UPI")
        self._v3_1()
        self._v3_2()
        v31 = RuleSet.objects.get(version_label="v3.1")
        v32 = RuleSet.objects.get(version_label="v3.2")
        self.assertEqual((v31.status, v32.status), ("RETIRED", "ACTIVE"))
        self.assertIsNotNone(v31.retired_at)
        self.assertEqual(v32.created_by, "migration rule_engine.0005")
        v32.validate_for_activation()
        for rule in v32.rules.all():
            rule.clean()
        self._v3_2()  # idempotent
        self.assertEqual((RuleSet.objects.count(), Rule.objects.count()), (2, 48))

    def test_does_not_retire_another_active_version(self):
        upi = PaymentModule.objects.create(code="UPI", name="UPI")
        self._v3_1()
        RuleSet._base_manager.filter(payment_module=upi).update(version_label="v9")
        self._v3_2()
        self.assertFalse(RuleSet.objects.filter(version_label="v3.2").exists())
        self.assertEqual(RuleSet.objects.get().status, "ACTIVE")
