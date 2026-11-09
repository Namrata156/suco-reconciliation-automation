"""
Owner decision 2026-11-09: no-money cases (R01 DRC 102, R03 DRC 104, R04a
TCC 102) close by themselves when the NPCI file holding them is generated;
money cases (R02, R05, R06...) keep the Maker-Checker workflow. Which cases
close, which stay, the audit, a second file of the date, re-runs, the
backfill command and the API output. Synthetic data.
"""
from __future__ import annotations

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.utils import timezone

from api.tests.test_phase6_daily import csv_lines
from api.tests.test_v1 import V1TestBase
from core.models import OperationLog, OperationType
from exception_management import services
from exception_management.actions import ActionType
from exception_management.models import CaseEvent, CaseStatus, Decision, EventType, ExceptionCase, ProposedAction
from exception_management.roles import Role
from npci_files import auto_close
from npci_files.generator import generate_file, plan_file
from npci_files.models import NpciFileRow
from npci_files.tests.base import D, NpciTestBase

UPI = "/api/v1/upi"


def status(case) -> str:
    return ExceptionCase.objects.get(pk=case.pk).status


def closing_events(case=None):
    qs = CaseEvent.objects.filter(event_type=EventType.CLOSED_BY_SYSTEM)
    return qs.filter(case=case) if case is not None else qs


