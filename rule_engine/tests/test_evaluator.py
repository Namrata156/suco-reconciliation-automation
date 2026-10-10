"""
Rule evaluator (Phase 2 Step 4): fact extraction, condition semantics,
first-match order, deadlines, supersede-on-rerun, single-day evaluation
(rule set v3.2: R13 a T+1 review item, no cross-day check), read-time ageing, admin,
command and Celery task.

Match groups are built directly (presence rows + the facts they point at)
so each test states exactly the matrix shape it needs. The full-date
real-data acceptance (EXPECTED_COUNTS, 233,081 groups vs the oracle SQL) is
`manage.py check_rule_evaluation`: the test suite only loads single-cycle
samples. OracleAgreementTests runs the same oracle SQL on the synthetic
groups here.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Direction, PaymentModule, RegisterType
from core.working_days import WorkingDayCalendar
from file_processing.models import FileTypeConfig, PayloadFormat, ProcessingStatus, UploadedFile
from reconciliation.models import RegisterPresence, TransactionMatchGroup
from rule_engine.conditions import FACTS
from rule_engine.evaluator import (
    active_rule_set,
    compile_condition,
    compile_rule_set,
    evaluate_business_date,
    first_match,
)
from rule_engine.facts import extract_facts
from rule_engine.management.commands.check_rule_evaluation import oracle_rules
from rule_engine.models import RuleEvaluationRun, RuleResult
from rule_engine.tasks import evaluate_rules
from transactions.models import TransactionFact
from transactions.models.history import StagingAdjustmentHistory
from transactions.models.staging import STAGING_MODEL_BY_REGISTER_TYPE, StagingAdjustment

D = date(2026, 7, 14)  # Tuesday
AUTO = object()


class EvaluatorTestBase(TestCase):
    def setUp(self):
        call_command("seed_rule_sets", stdout=StringIO())
        self.module = PaymentModule.objects.get(code="UPI")
        self._files: dict = {}
        self._row = 0

    # -- builders -------------------------------------------------------------
    def file(self, register_type, d, status=ProcessingStatus.LOADED):
        key = (register_type, d)
        if key not in self._files:
            config = FileTypeConfig.objects.create(
                payment_module=self.module,
                file_type_code=f"{register_type}_{d:%Y%m%d}",
                register_type=register_type,
                payload_format=PayloadFormat.CSV,
            )
            self._files[key] = UploadedFile.objects.create(
                filename=f"{register_type}_{d}.csv",
                path=f"{register_type}_{d}.csv",
                payment_module=self.module,
                file_type_config=config,
                business_date=d,
                declared_format=".csv",
                size_bytes=1,
                sha256_hash=f"hash-{register_type}-{d}",
                processing_status=status,
            )
        return self._files[key]

    def fact(self, register_type, rrn, d, **kw):
        self._row += 1
        return TransactionFact.objects.create(
            payment_module=self.module,
            source_register_type=register_type,
            uploaded_file=kw.pop("uploaded_file", None) or self.file(register_type, d),
            business_date=d,
            rrn=rrn,
            rrn_valid=True,
            amount=Decimal("10.00"),
            direction=kw.pop("direction", Direction.DEBIT),
            row_reference=str(self._row),
            **kw,
        )

    def npci_fact(self, rrn, d, code="00", register_type=RegisterType.RAW_DATA):
        """An NPCI-leg fact whose staging row carries the response code in
        cell 5, where the presence engine reads it."""
        f = self.file(register_type, d)
        self._row += 1
        staging = STAGING_MODEL_BY_REGISTER_TYPE[register_type].objects.create(
            uploaded_file=f, row_number=self._row, row_reference=f":{self._row}", raw_cells={"4": rrn, "5": code}
        )
        return self.fact(register_type, rrn, d, staging_row_id=staging.id, uploaded_file=f)

    def group(
        self,
        rrn,
        d=D,
        *,
        cbs=False,
        npci=False,
        code="00",
        cbs_reversed=False,
        is_on_us=False,
        pair_missing=False,
        amount_match=AUTO,
        direction_match=AUTO,
        drc=None,
        timeout=None,
        adj=None,
        superseded=False,
    ):
        both = cbs and npci
        g = TransactionMatchGroup.objects.create(
            payment_module=self.module,
            rrn=rrn,
            business_date=d,
            cbs_reversed=cbs_reversed,
            is_on_us=is_on_us,
            amount_match=(True if both else None) if amount_match is AUTO else amount_match,
            direction_match=(True if both else None) if direction_match is AUTO else direction_match,
            is_superseded=superseded,
        )
        cbs_fact = self.fact(RegisterType.CBS_GL_EXTRACT, rrn, d) if cbs else None
        pair = (
            self.fact(RegisterType.CBS_GL_EXTRACT, rrn, d, direction=Direction.CREDIT)
            if cbs and is_on_us and not pair_missing
            else None
        )
        RegisterPresence.objects.create(
            match_group=g, leg="CBS", present=cbs, transaction_record=cbs_fact,
            paired_transaction_record=pair,
        )
        npci_fact = self.npci_fact(rrn, d, code) if npci else None
        RegisterPresence.objects.create(
            match_group=g, leg="NPCI", present=npci, transaction_record=npci_fact, response_code=code if npci else ""
        )
        if drc is not None:
            RegisterPresence.objects.create(match_group=g, leg="DRC", present=True, ssk_side=drc)
        if timeout is not None:
            RegisterPresence.objects.create(match_group=g, leg="TIMEOUT", present=True, ssk_side=timeout)
        if adj is not None:
            self.adjustment(g, **adj)
        return g

    def adjustment(self, g, *, ssk=True, type="RET", adj_date=None, txn_date="AUTO", archived=False):
        adj_date = adj_date or g.business_date
        txn_date = g.business_date.strftime("%d-%m-%Y") if txn_date == "AUTO" else txn_date
        f = self.file(RegisterType.ADJUSTMENT, adj_date)
        self._row += 1
        staging = StagingAdjustment.objects.create(
            uploaded_file=f, row_number=self._row, row_reference=f":{self._row}", raw_cells={}, txn_date_raw=txn_date
        )
        staging_id = staging.id
        if archived:
            StagingAdjustmentHistory.objects.create(
                original_id=staging_id, uploaded_file=f, row_number=staging.row_number,
                row_reference=staging.row_reference, raw_cells={}, created_at=staging.created_at,
                txn_date_raw=txn_date,
            )
            staging.delete()
        fact = self.fact(RegisterType.ADJUSTMENT, g.rrn, adj_date, staging_row_id=staging_id, uploaded_file=f)
        RegisterPresence.objects.create(
            match_group=g, leg="ADJUSTMENT", present=True, transaction_record=fact, ssk_side=ssk, dispute_type=type
        )

    def evaluate(self, d=D):
        return evaluate_business_date(self.module, d)

    def result(self, rrn, d=D) -> RuleResult:
        return RuleResult.objects.get(rrn=rrn, business_date=d, is_superseded=False)


# ---------------------------------------------------------------------------
class FactExtractionTests(EvaluatorTestBase):
    def facts(self, rrn, d=D):
        return next(gf for gf in extract_facts(self.module, d) if gf.rrn == rrn)

    def test_vocabulary_matches_conditions_exactly(self):
        self.group("100000000001", cbs=True, npci=True)
        self.assertEqual(set(self.facts("100000000001").facts), set(FACTS))

    def test_matched_group(self):
        f = self.facts(self.group("100000000001", cbs=True, npci=True).rrn).facts
        self.assertEqual(
            {k: f[k] for k in ("cbs.present", "npci.present", "npci.response_code", "amount_match", "direction_match")},
            {"cbs.present": True, "npci.present": True, "npci.response_code": "00", "amount_match": True,
             "direction_match": True},
        )
        for prefix in ("drc", "timeout", "adjustment"):
            self.assertFalse(f[f"{prefix}.present"])
            self.assertIsNone(f[f"{prefix}.ssk_side"])
        self.assertIsNone(f["adjustment.adjtype"])
        self.assertIsNone(f["adjustment.age_days"])
        self.assertTrue(f["cbs.pair_missing"])  # no pair row; only meaningful with is_on_us

    def test_one_sided_npci_code_blank_not_null(self):
        f = self.facts(self.group("100000000002", cbs=True).rrn).facts
        self.assertEqual(f["npci.response_code"], "")
        self.assertIsNone(f["amount_match"])

    def test_on_us_pair(self):
        f = self.facts(self.group("100000000003", cbs=True, npci=True, is_on_us=True).rrn).facts
        self.assertTrue(f["is_on_us"])
        self.assertFalse(f["cbs.pair_missing"])

    def test_overlays_and_adjustment_age_from_staging(self):
        g = self.group("100000000004", cbs=True, drc=False, timeout=True,
                       adj=dict(type="Re-presentment Raise", adj_date=D, txn_date="03-06-2026"))
        gf = self.facts(g.rrn)
        self.assertEqual(gf.facts["drc.ssk_side"], False)
        self.assertEqual(gf.facts["timeout.ssk_side"], True)
        self.assertEqual(gf.facts["adjustment.adjtype"], "Re-presentment Raise")
        self.assertEqual(gf.facts["adjustment.age_days"], 41)
        self.assertEqual(gf.adjustment_date, D)

    def test_adjustment_age_from_staging_history_once_archived(self):
        g = self.group("100000000005", adj=dict(type="RET", txn_date="04-07-2026", archived=True))
        self.assertEqual(self.facts(g.rrn).facts["adjustment.age_days"], 10)

    def test_unparseable_txn_date_is_null(self):
        g = self.group("100000000006", adj=dict(type="RET", txn_date="2026/07/04"))
        self.assertIsNone(self.facts(g.rrn).facts["adjustment.age_days"])

    def test_superseded_groups_skipped(self):
        self.group("100000000007", cbs=True, superseded=True)
        self.assertEqual(extract_facts(self.module, D), [])

    def test_query_count_does_not_grow_with_groups(self):
        for i in range(3):
            self.group(f"20000000000{i}", cbs=True, npci=True, adj=dict(type="RET"))
        # groups, presences, Adjustment facts, staging (+1 for history only
        # when some staging rows are archived)
        with self.assertNumQueries(4):
            extract_facts(self.module, D)
        for i in range(3, 9):
            self.group(f"20000000000{i}", cbs=True, npci=True, adj=dict(type="RET"))
        with self.assertNumQueries(4):
            extract_facts(self.module, D)


# ---------------------------------------------------------------------------
class ConditionSemanticsTests(TestCase):
    def test_null_fact_is_false_for_every_operator(self):
        facts = {"x": None}
        for op, value in (("eq", False), ("ne", True), ("in", ["a"]), ("not_in", ["a"]), ("icontains_any", ["a"]),
                          ("gt", 1), ("lte", 1)):
            with self.subTest(op=op):
                self.assertFalse(compile_condition({"fact": "x", "op": op, "value": value})(facts))

    def test_operators(self):
        c = compile_condition
        self.assertTrue(c({"fact": "t", "op": "icontains_any", "value": ["chargeback"]})({"t": "Wrong Credit ChargeBack Raise"}))
        self.assertFalse(c({"fact": "t", "op": "in", "value": ["RET"]})({"t": "ret"}))  # exact, case-sensitive
        self.assertTrue(c({"fact": "t", "op": "not_in", "value": ["RET"]})({"t": "TCC"}))
        self.assertTrue(c({"fact": "n", "op": "gt", "value": 35})({"n": 36}))
        self.assertFalse(c({"fact": "n", "op": "gt", "value": 35})({"n": 35}))
        self.assertTrue(c({"any": [{"fact": "a", "op": "eq", "value": True}, {"fact": "b", "op": "eq", "value": True}]})(
            {"a": None, "b": True}))
        self.assertFalse(c({"all": [{"fact": "a", "op": "eq", "value": True}, {"always": True}]})({"a": False}))


# ---------------------------------------------------------------------------
class FirstMatchTests(EvaluatorTestBase):
    CASES = [
        # rrn suffix, group kwargs, expected rule
        ("01", dict(cbs=True, npci=True), "R09"),
        ("02", dict(cbs=True, npci=True, cbs_reversed=True, drc=True), "R01"),  # not R10
        ("03", dict(cbs=True, drc=True), "R02"),
        ("04", dict(drc=True), "R03"),
        ("05", dict(cbs=True, npci=True, timeout=True), "R04a"),
        ("06", dict(cbs=True, cbs_reversed=True, timeout=True), "R04b"),
        ("07", dict(timeout=True), "R05"),
        ("08", dict(cbs=True, cbs_reversed=True, adj=dict(type="RET")), "R06b"),
        ("09", dict(cbs=True, npci=True, code="RB", timeout=False, adj=dict(type="RET")), "R06"),  # not R04c
        ("10", dict(cbs=True, npci=True, code="RB", timeout=False, adj=dict(type="TCC")), "R07a"),
        ("11", dict(adj=dict(type="Re-presentment Raise", txn_date="08-06-2026")), "R07x"),  # 36 days
        ("12", dict(adj=dict(type="Re-presentment Raise", txn_date="09-06-2026")), "R07b"),  # 35 days
        ("13", dict(adj=dict(type="Complaint Raise")), "R07c"),
        ("14", dict(cbs=True, npci=True, code="RB", timeout=False), "R04c"),
        ("15", dict(drc=False), "R08"),
        ("16", dict(adj=dict(ssk=False, type="RET")), "R08"),
        ("17", dict(cbs=True, cbs_reversed=True), "R10"),
        ("18", dict(npci=True, code="Z9"), "R11"),
        ("19", dict(npci=True), "R12"),
        ("20", dict(cbs=True), "R13"),
        ("21", dict(cbs=True, npci=True, is_on_us=True, pair_missing=True), "R14"),
        ("22", dict(cbs=True, npci=True, amount_match=False), "R15"),
        ("23", dict(cbs=True, npci=True, direction_match=False), "R16"),
        ("24", dict(cbs=True, npci=True, code="U9"), "R17"),
        ("25", dict(cbs=True, npci=True, amount_match=None, direction_match=None), "R99"),
        ("26", dict(cbs=True, npci=True, timeout=False, adj=dict(ssk=False, type="RET")), "R04c"),
    ]

    def test_each_rule_and_its_order(self):
        for suffix, kwargs, _ in self.CASES:
            self.group(f"3000000000{suffix}", **kwargs)
        summary = self.evaluate()
        self.assertEqual(summary.group_count, len(self.CASES))
        for suffix, _, expected in self.CASES:
            with self.subTest(rrn=suffix, expected=expected):
                self.assertEqual(self.result(f"3000000000{suffix}").rule_code, expected)
        self.assertEqual(RuleResult.objects.filter(is_superseded=False).count(), len(self.CASES))
        results = RuleResult.objects.filter(is_superseded=False)
        self.assertEqual({r.rule_set.version_label for r in results}, {"v3.2"})
        self.assertTrue(all(r.facts and r.rule.code == r.rule_code for r in results))

    def test_first_match_helper_never_returns_none(self):
        compiled = compile_rule_set(active_rule_set(self.module))
        empty = {name: None for name in FACTS}
        self.assertEqual(first_match(compiled, empty).rule.code, "R99")


class OracleAgreementTests(FirstMatchTests):
    """The acceptance oracle SQL (docs/phase2_rule_table_v3_1_reference.sql)
    gives the same rule as the evaluator for every synthetic group above."""

    def test_each_rule_and_its_order(self):
        for suffix, kwargs, _ in self.CASES:
            self.group(f"3000000000{suffix}", **kwargs)
        self.evaluate()
        ours = {r.match_group_id: (r.business_date, r.rule_code) for r in RuleResult.objects.filter(is_superseded=False)}
        self.assertEqual(oracle_rules(), ours)


# ---------------------------------------------------------------------------
class OutcomeTests(EvaluatorTestBase):
    def test_r05_due_next_working_day_by_16_00_with_alternatives(self):
        self.group("400000000001", timeout=True)
        self.evaluate()
        r = self.result("400000000001")
        self.assertEqual((r.status_category, r.priority), ("RETURN_REQUIRED", "HIGH"))
        self.assertEqual(r.tat_start_date, D)
        self.assertEqual((r.due_date, r.due_cutoff_time), (date(2026, 7, 15), time(16, 0)))
        self.assertEqual(r.due_datetime.isoformat(), "2026-07-15T16:00:00+05:30")
        self.assertEqual(r.response_code.code, "115")
        self.assertEqual(len(r.alternative_codes), 7)
        self.assertTrue(r.rbi_compensation)

    def test_r07x_clock_on_adjdate_and_reason(self):
        g = self.group("400000000002", d=D, adj=dict(type="Re-presentment Raise", adj_date=D, txn_date="03-06-2026"))
        self.evaluate()
        r = self.result(g.rrn)
        self.assertEqual(r.rule_code, "R07x")
        self.assertEqual((r.tat_start_date, r.due_date), (D, date(2026, 7, 15)))
        self.assertEqual(
            r.reason,
            "Dispute is 41 days past the original transaction (limit 35). Check whether the response deadline "
            "has already passed.",
        )

    def test_adjustment_on_txndate_group_starts_clock_on_adjdate(self):
        # Overlay sits on its Txndate's group (Fri 25 Sep) but arrived on Sat 26 Sep.
        d = date(2026, 9, 25)
        self.group("400000000003", d=d, cbs=True, npci=True, adj=dict(type="RET", adj_date=date(2026, 9, 26)))
        self.group("400000000004", d=d, drc=True)
        self.evaluate(d)
        r = self.result("400000000003", d)
        self.assertEqual(r.rule_code, "R06")
        self.assertEqual(r.tat_start_date, date(2026, 9, 26))
        self.assertEqual(r.due_date, date(2026, 9, 28))  # 26 = 4th Sat, 27 = Sun
        drc = self.result("400000000004", d)
        self.assertEqual((drc.tat_start_date, drc.due_date), (d, date(2026, 9, 28)))

    def test_calendar_day_tat_not_shifted(self):
        # Sun 6 Sep + 35 calendar days = Sun 11 Oct, left on the Sunday.
        d = date(2026, 9, 6)
        self.group("400000000005", d=d, adj=dict(type="Wrong Credit Chargeback Raise", txn_date="24-08-2026"))
        self.evaluate(d)
        r = self.result("400000000005", d)
        self.assertEqual((r.rule_code, r.due_date), ("R07b", date(2026, 10, 11)))
        self.assertIn("Wrong chargeback raise and representment: 35 calendar days", r.tat_label)

    def test_by_adjtype_without_matching_row_is_flagged(self):
        self.group("400000000006", adj=dict(type="Chargeback Representment"))
        self.evaluate()
        r = self.result("400000000006")
        self.assertEqual(r.rule_code, "R07b")
        self.assertTrue(r.tat_unresolved)
        self.assertIsNone(r.due_date)

    def test_no_tat_rule(self):
        self.group("400000000007", cbs=True, npci=True)
        self.evaluate()
        r = self.result("400000000007")
        self.assertEqual((r.status_category, r.priority, r.due_date, r.tat_label), ("RECONCILED", "NONE", None, ""))

    def test_crossing_into_unloaded_year_flags_result(self):
        d = date(2026, 12, 31)
        self.group("400000000008", d=d, cbs=True, drc=True)
        with self.assertLogs("core.working_days", level="WARNING"):
            summary = self.evaluate(d)
        r = self.result("400000000008", d)
        self.assertEqual((r.rule_code, r.due_date), ("R02", date(2027, 1, 1)))
        self.assertTrue(r.calendar_incomplete)
        self.assertEqual(summary.calendar_incomplete_count, 1)


# ---------------------------------------------------------------------------
class SupersedeTests(EvaluatorTestBase):
    def test_rerun_supersedes_and_recreates_identically(self):
        self.group("500000000001", cbs=True, npci=True)
        self.group("500000000002", timeout=True)
        first = self.evaluate()
        old_ids = set(RuleResult.objects.values_list("id", flat=True))
        second = self.evaluate()
        self.assertEqual(first.rule_counts, second.rule_counts)
        self.assertEqual(second.superseded_count, 2)
        old = RuleResult.objects.filter(id__in=old_ids)
        self.assertTrue(all(r.is_superseded and r.superseded_at for r in old))
        new = RuleResult.objects.filter(is_superseded=False)
        self.assertEqual(new.count(), 2)
        self.assertFalse(set(new.values_list("id", flat=True)) & old_ids)
        self.assertEqual(RuleResult.objects.count(), 4)  # kept, never deleted
        self.assertEqual(RuleEvaluationRun.objects.count(), 2)

    def test_one_current_result_per_group_enforced(self):
        self.group("500000000003", cbs=True)
        self.evaluate()
        r = self.result("500000000003")
        r.pk = None
        with self.assertRaises(IntegrityError), transaction.atomic():
            r.save()

    def test_other_dates_untouched(self):
        self.group("500000000004", cbs=True, npci=True)
        self.group("500000000005", d=D + timedelta(days=10), cbs=True, npci=True)
        self.evaluate()
        self.evaluate(D + timedelta(days=10))
        self.evaluate()
        self.assertFalse(self.result("500000000005", D + timedelta(days=10)).is_superseded)
        self.assertEqual(RuleResult.objects.filter(business_date=D + timedelta(days=10)).count(), 1)


# ---------------------------------------------------------------------------
class SingleDayTests(EvaluatorTestBase):
    """Rule set v3.2 (owner 2026-10-10, "we only work on that particular
    day"): each business date is evaluated against its own files only. R13
    is a T+1 review item; there is no cross-day check in either direction."""

    def test_active_set_is_v3_2(self):
        self.group("620000000001", cbs=True)
        summary = self.evaluate()
        self.assertEqual(summary.rule_set_version, "v3.2")
        r = self.result("620000000001")
        self.assertEqual((r.rule_set.version_label, r.rule.rule_set.version_label), ("v3.2", "v3.2"))

    def test_r13_pending_investigation_medium_due_next_working_day(self):
        for rrn, d, due in (
            ("620000000002", date(2026, 7, 14), date(2026, 7, 15)),  # Tue -> Wed
            ("620000000003", date(2026, 9, 25), date(2026, 9, 28)),  # Fri -> (4th Sat, Sun) Mon
            ("620000000004", date(2026, 9, 26), date(2026, 9, 28)),  # Sat -> (Sun) Mon
        ):
            self.group(rrn, d=d, cbs=True)
            self.evaluate(d)
            r = self.result(rrn, d)
            with self.subTest(d=d):
                self.assertEqual(r.rule_code, "R13")
                self.assertEqual((r.status_category, r.priority), ("PENDING_INVESTIGATION", "MEDIUM"))
                self.assertEqual((r.tat_start_date, r.due_date, r.due_cutoff_time), (d, due, None))
                self.assertEqual(r.tat_label, "T+1 working day")
                self.assertFalse(r.rbi_compensation)
                self.assertIsNone(r.response_code)
                self.assertIn("In CBS only, not in this date's NPCI files", r.reason)
                self.assertIn("No automatic refund", r.reason)

    def test_r12_unchanged(self):
        self.group("620000000005", npci=True)
        self.evaluate()
        r = self.result("620000000005")
        self.assertEqual(
            (r.rule_code, r.status_category, r.priority, r.due_date, r.rbi_compensation),
            ("R12", "ADJUSTMENT_REQUIRED", "HIGH", date(2026, 7, 15), True),
        )

    def _snapshot(self, rrn, d):
        r = self.result(rrn, d)
        return (r.rule_code, r.status_category, r.priority, r.due_date, r.reason, r.facts)

    def test_next_days_npci_row_does_not_change_r13(self):
        d1 = D + timedelta(days=1)
        self.group("620000000006", cbs=True)
        self.evaluate()
        before = self._snapshot("620000000006", D)
        # D+1 now has an NPCI 00 row (and its own R12 group) for the same RRN.
        self.group("620000000006", d=d1, npci=True, code="00")
        self.npci_fact("620000000006", d1, "RB")
        self.evaluate(d1)
        self.assertEqual(self._snapshot("620000000006", D), before)  # evaluating D+1 left D alone
        self.evaluate()
        self.assertEqual(self._snapshot("620000000006", D), before)  # re-evaluating D ignores D+1
        r12 = self.result("620000000006", d1)
        self.assertEqual((r12.rule_code, r12.status_category), ("R12", "ADJUSTMENT_REQUIRED"))

    def test_evaluating_a_date_reads_and_writes_only_that_date(self):
        d1 = D + timedelta(days=1)
        self.group("620000000007", cbs=True)
        self.group("620000000007", d=d1, npci=True)
        self.evaluate()
        self.evaluate(d1)
        d_result = self.result("620000000007", D)
        with CaptureQueriesContext(connection) as ctx:
            self.evaluate(d1)
        sql = chr(10).join(q["sql"] for q in ctx.captured_queries)
        self.assertIn("'2026-07-15'", sql)
        self.assertNotIn("'2026-07-14'", sql)  # D is never read or written while evaluating D+1
        self.assertEqual(self.result("620000000007", D).pk, d_result.pk)  # D's result not superseded


# ---------------------------------------------------------------------------
class AgeingTests(EvaluatorTestBase):
    def test_overdue_and_bucket_derived_at_read_time(self):
        self.group("700000000001", timeout=True)  # R05, due Wed 15 Jul 16:00
        self.group("700000000002", cbs=True, npci=True)  # R09, no deadline
        self.evaluate()
        tz = timezone.get_current_timezone()
        before = datetime(2026, 7, 15, 15, 59, tzinfo=tz)
        after = datetime(2026, 7, 15, 16, 1, tzinfo=tz)

        def annotated(rrn, now):
            return RuleResult.objects.current().with_ageing(now=now).get(rrn=rrn)

        self.assertFalse(annotated("700000000001", before).is_overdue)
        self.assertTrue(annotated("700000000001", after).is_overdue)
        self.assertEqual(annotated("700000000001", after).ageing_bucket, "0-1 d")
        later = annotated("700000000001", datetime(2026, 9, 1, 9, 0, tzinfo=tz))
        self.assertEqual((later.age_days, later.ageing_bucket, later.is_overdue), (49, "45+ d", True))
        self.assertFalse(annotated("700000000002", after).is_overdue)
        by_today = RuleResult.objects.current().with_ageing(today=date(2026, 7, 16)).get(rrn="700000000001")
        self.assertTrue(by_today.is_overdue)

        # Model methods give the same answers.
        r = self.result("700000000001")
        self.assertEqual(r.ageing_bucket(date(2026, 7, 18)), "2-5 d")  # 4 days
        self.assertTrue(r.is_overdue(after))
        self.assertFalse(r.is_overdue(before))


class EntryPointAndAdminTests(EvaluatorTestBase):
    def test_command_task_and_admin(self):
        self.group("800000000001", cbs=True, npci=True)
        self.group("800000000002", d=D + timedelta(days=1), timeout=True)
        out = StringIO()
        call_command("evaluate_rules", "--business-date", D.isoformat(), stdout=out)
        self.assertIn("1 groups", out.getvalue())
        call_command("evaluate_rules", "--all-loaded", stdout=out)
        self.assertEqual(RuleEvaluationRun.objects.count(), 3)  # one per date, not one per group
        result = evaluate_rules(self.module.pk, D.isoformat())
        self.assertEqual(result["rule_counts"]["R09"], 1)
        self.assertEqual(result["superseded_count"], 1)

        user = get_user_model().objects.create_superuser("admin", "a@example.invalid", "x")
        self.client.force_login(user)
        r = self.result("800000000002", D + timedelta(days=1))
        for url in (
            "/admin/rule_engine/ruleresult/",
            "/admin/rule_engine/ruleresult/?rule_code=R05&priority__exact=HIGH",
            f"/admin/rule_engine/ruleresult/{r.pk}/change/",
            "/admin/rule_engine/ruleevaluationrun/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get("/admin/rule_engine/ruleresult/add/").status_code, 403)

    def test_command_without_dates_fails_loudly(self):
        with self.assertRaises(CommandError):
            call_command("evaluate_rules", "--all-loaded", stdout=StringIO())


class WorkingCalendarInjectionTests(EvaluatorTestBase):
    def test_calendar_can_be_passed_in(self):
        self.group("900000000001", drc=True)  # R03, T+1
        evaluate_business_date(self.module, D, calendar=WorkingDayCalendar(holidays={D + timedelta(1): "x"}))
        self.assertEqual(self.result("900000000001").due_date, date(2026, 7, 16))
