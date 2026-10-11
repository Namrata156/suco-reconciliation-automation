"""Case sync from rule results (design sections 1, 6, 7): creation,
idempotency, every re-run branch, the multiple-adjustment-rows flag, the
queue/ageing helper, the commands and the post-evaluation hook."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import RegisterType
from exception_management import services
from exception_management.models import CaseEvent, CaseStatus, Decision, EventType, ExceptionCase
from exception_management.sync import sync_cases_for_date
from exception_management.tests.base import D, CaseTestBase
from rule_engine.models import RuleResult
from rule_engine.tasks import evaluate_rules
from rule_engine.tests.test_evaluator import EvaluatorTestBase
from transactions.models import TransactionFact
from transactions.models.staging import StagingAdjustment


class CreationTests(CaseTestBase):
    def test_only_results_with_a_due_date_become_cases(self):
        self.result("100000000001", "R01")
        self.result("100000000002", "R05", cutoff=time(16))
        self.result("100000000003", "R13")
        self.result("100000000004", "R09")  # reconciled: no due date
        self.result("100000000005", "R10")
        self.result("100000000006", "R07b", due=None)  # no due date and TAT not unresolved: no case (A4 covers unresolved)
        s = self.sync()
        self.assertEqual((s.actionable_results, s.created, s.events_written), (3, 3, 3))
        self.assertEqual(s.cases_by_rule_status, {"R01/OPEN": 1, "R05/OPEN": 1, "R13/OPEN": 1})
        c = self.case("100000000002")
        r = RuleResult.objects.get(rrn="100000000002")
        self.assertEqual(
            (c.rule_result, c.match_group_id, c.rule_code, c.status_category, c.priority, c.due_date, c.due_cutoff_time,
             c.status, c.assigned_to, c.needs_review, c.result_missing),
            (r, r.match_group_id, "R05", "RETURN_REQUIRED", "HIGH", D + timedelta(days=1), time(16), "OPEN", None, False,
             False),
        )
        created = c.events.get()
        self.assertEqual((created.event_type, created.actor, created.new_status, created.source), ("CREATED", None, "OPEN", "test"))
        self.assertEqual(created.changes["rule_code"], [None, "R05"])

    def test_dates_are_independent(self):
        self.result("100000000001", "R01", D)
        self.result("100000000001", "R01", D + timedelta(days=1))
        self.sync(D)
        self.assertEqual(ExceptionCase.objects.count(), 1)
        self.sync(D + timedelta(days=1))
        self.assertEqual(ExceptionCase.objects.count(), 2)

    def test_idempotent(self):
        for i in range(3):
            self.result(f"10000000000{i}", "R01")
        self.sync()
        before = list(ExceptionCase.objects.values_list("id", "updated_at"))
        s = self.sync()
        self.assertEqual((s.created, s.unchanged, s.events_written), (0, 3, 0))
        self.assertEqual(list(ExceptionCase.objects.values_list("id", "updated_at")), before)
        self.assertEqual(CaseEvent.objects.count(), 3)

    def test_query_count_does_not_grow_with_cases(self):
        def queries():
            with CaptureQueriesContext(connection) as ctx:
                self.sync()
            return len(ctx.captured_queries)

        for i in range(3):
            self.result(f"20000000000{i}", "R01")
        small_create = queries()
        self.rerun({f"20000000000{i}": "R03" for i in range(3)})
        small_update = queries()
        for i in range(3, 12):
            self.result(f"2000000000{i:02d}", "R01", D + timedelta(days=1))
        d2 = D + timedelta(days=1)
        with CaptureQueriesContext(connection) as ctx:
            self.sync(d2)
        self.assertEqual(len(ctx.captured_queries), small_create)
        self.rerun({f"2000000000{i:02d}": "R03" for i in range(3, 12)}, d2)
        with CaptureQueriesContext(connection) as ctx:
            self.sync(d2)
        self.assertEqual(len(ctx.captured_queries), small_update)


class RerunTests(CaseTestBase):
    RRN = "100000000001"

    def setUp(self):
        super().setUp()
        self.c = self.make_case(self.RRN, "R01")
        self.m, self.chk = self.maker(), self.checker()

    def events(self, *types):
        return list(self.c.events.filter(event_type__in=types).order_by("id"))

    def test_same_rule_relinks_silently(self):
        old_result = self.c.rule_result_id
        self.rerun({self.RRN: "R01"})
        s = self.sync()
        self.assertEqual((s.relinked, s.events_written), (1, 0))
        c = self.case(self.RRN)
        self.assertNotEqual(c.rule_result_id, old_result)
        self.assertEqual(c.rule_result, RuleResult.objects.get(rrn=self.RRN, is_superseded=False))
        self.assertEqual(c.events.count(), 1)  # CREATED only

    def test_same_rule_new_due_date_is_recorded(self):
        services.take(self.c, self.m)
        self.rerun({self.RRN: "R01"}, **{self.RRN: {"due": D + timedelta(days=2)}})
        s = self.sync()
        self.assertEqual(s.result_updated, 1)
        c = self.case(self.RRN)
        self.assertEqual((c.due_date, c.status), (D + timedelta(days=2), CaseStatus.IN_PROGRESS))
        (e,) = self.events(EventType.RESULT_UPDATED)
        self.assertEqual(e.changes, {"due_date": ["2026-07-15", "2026-07-16"]})

    def test_rule_changed_open_case_is_updated(self):
        self.rerun({self.RRN: "R03"})
        s = self.sync()
        self.assertEqual(s.rule_changed, 1)
        c = self.case(self.RRN)
        self.assertEqual((c.rule_code, c.priority, c.default_action_type, c.status), ("R03", "MEDIUM", "DRC_104", "OPEN"))
        (e,) = self.events(EventType.RULE_CHANGED)
        self.assertEqual(e.changes["rule_code"], ["R01", "R03"])
        self.assertEqual(e.changes["default_action_type"], ["DRC_102", "DRC_104"])
        self.assertEqual((e.old_status, e.new_status, e.actor), ("OPEN", "OPEN", None))
        self.assertEqual(self.sync().events_written, 0)  # idempotent afterwards

    def test_rule_changed_in_progress_keeps_assignee(self):
        services.take(self.c, self.m)
        self.rerun({self.RRN: "R02"})
        self.sync()
        c = self.case(self.RRN)
        self.assertEqual((c.rule_code, c.status, c.assigned_to), ("R02", CaseStatus.IN_PROGRESS, self.m))

    def test_rule_changed_while_pending_approval_withdraws_the_action(self):
        services.take(self.c, self.m)
        a = services.submit(self.c, self.m)
        self.rerun({self.RRN: "R02"})
        s = self.sync()
        self.assertEqual((s.rule_changed, s.actions_withdrawn), (1, 1))
        c = self.case(self.RRN)
        a.refresh_from_db()
        self.assertEqual((c.status, c.assigned_to, c.rule_code), (CaseStatus.IN_PROGRESS, self.m, "R02"))
        self.assertEqual(a.decision, Decision.WITHDRAWN)
        (e,) = self.events(EventType.RULE_CHANGED)
        self.assertEqual((e.old_status, e.new_status, e.action_snapshot["decision"]), ("PENDING_APPROVAL", "IN_PROGRESS", "WITHDRAWN"))
        with self.assertRaises(services.InvalidTransition):
            services.approve(c, self.chk)
        services.submit(c, self.m)  # maker re-proposes under the new rule
        self.assertEqual(c.pending_action.action_type, "DRC_103")

    def _approved(self):
        services.take(self.c, self.m)
        services.submit(self.c, self.m)
        services.approve(self.c, self.chk)

    def test_rule_changed_after_approval_is_flagged_not_changed_silently(self):
        self._approved()
        self.rerun({self.RRN: "R02"})
        s = self.sync()
        self.assertEqual(s.flagged_for_review, 1)
        c = self.case(self.RRN)
        self.assertEqual((c.status, c.needs_review, c.assigned_to), (CaseStatus.APPROVED, True, self.m))
        self.assertIn("R01 to R02", c.needs_review_reason)
        self.assertEqual((c.approved_action.rule_code, c.approved_action.decision), ("R01", Decision.APPROVED))
        (e,) = self.events(EventType.FLAGGED_FOR_REVIEW)
        self.assertEqual(e.changes["rule_code"], ["R01", "R02"])
        self.rerun({self.RRN: "R02"})
        self.assertEqual(self.sync().events_written, 0)  # flagged once

    def test_rule_changed_after_completion_is_flagged(self):
        self._approved()
        services.mark_completed(self.c, self.m, external_reference="REF")
        self.rerun({self.RRN: "R03"})
        self.sync()
        c = self.case(self.RRN)
        self.assertEqual((c.status, c.needs_review), (CaseStatus.COMPLETED, True))

    def test_no_longer_needs_action_closes_open_case(self):
        services.take(self.c, self.m)
        self.rerun({self.RRN: "R10"})
        s = self.sync()
        self.assertEqual(s.superseded, 1)
        c = self.case(self.RRN)
        self.assertEqual((c.status, c.rule_code, c.due_date), (CaseStatus.CLOSED_SUPERSEDED, "R10", None))
        (e,) = self.events(EventType.SUPERSEDED)
        self.assertEqual((e.old_status, e.new_status), ("IN_PROGRESS", "CLOSED_SUPERSEDED"))
        self.rerun({self.RRN: "R10"})
        s = self.sync()
        self.assertEqual((s.relinked, s.events_written), (1, 0))

    def test_no_longer_needs_action_withdraws_pending(self):
        services.take(self.c, self.m)
        a = services.submit(self.c, self.m)
        self.rerun({self.RRN: "R09"})
        self.sync()
        a.refresh_from_db()
        self.assertEqual((self.case(self.RRN).status, a.decision), (CaseStatus.CLOSED_SUPERSEDED, Decision.WITHDRAWN))

    def test_no_longer_needs_action_after_approval_is_flagged(self):
        self._approved()
        self.rerun({self.RRN: "R09"})
        self.sync()
        c = self.case(self.RRN)
        self.assertEqual((c.status, c.needs_review), (CaseStatus.APPROVED, True))
        self.assertIn("no longer needs action", c.needs_review_reason)

    def test_key_without_current_result(self):
        self.rerun({})  # the group's RRN dropped out of the files
        s = self.sync()
        c = self.case(self.RRN)
        self.assertEqual((s.superseded, c.status, c.result_missing, c.rule_code), (1, CaseStatus.CLOSED_SUPERSEDED, True, "R01"))
        self.assertEqual(self.sync().events_written, 0)

    def test_closed_case_reopens_when_action_needed_again(self):
        services.take(self.c, self.m)
        self.rerun({self.RRN: "R10"})
        self.sync()
        self.rerun({self.RRN: "R02"})
        s = self.sync()
        self.assertEqual(s.reopened, 1)
        c = self.case(self.RRN)
        self.assertEqual((c.status, c.assigned_to, c.rule_code), (CaseStatus.OPEN, None, "R02"))
        self.assertEqual(len(self.events(EventType.REOPENED)), 1)


class MultipleAdjustmentRowsTests(CaseTestBase):
    def setUp(self):
        super().setUp()
        self._files, self._row = {}, 0

    def adjustment_row(self, rrn, d=D):
        """An Adjustment fact for rrn on d whose original date is not loaded
        (no CBS facts), so it belongs to d's group (presence rule). Uses the
        evaluator tests' file/fact builders."""
        f = EvaluatorTestBase.file(self, RegisterType.ADJUSTMENT, d)
        self._row += 1
        staging = StagingAdjustment.objects.create(
            uploaded_file=f, row_number=self._row, row_reference=f":{self._row}",
            raw_cells={"4": "Credit Adjustment", "5": "SSK", "8": "01-06-2026"}, txn_date_raw="01-06-2026",
        )
        return EvaluatorTestBase.fact(self, RegisterType.ADJUSTMENT, rrn, d, staging_row_id=staging.id, uploaded_file=f)

    def test_flag(self):
        self.adjustment_row("100000000001")
        self.adjustment_row("100000000001")
        self.adjustment_row("100000000002")
        self.result("100000000001", "R06")
        self.result("100000000002", "R06")
        s = self.sync()
        self.assertEqual(s.multiple_adjustment_rows, 1)
        self.assertTrue(self.case("100000000001").multiple_adjustment_rows)
        self.assertFalse(self.case("100000000002").multiple_adjustment_rows)
        self.adjustment_row("100000000002")  # a second row arrives later
        s = self.sync()
        self.assertEqual((s.adjustment_flag_changed, s.multiple_adjustment_rows), (1, 2))
        e = self.case("100000000002").events.get(event_type=EventType.ADJUSTMENT_ROWS_CHANGED)
        self.assertEqual(e.changes, {"multiple_adjustment_rows": [False, True]})
        self.assertEqual(TransactionFact.objects.count(), 4)