class AutoCloseTests(NpciTestBase):
    def test_the_list_lives_in_one_constant(self):
        self.assertEqual(auto_close.AUTO_CLOSE_REPLIES,
                         {"R01": ("DRC", "102"), "R03": ("DRC", "104"), "R04a": ("TCC", "102")})

    def test_each_no_money_rule_closes_when_the_file_is_generated(self):
        r01 = self.drc_case("100000000001", "R01")
        r03 = self.drc_case("100000000002", "R03")
        r04a = self.timeout_case("100000000003", "R04a")
        plan = plan_file(self.module, D)
        self.assertTrue(all(r.closes_case for r in plan.rows))
        self.assertTrue(any("3 case(s) with a no-money reply (R01: 1, R03: 1, R04a: 1) are closed by the system"
                            in w for w in plan.warnings))
        for case in (r01, r03, r04a):
            self.assertEqual(status(case), CaseStatus.OPEN)  # the preview changes nothing
        f = generate_file(self.chk, self.module, D)
        for case in (r01, r03, r04a):
            with self.subTest(rule=case.rule_code):
                self.assertEqual(status(case), CaseStatus.COMPLETED)
                self.assertEqual(closing_events(case).count(), 1)
        log = OperationLog.objects.get(operation=OperationType.NPCI_FILE_GENERATED)
        self.assertEqual((log.details["closed_by_system"], log.details["closed_by_system_rules"]),
                         (3, {"R01": 1, "R03": 1, "R04a": 1}))
        self.assertEqual(f.row_count, 3)

    def test_audit_event_names_the_file_and_the_system(self):
        case = self.drc_case("100000000001", "R01")
        f = generate_file(self.chk, self.module, D)
        e = closing_events(case).get()
        self.assertIsNone(e.actor)
        self.assertEqual(e.actor_username, "")
        self.assertEqual((e.old_status, e.new_status, e.source),
                         (CaseStatus.OPEN, CaseStatus.COMPLETED, auto_close.GENERATION_SOURCE))
        self.assertIn(f"NPCI file {f.file_name} (file id {f.pk})", e.remarks)
        self.assertIn("owner decision 2026-11-09", e.remarks)
        self.assertEqual(e.changes["npci_file_id"], [None, f.pk])
        self.assertEqual(e.changes["npci_file_name"], [None, "SSKUPI14072026.csv"])
        self.assertEqual(e.changes["closed_by"], [None, "system"])
        self.assertIsNone(e.action_snapshot)  # an open case had no proposal
        # the event names the file; the file's row still points at the case
        self.assertEqual(NpciFileRow.objects.get(case=case).npci_file_id, f.pk)

    def test_cases_already_in_the_workflow_with_the_no_money_reply_close_too(self):
        taken = self.drc_case("100000000001", "R01")
        services.take(taken, self.mk)
        submitted = self.drc_case("100000000002", "R03")
        pending = self.submit_only(submitted)
        approved = self.timeout_case("100000000003", "R04a")
        approved_action = self.approve(approved)
        generate_file(self.chk, self.module, D)
        for case in (taken, submitted, approved):
            c = ExceptionCase.objects.get(pk=case.pk)
            self.assertEqual((c.status, c.assigned_to), (CaseStatus.COMPLETED, None), case.rule_code)
        e = closing_events(taken).get()
        self.assertEqual((e.old_status, e.changes["assigned_to"]), (CaseStatus.IN_PROGRESS, [self.mk.username, None]))
        # the pending proposal is withdrawn, not left for a Checker
        pending.refresh_from_db()
        self.assertEqual(pending.decision, Decision.WITHDRAWN)
        self.assertIsNone(pending.decided_by)
        self.assertIn("Withdrawn: Closed by the system", pending.decision_remarks)
        self.assertFalse(ProposedAction.objects.filter(decision=Decision.PENDING).exists())
        self.assertEqual(closing_events(submitted).get().action_snapshot["decision"], Decision.WITHDRAWN)
        # the approved action is recorded done, by nobody, with the file as its reference
        approved_action.refresh_from_db()
        self.assertEqual((approved_action.decision, approved_action.completed_by, approved_action.external_reference),
                         (Decision.APPROVED, None, "SSKUPI14072026.csv"))
        self.assertIsNotNone(approved_action.completed_at)

    def test_a_changed_reply_stays_with_people(self):
        submitted = self.drc_case("100000000001", "R01")
        services.take(submitted, self.mk)
        services.submit(submitted, self.mk, action_type=ActionType.DRC_104)  # the Maker's own reply
        approved = self.drc_case("100000000002", "R03")
        self.approve(approved, action_type=ActionType.DRC_102)  # a Checker-approved different reply
        f = generate_file(self.chk, self.module, D)
        self.assertEqual(sorted(f.rows.values_list("code", flat=True)), ["102", "104"])  # both are in the file
        self.assertEqual(status(submitted), CaseStatus.PENDING_APPROVAL)
        self.assertEqual(status(approved), CaseStatus.APPROVED)
        self.assertFalse(closing_events().exists())
        self.assertEqual(OperationLog.objects.get(operation=OperationType.NPCI_FILE_GENERATED)
                         .details["closed_by_system"], 0)

    def test_money_rules_never_close(self):
        r02 = self.drc_case("100000000001", "R02")  # DRC 103
        r05 = self.timeout_case("100000000002", "R05")  # RET 115
        r06 = self.rrc_case("100000000003")  # manual credit + RRC 501
        approved_r02 = self.drc_case("100000000004", "R02")
        self.approve(approved_r02)
        r04b = self.timeout_case("100000000005", "R04b")  # TCC 102, but not a no-money rule
        self.approve(r04b)
        plan = plan_file(self.module, D)
        self.assertFalse(any(r.closes_case for r in plan.rows))
        f = generate_file(self.chk, self.module, D)
        self.assertEqual(f.row_count, 5)
        for case, expected in ((r02, CaseStatus.OPEN), (r05, CaseStatus.OPEN), (r06, CaseStatus.OPEN),
                               (approved_r02, CaseStatus.APPROVED), (r04b, CaseStatus.APPROVED)):
            self.assertEqual(status(case), expected, case.rule_code)
        self.assertFalse(closing_events().exists())

    def test_flagged_for_review_does_not_close(self):
        flagged = self.drc_case("100000000001", "R01")
        self.approve(flagged)
        ExceptionCase.objects.filter(pk=flagged.pk).update(needs_review=True, needs_review_reason="re-run")
        self.drc_case("100000000002", "R01")
        f = generate_file(self.chk, self.module, D, confirm_partial=True)  # the flagged one is left out
        self.assertEqual(f.row_count, 1)
        self.assertEqual(status(flagged), CaseStatus.APPROVED)
        # included first and flagged afterwards: the backfill does not close it either
        late = self.drc_case("100000000003", "R01")
        with mock.patch.object(auto_close, "close_included_cases", return_value=auto_close.FileClosure(None)):
            f2 = generate_file(self.chk, self.module, D, confirm_partial=True)
        ExceptionCase.objects.filter(pk=late.pk).update(needs_review=True, needs_review_reason="re-run")
        result = auto_close.close_included_cases(f2)
        self.assertEqual((result.closed, dict(result.skipped)), ([], {auto_close.Skip.NEEDS_REVIEW: 1}))
        self.assertEqual(status(late), CaseStatus.OPEN)

    def test_rejected_no_money_reply_is_not_closed_by_the_backfill(self):
        case = self.drc_case("100000000001", "R01")
        with mock.patch.object(auto_close, "close_included_cases", return_value=auto_close.FileClosure(None)):
            f = generate_file(self.chk, self.module, D)
        services.take(case, self.mk)
        services.submit(case, self.mk)
        services.reject(case, self.chk, reason="not reversed in CBS after all")
        result = auto_close.close_included_cases(f)
        self.assertEqual(dict(result.skipped), {auto_close.Skip.HELD: 1})
        self.assertEqual(status(case), CaseStatus.IN_PROGRESS)

    def test_second_file_closes_only_its_own_cases_once(self):
        first = self.drc_case("100000000001", "R01")
        f1 = generate_file(self.chk, self.module, D)
        self.assertEqual(status(first), CaseStatus.COMPLETED)
        self.rerun({"100000000001": "R01", "100000000002": "R03"})  # a re-run brings a new case
        self.sync()
        second = self.case("100000000002")
        self.dispute(second, "DRC", "10", "000000009999")
        self.assertEqual(status(first), CaseStatus.COMPLETED)  # the re-run kept it closed
        f2 = generate_file(self.chk, self.module, D)
        self.assertEqual((f2.file_name, f2.row_count), ("SSKUPI14072026_2.csv", 1))
        self.assertEqual(status(second), CaseStatus.COMPLETED)
        self.assertEqual(closing_events(first).count(), 1)
        self.assertEqual(closing_events(second).get().changes["npci_file_id"], [None, f2.pk])
        self.assertEqual(closing_events(first).get().changes["npci_file_id"], [None, f1.pk])
        # running the closure of either file again changes nothing
        self.assertEqual(auto_close.close_included_cases(f1).closed, [])
        self.assertEqual(auto_close.close_included_cases(f2).closed, [])
        self.assertEqual(closing_events().count(), 2)

    def test_reruns_never_reopen_a_system_closed_case(self):
        same = self.drc_case("100000000001", "R01")
        changed = self.drc_case("100000000002", "R01")
        lapsed = self.drc_case("100000000003", "R01")
        generate_file(self.chk, self.module, D)
        # same rule: re-linked silently; rule changed: flagged; no longer needs action: flagged, not superseded
        self.rerun({"100000000001": "R01", "100000000002": "R02"})
        summary = self.sync()
        self.assertEqual((summary.reopened, summary.superseded, summary.flagged_for_review), (0, 0, 2))
        for case in (same, changed, lapsed):
            self.assertEqual(status(case), CaseStatus.COMPLETED)
        self.assertFalse(self.case("100000000001").needs_review)
        self.assertTrue(self.case("100000000002").needs_review)
        self.assertTrue(self.case("100000000003").needs_review)
        plan = plan_file(self.module, D)  # the file never changes; the preview warns
        self.assertEqual({c.pk for c, _r in plan.changed_since_included}, {changed.pk, lapsed.pk})
        self.assertEqual(plan.rows, [])
        # the Checker clears the review: still closed, no second closing event
        services.clear_review(self.case("100000000002"), self.chk, remarks="DRC 102 already sent; ops told")
        self.assertEqual(status(changed), CaseStatus.COMPLETED)
        self.assertEqual(closing_events().count(), 3)
        self.sync()  # a further re-run with nothing new is fine
        self.assertEqual(status(same), CaseStatus.COMPLETED)

    def test_rule_defaults_mode_marks_nothing(self):
        self.drc_case("100000000001", "R01")
        self.assertFalse(any(r.closes_case for r in plan_file(self.module, D, rule_defaults=True).rows))


