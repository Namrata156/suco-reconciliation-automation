"""
Rule set v3.3 (docs/phase2_rule_table_v3_approved.txt "v3.3 AMENDMENT",
owner 2026-10-23): v3.2 with every WORKING deadline basis changed to
CALENDAR, same days and cutoff times; nothing else changes. Seeded by
`seed_rule_sets` (fresh DBs) and data migration 0006 (existing DBs), both
activating v3.3 and retiring v3.2.
"""
from __future__ import annotations

import importlib
from datetime import time
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

from core.models import PaymentModule
from rule_engine.models import ResponseCode, Rule, RuleSet, RuleSetStatus, RuleTat
from rule_engine.seed_data import v3_2, v3_3

DOC = Path(__file__).resolve().parents[2] / "docs" / "phase2_rule_table_v3_approved.txt"

# Rules whose v3.2 deadline was WORKING (all T+1), per the doc; R07b's
# deferred-chargeback TAT row too.
WORKING_IN_V3_2 = {
    "R01", "R02", "R03", "R04a", "R04b", "R05", "R06b", "R06", "R07x", "R07c", "R12", "R13", "R14", "R15",
    "R16", "R17", "R99",
}
RULE_FIELDS = ("code", "evaluation_order", "name", "condition", "status_category", "action", "priority",
               "response_code_id", "tat_type", "tat_days", "tat_cutoff_time", "rbi_compensation_after_tat",
               "reason_template", "source", "notes")


def _basis_only(rule: dict) -> dict:
    """A rule dict with the basis (and R13's basis wording) masked out."""
    r = dict(rule, tat_day_basis="*", tat_note=v3_3.TAT_NOTE_REWORDING.get(rule.get("tat_note"), rule.get("tat_note")))
    if "tat_rows" in r:
        r["tat_rows"] = [row[:4] + ("*",) + row[5:] for row in r["tat_rows"]]
    return r


class SeedDataTests(TestCase):
    def test_no_working_basis_left(self):
        self.assertEqual([r["code"] for r in v3_3.RULES if r.get("tat_day_basis") == "WORKING"], [])
        rows = [row for r in v3_3.RULES for row in r.get("tat_rows", [])]
        self.assertEqual([row[1] for row in rows if row[4] == "WORKING"], [])

    def test_the_working_rules_became_calendar_with_the_same_days_and_cutoff(self):
        self.assertEqual({r["code"] for r in v3_2.RULES if r.get("tat_day_basis") == "WORKING"}, WORKING_IN_V3_2)
        for old, new in zip(v3_2.RULES, v3_3.RULES):
            with self.subTest(rule=old["code"]):
                if old["code"] in WORKING_IN_V3_2:
                    self.assertEqual(new["tat_day_basis"], "CALENDAR")
                else:
                    self.assertEqual(new["tat_day_basis"], old["tat_day_basis"])
                self.assertEqual((new["tat_type"], new["tat_days"]), (old["tat_type"], old["tat_days"]))
                self.assertEqual(new.get("tat_cutoff_time"), old.get("tat_cutoff_time"))
        r05 = next(r for r in v3_3.RULES if r["code"] == "R05")
        self.assertEqual((r05["tat_days"], r05["tat_day_basis"], r05["tat_cutoff_time"]), (1, "CALENDAR", time(16, 0)))
        r07b = next(r for r in v3_3.RULES if r["code"] == "R07b")
        self.assertEqual(
            [(row[1], row[3], row[4]) for row in r07b["tat_rows"]],
            [("Wrong chargeback raise and representment", 35, "CALENDAR"),
             ("Fraud chargeback raise and representment", 35, "CALENDAR"),
             ("Deferred chargeback raise and representment", 1, "CALENDAR"),
             ("Re-presentment Raise", 35, "CALENDAR"),
             ("Chargeback raise", 7, "CALENDAR")],
        )

    def test_nothing_else_differs_from_v3_2(self):
        self.assertEqual([r["code"] for r in v3_3.RULES], [r["code"] for r in v3_2.RULES])
        for old, new in zip(v3_2.RULES, v3_3.RULES):
            with self.subTest(rule=old["code"]):
                self.assertEqual(_basis_only(old), _basis_only(new))
        r13 = next(r for r in v3_3.RULES if r["code"] == "R13")
        self.assertEqual(r13["tat_note"], "T+1 (calendar days), on its own business date.")
        self.assertIsNot(v3_3.RULES[0], v3_2.RULES[0])  # copies: editing one can't change the other
        self.assertIsNot(v3_3.RULES[0]["condition"], v3_2.RULES[0]["condition"])
        self.assertEqual(next(r for r in v3_2.RULES if r["code"] == "R13")["tat_day_basis"], "WORKING")  # v3.2 frozen

    def test_counts_and_version_metadata(self):
        self.assertEqual(v3_3.EXPECTED_COUNTS, v3_2.EXPECTED_COUNTS)
        self.assertEqual(v3_3.RESPONSE_CODES, v3_2.RESPONSE_CODES)
        self.assertEqual(v3_3.AGEING_BUCKETS, v3_2.AGEING_BUCKETS)
        self.assertEqual((v3_3.VERSION_LABEL, v3_3.SUPERSEDES), ("v3.3", "v3.2"))
        self.assertEqual(str(v3_3.EFFECTIVE_FROM), "2026-10-23")
        self.assertIn("v3.3 AMENDMENT (2026-10-23)", DOC.read_text(encoding="utf-8"))


