"""
Seed of rule table v3.1: `seed_rule_sets` command and data migration 0002.

The expected values below are typed in again from
docs/phase2_rule_table_v3_approved.txt, independently of
rule_engine/seed_data/v3_1.py, so a transcription slip in either shows up.
Evaluation order is also read straight from the doc's rule headings and
from the reference SQL's CASE branches.
"""
from __future__ import annotations

import importlib
import re
from datetime import time
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

from core.models import PaymentModule
from rule_engine.conditions import facts_used, validate_condition
from rule_engine.models import (
    AgeingBucket,
    ResponseCode,
    Rule,
    RuleSet,
    RuleSetStatus,
    RuleTat,
    validate_ageing_buckets,
)
from rule_engine.seed_data import v3_1

DOCS = Path(__file__).resolve().parents[2] / "docs"
RULE_DOC = DOCS / "phase2_rule_table_v3_approved.txt"
REFERENCE_SQL = DOCS / "phase2_rule_table_v3_1_reference.sql"

ORDER = [
    "R01", "R02", "R03", "R04a", "R04b", "R05", "R06b", "R06", "R07a", "R07x", "R07b", "R07c",
    "R04c", "R08", "R09", "R10", "R11", "R12", "R13", "R14", "R15", "R16", "R17", "R99",
]

NFA, PEND, EXC = "NO_FINANCIAL_ACTION_REQUIRED", "PENDING_INVESTIGATION", "EXCEPTION"
T1 = ("DAYS", 1, "WORKING")
NO = ("NONE", None, "")
# code: (status, priority, (flag, code) or None, (tat_type, days, basis))
EXPECTED = {
    "R01": (NFA, "LOW", ("DRC", "102"), T1),
    "R02": ("REVERSAL_REQUIRED", "HIGH", ("DRC", "103"), T1),
    "R03": (NFA, "MEDIUM", ("DRC", "104"), T1),
    "R04a": (NFA, "LOW", ("TCC", "102"), T1),
    "R04b": (PEND, "MEDIUM", ("TCC", "102"), T1),
    "R05": ("RETURN_REQUIRED", "HIGH", ("RET", "115"), T1),
    "R06b": (PEND, "HIGH", ("RRC", "501"), T1),
    "R06": ("MANUAL_CREDIT_REQUIRED", "HIGH", ("RRC", "501"), T1),
    "R07a": (NFA, "LOW", None, NO),
    "R07x": (EXC, "HIGH", None, T1),
    "R07b": ("CHARGEBACK_REQUIRED", "HIGH", None, ("BY_ADJTYPE", None, "")),
    "R07c": (PEND, "MEDIUM", None, T1),
    "R04c": (NFA, "LOW", None, NO),
    "R08": (NFA, "LOW", None, NO),
    "R09": ("RECONCILED", "NONE", None, NO),
    "R10": (NFA, "LOW", None, NO),
    "R11": (NFA, "NONE", None, NO),
    "R12": ("ADJUSTMENT_REQUIRED", "HIGH", None, T1),
    "R13": (PEND, "MEDIUM", None, ("NEXT_BUSINESS_DATE_LOAD", None, "")),
    "R14": (EXC, "HIGH", None, T1),
    "R15": (EXC, "HIGH", None, T1),
    "R16": (EXC, "HIGH", None, T1),
    "R17": (PEND, "MEDIUM", None, T1),
    "R99": (PEND, "MEDIUM", None, T1),
}
R07B_TATS = [
    ("Wrong chargeback raise and representment", 35, "CALENDAR"),
    ("Fraud chargeback raise and representment", 35, "CALENDAR"),
    ("Deferred chargeback raise and representment", 1, "WORKING"),
    ("Re-presentment Raise", 35, "CALENDAR"),
    ("Chargeback raise", 7, "CALENDAR"),
]
RET_REASONS_UNKNOWN = {
    "Account closed", "NRI account", "Party instructions", "Invalid beneficiary details",
    "Credit freezed", "Beneficiary bank unable to credit", "Any other reason",
}


def _seed() -> str:
    out = StringIO()
    call_command("seed_rule_sets", stdout=out)
    return out.getvalue()


def _snapshot(module) -> dict:
    """Comparable content of everything seeded for `module` (ids/timestamps dropped)."""
    rs = RuleSet.objects.get(payment_module=module, version_label="v3.1")
    rules = []
    for r in Rule.objects.filter(rule_set=rs).order_by("evaluation_order"):
        rules.append((
            r.evaluation_order, r.code, r.name, r.condition, r.status_category, r.action, r.priority,
            (r.response_code.flag, r.response_code.description) if r.response_code else None,
            sorted(c.description for c in r.alternative_codes.all()),
            r.tat_type, r.tat_days, r.tat_day_basis, r.tat_cutoff_time, r.tat_note,
            r.rbi_compensation_after_tat, r.reason_template, r.source, r.notes,
            [(t.evaluation_order, t.label, t.condition, t.tat_days, t.tat_day_basis, t.source, t.notes)
             for t in r.tat_rows.order_by("evaluation_order")],
        ))
    return dict(
        rule_set=(rs.status, rs.effective_from, rs.source_document, rs.notes),
        rules=rules,
        codes=list(ResponseCode.objects.filter(payment_module=module).order_by("flag", "description").values_list(
            "flag", "code", "description", "source", "notes")),
        buckets=list(AgeingBucket.objects.filter(payment_module=module).order_by("order").values_list(
            "order", "label", "min_days", "max_days", "boundary_reason", "source")),
    )