class BackfillCommandTests(NpciTestBase):
    def _old_file(self):
        """A file generated before the owner decision: nothing closed."""
        with mock.patch.object(auto_close, "close_included_cases", return_value=auto_close.FileClosure(None)):
            return generate_file(self.chk, self.module, D)

    def test_dry_run_counts_then_the_real_run_closes_once(self):
        r01 = self.drc_case("100000000001", "R01")
        r04a = self.timeout_case("100000000002", "R04a")
        r02 = self.drc_case("100000000003", "R02")
        changed = self.drc_case("100000000004", "R03")
        f = self._old_file()
        services.take(changed, self.mk)
        services.submit(changed, self.mk, action_type=ActionType.DRC_102)  # another reply since the file
        services.take(r04a, self.mk)
        out = StringIO()
        call_command("close_included_no_money_cases", "--dry-run", stdout=out)
        text = out.getvalue()
        self.assertIn("DRY RUN", text)
        self.assertIn(f"{f.file_name} (id {f.pk}, 4 rows): would close 2 [R01: 1, R04a: 1; status before: "
                      "IN_PROGRESS: 1, OPEN: 1]", text)
        self.assertIn("Total would close: 2.", text)
        self.assertIn(f"{auto_close.Skip.NOT_A_NO_MONEY_RULE}: 1", text)
        self.assertIn(f"{auto_close.Skip.REPLY_CHANGED}: 1", text)
        self.assertFalse(closing_events().exists())
        self.assertEqual(status(r01), CaseStatus.OPEN)

        out = StringIO()
        call_command("close_included_no_money_cases", stdout=out)
        self.assertIn("Total closed: 2.", out.getvalue())
        self.assertEqual((status(r01), status(r04a), status(r02), status(changed)),
                         (CaseStatus.COMPLETED, CaseStatus.COMPLETED, CaseStatus.OPEN, CaseStatus.PENDING_APPROVAL))
        self.assertEqual(set(closing_events().values_list("source", flat=True)),
                         {"manage.py close_included_no_money_cases"})
        out = StringIO()
        call_command("close_included_no_money_cases", stdout=out)  # again: nothing more
        self.assertIn("Total closed: 0.", out.getvalue())
        self.assertEqual(closing_events().count(), 2)