class QueueTests(CaseTestBase):
    def test_order_flags_and_ageing_from_rule_result(self):
        self.result("100000000001", "R01", due=D + timedelta(days=1))  # LOW
        self.result("100000000002", "R13", due=D + timedelta(days=1))  # MEDIUM
        self.result("100000000003", "R12", due=D + timedelta(days=5))  # HIGH, not overdue
        self.result("100000000004", "R05", due=D + timedelta(days=1), cutoff=time(16))  # HIGH, overdue after 16:00
        self.result("100000000005", "R07x", due=D + timedelta(days=9))  # HIGH, attention
        self.sync()
        now = timezone.make_aware(datetime.combine(D + timedelta(days=1), time(17)))
        q = list(ExceptionCase.objects.queue(now=now))
        self.assertEqual([c.rrn[-1] for c in q], ["4", "3", "5", "2", "1"])
        by_rrn = {c.rrn[-1]: c for c in q}
        self.assertTrue(by_rrn["4"].is_overdue)
        self.assertTrue(by_rrn["4"].needs_attention)
        self.assertTrue(by_rrn["5"].needs_attention)  # R07x
        self.assertFalse(by_rrn["3"].needs_attention)
        aged = {r.rrn[-1]: r for r in RuleResult.objects.with_ageing(now=now)}
        for k, c in by_rrn.items():
            self.assertEqual((c.age_days, c.ageing_bucket, c.is_overdue),
                             (aged[k].age_days, aged[k].ageing_bucket, aged[k].is_overdue))
        before_cutoff = timezone.make_aware(datetime.combine(D + timedelta(days=1), time(15)))
        self.assertFalse(ExceptionCase.objects.queue(now=before_cutoff).get(rrn="100000000004").is_overdue)

    def test_queue_excludes_finished_and_filters(self):
        c1 = self.make_case("100000000001")
        self.make_case("100000000002")
        m = self.maker()
        services.take(c1, m)
        services.submit(c1, m)
        self.assertEqual(list(ExceptionCase.objects.assigned_to(m)), [self.case("100000000001")])
        self.assertEqual(ExceptionCase.objects.awaiting_approval().count(), 1)
        self.rerun({"100000000001": "R01"})  # 2 dropped -> closed
        self.sync()
        self.assertEqual([c.rrn for c in ExceptionCase.objects.queue()], ["100000000001"])