class DocOrderTests(TestCase):
    def test_doc_headings_give_the_seed_order(self):
        text = RULE_DOC.read_text(encoding="utf-8")
        section = text.split("THE RULES (checked in this order)")[1].split(" SUMMARY - ")[0]
        headings = re.findall(r"^(R\d\d[a-z]?)\s", section, flags=re.M)
        self.assertEqual(headings, ORDER)
        self.assertEqual([r["code"] for r in v3_1.RULES], ORDER)
        self.assertEqual(len(ORDER), 24)

    def test_reference_sql_case_order_matches(self):
        sql = REFERENCE_SQL.read_text(encoding="utf-8")
        branches = re.findall(r"then '(R\d\d[a-z]?)'", sql)
        # R04b/R14-R17 have branches; R99 is the ELSE.
        self.assertEqual(branches + ["R99"], ORDER)

    def test_expected_counts_cover_every_rule_and_add_up(self):
        self.assertEqual(list(v3_1.EXPECTED_COUNTS), ORDER)
        sums = tuple(sum(c[i] for c in v3_1.EXPECTED_COUNTS.values()) for i in range(3))
        self.assertEqual(sums, v3_1.EXPECTED_TOTALS)


class SeedCommandTests(TestCase):
    def setUp(self):
        self.output = _seed()
        self.upi = PaymentModule.objects.get(code="UPI")
        self.rs = RuleSet.objects.get(payment_module=self.upi, version_label="v3.1")

    def test_active_version_v3_1(self):
        self.assertEqual(self.rs.status, RuleSetStatus.ACTIVE)
        self.assertEqual(str(self.rs.effective_from), "2026-10-09")
        self.assertIn("phase2_rule_table_v3_approved.txt", self.rs.source_document)
        self.assertEqual(self.rs.created_by, "seed_rule_sets")
        self.assertIn("24 rules", self.output)

    def test_rule_count_and_order(self):
        rules = list(self.rs.rules.order_by("evaluation_order"))
        self.assertEqual([r.code for r in rules], ORDER)
        self.assertEqual([r.evaluation_order for r in rules], list(range(1, 25)))
        codes = [r.code for r in rules]
        # R04c deliberately after the Adjustment rules; R07x before R07b; R06b before R06.
        self.assertGreater(codes.index("R04c"), codes.index("R07c"))
        self.assertLess(codes.index("R07x"), codes.index("R07b"))
        self.assertLess(codes.index("R06b"), codes.index("R06"))

    def test_each_rule_matches_the_doc(self):
        for rule in self.rs.rules.select_related("response_code"):
            status, priority, code, tat = EXPECTED[rule.code]
            with self.subTest(rule=rule.code):
                self.assertEqual(rule.status_category, status)
                self.assertEqual(rule.priority, priority)
                self.assertEqual(
                    (rule.response_code.flag, rule.response_code.code) if rule.response_code else None, code
                )
                self.assertEqual((rule.tat_type, rule.tat_days, rule.tat_day_basis), tat)
                validate_condition(rule.condition)
                self.assertTrue(rule.source)

    def test_rule_specifics(self):
        r = {x.code: x for x in self.rs.rules.all()}
        self.assertEqual(r["R05"].tat_cutoff_time, time(16, 0))
        self.assertEqual({c.description for c in r["R05"].alternative_codes.all()}, RET_REASONS_UNKNOWN)
        self.assertEqual({k for k, v in r.items() if v.rbi_compensation_after_tat}, {"R02", "R05", "R12"})
        self.assertIn("Verify before crediting, or the customer is paid twice.", r["R06b"].reason_template)
        self.assertEqual(
            r["R07x"].reason_template.format(age_days=41),
            "Dispute is 41 days past the original transaction (limit 35). Check whether the response "
            "deadline has already passed.",
        )
        self.assertEqual(
            [(t.label, t.tat_days, t.tat_day_basis) for t in r["R07b"].tat_rows.order_by("evaluation_order")],
            R07B_TATS,
        )
        self.assertEqual(RuleTat.objects.exclude(rule=r["R07b"]).count(), 0)
        self.assertIn({"fact": "adjustment.age_days", "op": "gt", "value": 35}, r["R07x"].condition["all"])
        self.assertEqual(r["R04c"].condition, {"all": [{"fact": "timeout.ssk_side", "op": "eq", "value": False}]})
        self.assertIn({"fact": "cbs_reversed", "op": "eq", "value": False}, r["R09"].condition["all"])
        self.assertEqual(r["R99"].condition, {"always": True})
        self.assertEqual(facts_used(r["R08"].condition), {"drc.ssk_side", "adjustment.ssk_side", "cbs.present", "npci.present"})

    def test_code_table(self):
        rows = {(c.flag, c.code, c.description) for c in ResponseCode.objects.filter(payment_module=self.upi)}
        known = {(f, c) for f, c, _ in rows if c is not None}
        self.assertEqual(known, {
            ("DRC", "102"), ("DRC", "103"), ("DRC", "104"), ("TCC", "102"), ("TCC", "103"),
            ("RET", "115"), ("RRC", "501"), ("NPCI_RESPONSE", "00"), ("NPCI_RESPONSE", "RB"),
        })
        self.assertEqual({d for f, c, d in rows if c is None}, RET_REASONS_UNKNOWN)
        self.assertTrue(all(f == "RET" for f, c, _ in rows if c is None))
        self.assertTrue(all(c.source for c in ResponseCode.objects.all()))

    def test_code_used_by(self):
        def used(flag, code):
            return ResponseCode.objects.get(payment_module=self.upi, flag=flag, code=code).used_by(self.rs)

        self.assertEqual(used("DRC", "102"), ["R01"])
        self.assertEqual(used("DRC", "103"), ["R02"])
        self.assertEqual(used("DRC", "104"), ["R03"])
        self.assertEqual(used("TCC", "102"), ["R04a", "R04b"])
        self.assertEqual(used("TCC", "103"), [])
        self.assertEqual(used("RET", "115"), ["R05"])
        self.assertEqual(used("RRC", "501"), ["R06b", "R06"])
        self.assertEqual(used("NPCI_RESPONSE", "00"), ["R09", "R11", "R17"])
        self.assertEqual(used("NPCI_RESPONSE", "RB"), [])
        closed = ResponseCode.objects.get(payment_module=self.upi, flag="RET", description="Account closed")
        self.assertEqual(closed.used_by(self.rs), ["R05"])

    def test_ageing_buckets(self):
        buckets = list(AgeingBucket.objects.filter(payment_module=self.upi).order_by("order"))
        self.assertEqual(
            [(b.label, b.min_days, b.max_days) for b in buckets],
            [("0-1 d", 0, 1), ("2-5 d", 2, 5), ("6-7 d", 6, 7), ("8-35 d", 8, 35), ("36-45 d", 36, 45), ("45+ d", 46, None)],
        )
        self.assertTrue(all(b.boundary_reason for b in buckets))
        validate_ageing_buckets(self.upi)

    def test_idempotent(self):
        before = _snapshot(self.upi)
        counts = (RuleSet.objects.count(), Rule.objects.count(), ResponseCode.objects.count(), AgeingBucket.objects.count())
        out = _seed()
        self.assertNotIn("differs", out)
        self.assertEqual(
            (RuleSet.objects.count(), Rule.objects.count(), ResponseCode.objects.count(), AgeingBucket.objects.count()),
            counts,
        )
        self.assertEqual(counts, (1, 24, 16, 6))
        self.assertEqual(_snapshot(self.upi), before)

    def test_does_not_retire_a_different_active_version(self):
        # Simulate a later version being the active one and v3.1 absent
        # (relabel via the unguarded base manager, test-only).
        RuleSet._base_manager.filter(pk=self.rs.pk).update(version_label="v4")
        out = _seed()
        self.assertIn("already active", out)
        self.assertFalse(RuleSet.objects.filter(version_label="v3.1").exists())