class AutoCloseApiTests(V1TestBase, NpciTestBase):
    """The case list / detail, B4 and B6 reports and the audit feed show a
    system-closed case as closed, "closed by system"."""

    def setUp(self):
        super().setUp()
        self.mk, self.chk = self.users[Role.MAKER], self.users[Role.CHECKER]
        self.closed = self.drc_case("100000000001", "R01")
        self.submitted = self.drc_case("100000000002", "R03")
        self.submit_only(self.submitted)
        self.open = self.drc_case("100000000003", "R02")
        self.file = generate_file(self.chk, self.module, D)

    def get(self, url, params=None, who=Role.AUDITOR):
        r = self.as_(who).get(url, params or {})
        self.assertEqual(r.status_code, 200, r.content[:300])
        return r.json()

    def test_case_detail_and_list(self):
        body = self.get(f"{UPI}/cases/{self.closed.pk}/")
        self.assertEqual((body["status"], body["closed_by_system"], body["assigned_to"]),
                         (CaseStatus.COMPLETED, True, None))
        self.assertEqual(body["npci_file"]["file_name"], self.file.file_name)
        self.assertEqual(body["available_transitions"], [])  # nothing left for people (Auditor)
        event = [e for e in body["events"] if e["event_type"] == EventType.CLOSED_BY_SYSTEM][0]
        self.assertIsNone(event["actor_username"] or None)
        self.assertIn(self.file.file_name, event["remarks"])
        self.assertFalse(self.get(f"{UPI}/cases/{self.open.pk}/")["closed_by_system"])
        rows = {r["id"]: r for r in self.get(f"{UPI}/cases/", {"status": "all"})["results"]}
        self.assertEqual({pk: (r["status"], r["closed_by_system"]) for pk, r in rows.items()},
                         {self.closed.pk: (CaseStatus.COMPLETED, True), self.submitted.pk: (CaseStatus.COMPLETED, True),
                          self.open.pk: (CaseStatus.OPEN, False)})
        active = [r["id"] for r in self.get(f"{UPI}/cases/")["results"]]  # the work queue: open cases only
        self.assertEqual(active, [self.open.pk])

    def test_meta_labels_the_new_event_type(self):
        meta = self.get(f"{UPI}/meta/")
        labels = {e["value"]: e["label"] for e in meta["event_types"]}
        self.assertEqual(labels[EventType.CLOSED_BY_SYSTEM],
                         "Closed by the system (no-money reply included in an NPCI file)")

    def test_exception_ageing_and_approval_reports(self):
        july = {"date_from": "2026-07-01", "date_to": "2026-07-31"}
        b4 = self.get(f"{UPI}/reports/exceptions/", {**july, "status": "all"})
        rows = {r["id"]: r for r in b4["results"]}
        self.assertTrue(rows[self.closed.pk]["closed_by_system"])
        self.assertIsNotNone(rows[self.closed.pk]["closed_at"])
        self.assertFalse(rows[self.open.pk]["closed_by_system"])
        self.assertIsNone(rows[self.open.pk]["closed_at"])
        only = self.get(f"{UPI}/reports/exceptions/", {**july, "status": "all", "closed_by_system": "true"})
        self.assertEqual(sorted(r["id"] for r in only["results"]), sorted([self.closed.pk, self.submitted.pk]))
        active = self.get(f"{UPI}/reports/exceptions/", july)  # default: active cases only
        self.assertEqual([r["id"] for r in active["results"]], [self.open.pk])
        csv = self.as_(Role.AUDITOR).get(f"{UPI}/reports/exceptions/", {**july, "status": "all", "export": "csv"})
        self.assertEqual(csv.status_code, 200)
        self.assertIn("Closed by system", " ".join(csv_lines(csv)))
        # B5 lists active cases only: the closed ones are not there
        b5 = self.get(f"{UPI}/reports/ageing/")
        self.assertEqual(b5["totals"]["total"]["count"], 1)
        # B6: the withdrawn proposal of the system-closed case
        today = timezone.localdate().isoformat()  # B6 dates are decision dates; the withdrawal is today
        b6 = self.get(f"{UPI}/reports/approvals/", {"date_from": today, "date_to": today})
        row = {r["case_id"]: r for r in b6["results"]}[self.submitted.pk]
        self.assertEqual((row["decision"], row["checker"], row["closed_by_system"]),
                         (Decision.WITHDRAWN, None, True))

    def test_audit_feed_sentence(self):
        from api.audit import case_event_summary

        e = closing_events(self.closed).get()
        text = case_event_summary({"actor_username": e.actor_username, "case_id": self.closed.pk,
                                   "event_type": e.event_type, "changes": e.changes, "action_snapshot": None,
                                   "self_approved": False, "event_type_label": e.get_event_type_display()})
        self.assertEqual(text, f"The system closed case #{self.closed.pk}: no-money reply included in NPCI file "
                               f"{self.file.file_name}")