class CommandTests(CaseTestBase):
    def test_sync_cases_command(self):
        self.result("100000000001", "R01")
        self.result("100000000002", "R05", D + timedelta(days=1))
        out = StringIO()
        call_command("sync_cases", "--date", D.isoformat(), stdout=out)
        self.assertIn("1 created", out.getvalue())
        call_command("sync_cases", "--all-evaluated", stdout=out)
        self.assertEqual(ExceptionCase.objects.count(), 2)
        self.assertIn("R05/OPEN 1", out.getvalue())
        with self.assertRaises(CommandError):
            call_command("sync_cases", "--date", D.isoformat(), "--payment-module", "NOPE", stdout=out)


class EvaluationHookTests(EvaluatorTestBase):
    """The real evaluator: evaluate_rules (command and Celery task) syncs cases."""

    def test_command_and_task_sync_cases(self):
        self.group("800000000001", cbs=True, npci=True)  # R09: no case
        self.group("800000000002", timeout=True)  # R05: RET, due T+1 by 16:00
        out = StringIO()
        call_command("evaluate_rules", "--business-date", D.isoformat(), stdout=out)
        self.assertIn("1 created", out.getvalue())
        c = ExceptionCase.objects.get()
        self.assertEqual((c.rrn, c.rule_code, c.default_action_type, c.default_response_code.code, c.due_cutoff_time),
                         ("800000000002", "R05", "RET", "115", time(16)))
        first_result = c.rule_result_id
        result = evaluate_rules(self.module.pk, D.isoformat())
        self.assertEqual((result["cases"]["relinked"], result["cases"]["events_written"]), (1, 0))
        c.refresh_from_db()
        self.assertNotEqual(c.rule_result_id, first_result)
        self.assertFalse(c.rule_result.is_superseded)
        self.assertEqual(CaseEvent.objects.count(), 1)

    def test_sync_function_matches_command(self):
        self.group("800000000003", timeout=True)
        self.evaluate()
        s = sync_cases_for_date(self.module, D)
        self.assertEqual(s.created, 1)
        self.assertEqual(ExceptionCase.objects.get().due_date, date(2026, 7, 15))