class DataMigrationTests(TestCase):
    def _forwards(self):
        """Run 0002's forwards() with the historical models it really gets."""
        migration = importlib.import_module("rule_engine.migrations.0002_seed_rule_table_v3_1")
        state = MigrationExecutor(connection).loader.project_state(("rule_engine", "0001_initial"))
        migration.forwards(state.apps, None)

    def test_no_op_without_upi_module(self):
        self._forwards()
        self.assertFalse(PaymentModule.objects.exists())
        self.assertFalse(RuleSet.objects.exists())
        self.assertFalse(ResponseCode.objects.exists())

    def test_migration_and_command_seed_the_same_rows(self):
        upi = PaymentModule.objects.create(code="UPI", name="UPI")
        self._forwards()
        migrated = _snapshot(upi)
        rs = RuleSet.objects.get(payment_module=upi)
        self.assertEqual(rs.created_by, "migration rule_engine.0002")
        rs.validate_for_activation()  # the live structural checks pass on migrated data too
        for rule in rs.rules.all():
            rule.clean()

        # command afterwards is a no-op
        out = _seed()
        self.assertNotIn("differs", out)
        self.assertEqual(_snapshot(upi), migrated)

        # and a command-only seed on a clean DB gives identical content
        RuleSet._base_manager.all().delete()
        ResponseCode._base_manager.all().delete()
        AgeingBucket.objects.all().delete()
        _seed()
        self.assertEqual(_snapshot(upi), migrated)

    def test_migration_is_idempotent(self):
        upi = PaymentModule.objects.create(code="UPI", name="UPI")
        self._forwards()
        before = _snapshot(upi)
        self._forwards()
        self.assertEqual(_snapshot(upi), before)
        self.assertEqual(Rule.objects.count(), 24)