class CommandTests(TestCase):
    def setUp(self):
        self.out = StringIO()
        call_command("seed_rule_sets", stdout=self.out)
        self.upi = PaymentModule.objects.get(code="UPI")

    def test_v3_3_active_v3_2_retired(self):
        v32 = RuleSet.objects.get(version_label="v3.2")
        v33 = RuleSet.objects.get(version_label="v3.3")
        # v3.4 (owner 2026-11-11) is seeded after v3.3 and retires it.
        self.assertEqual((v32.status, v33.status), (RuleSetStatus.RETIRED, RuleSetStatus.RETIRED))
        self.assertIsNotNone(v32.retired_at)
        self.assertEqual(RuleSet.objects.get(status="ACTIVE").version_label, "v3.4")
        self.assertEqual(RuleSet.objects.filter(payment_module=self.upi, status="ACTIVE").count(), 1)
        self.assertIn("rule set v3.3 created and activated with 24 rules", self.out.getvalue())
        self.assertFalse(v33.rules.filter(tat_day_basis="WORKING").exists())
        self.assertFalse(RuleTat.objects.filter(rule__rule_set=v33, tat_day_basis="WORKING").exists())
        self.assertEqual(set(v32.rules.filter(tat_day_basis="WORKING").values_list("code", flat=True)), WORKING_IN_V3_2)
        old = list(v32.rules.order_by("evaluation_order").values_list(*RULE_FIELDS))
        new = list(v33.rules.order_by("evaluation_order").values_list(*RULE_FIELDS))
        self.assertEqual(old, new)
        for code in ("R05", "R07b"):
            a, b = v32.rules.get(code=code), v33.rules.get(code=code)
            self.assertEqual(sorted(a.alternative_codes.values_list("pk", flat=True)),
                             sorted(b.alternative_codes.values_list("pk", flat=True)))

    def test_idempotent(self):
        out = StringIO()
        call_command("seed_rule_sets", stdout=out)
        self.assertNotIn("differs", out.getvalue())
        self.assertEqual((RuleSet.objects.count(), Rule.objects.count()), (4, 97))  # + v3.4
        self.assertEqual(RuleSet.objects.get(status="ACTIVE").version_label, "v3.4")


class MigrationTests(TestCase):
    def _run(self, name, state_after):
        migration = importlib.import_module(f"rule_engine.migrations.{name}")
        state = MigrationExecutor(connection).loader.project_state(("rule_engine", state_after))
        migration.forwards(state.apps, None)

    def _chain(self):
        self._run("0002_seed_rule_table_v3_1", "0001_initial")
        self._run("0005_seed_rule_table_v3_2", "0004_remove_next_day_recheck")

    def _v3_3(self):
        self._run("0006_seed_rule_table_v3_3", "0005_seed_rule_table_v3_2")

    def test_no_op_without_upi_module(self):
        self._v3_3()
        self.assertFalse(RuleSet.objects.exists())

    def test_activates_v3_3_and_retires_v3_2(self):
        PaymentModule.objects.create(code="UPI", name="UPI")
        self._chain()
        self._v3_3()
        v32 = RuleSet.objects.get(version_label="v3.2")
        v33 = RuleSet.objects.get(version_label="v3.3")
        self.assertEqual((v32.status, v33.status), ("RETIRED", "ACTIVE"))
        self.assertIsNotNone(v32.retired_at)
        self.assertEqual(v33.created_by, "migration rule_engine.0006")
        v33.validate_for_activation()
        for rule in v33.rules.all():
            rule.clean()
        self.assertFalse(v33.rules.filter(tat_day_basis="WORKING").exists())
        self._v3_3()  # idempotent
        self.assertEqual((RuleSet.objects.count(), Rule.objects.count()), (3, 72))

    def test_reuses_reworded_codes_instead_of_duplicating_them(self):
        # A description reworded through maker-checker since v3.2 (NPCI file design Amendment 1).
        upi = PaymentModule.objects.create(code="UPI", name="UPI")
        self._chain()
        r05 = RuleSet.objects.get(version_label="v3.2").rules.get(code="R05")
        ResponseCode._base_manager.filter(pk=r05.response_code_id).update(description="Reworded by a checker")
        before = ResponseCode.objects.filter(payment_module=upi).count()
        self._v3_3()
        self.assertEqual(ResponseCode.objects.filter(payment_module=upi).count(), before)
        self.assertEqual(RuleSet.objects.get(version_label="v3.3").rules.get(code="R05").response_code_id,
                         r05.response_code_id)

    def test_does_not_retire_another_active_version(self):
        upi = PaymentModule.objects.create(code="UPI", name="UPI")
        self._chain()
        RuleSet._base_manager.filter(payment_module=upi, status="ACTIVE").update(version_label="v9")
        self._v3_3()
        self.assertFalse(RuleSet.objects.filter(version_label="v3.3").exists())
        self.assertEqual(RuleSet.objects.get(status="ACTIVE").version_label, "v9")
