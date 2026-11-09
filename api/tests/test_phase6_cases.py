"""
Phase 6 Slice 6.3 (docs/phase6_api_contract.txt B4, B5, B6, B6s, C2, C4;
owner answers D4 and "P"): exception, ageing and approval reports. Every
RRN, amount and user here is made up.
"""
from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from api.tests.test_phase6_daily import D, UPI, Phase6Base, csv_lines
from api.tests.test_v1 import ALL_ROLES
from exception_management import services
from exception_management.models import (
    CaseStatus,
    ChangeRequestStatus,
    ExceptionCase,
    ProposedAction,
    ResponseCodeChangeRequest,
)
from exception_management.roles import Role
from reconciliation.models import RegisterPresence
from rule_engine.models import ResponseCode, RuleResult
from settlement import cases as settlement_cases
from settlement import tolerance
from settlement.models import ResultStatus

A, O, M, C, U = Role.ADMINISTRATOR, Role.OPERATIONS_USER, Role.MAKER, Role.CHECKER, Role.AUDITOR
EXC, AGE = f"{UPI}/reports/exceptions/", f"{UPI}/reports/ageing/"
APPR, APPR_S = f"{UPI}/reports/approvals/", f"{UPI}/reports/approvals/settings/"
JULY = {"date_from": "2026-07-01", "date_to": "2026-07-31"}


def leg(case, name, amount):
    RegisterPresence.objects.create(match_group=case.match_group, leg=name, present=True, amount=Decimal(amount))


class CaseReportBase(Phase6Base):
    def setUp(self):
        super().setUp()
        self.mk, self.chk = self.users[M], self.users[C]
        # NPCI and CBS rows: NPCI's amount wins (D4); CBS only: CBS.
        self.both = self.make_case("100000000001", "R01", D)
        leg(self.both, "NPCI", "140.50")
        leg(self.both, "CBS", "999.00")
        self.cbs_only = self.make_case("100000000002", "R13", D)
        leg(self.cbs_only, "CBS", "-35.00")
        self.none = self.make_case("100000000003", "R01", D + timedelta(days=1))
        result = self.settlement(D, ResultStatus.UNEXPLAINED, unexplained="12.00")
        settlement_cases.sync_settlement_case(result, source="test")
        self.settle = ExceptionCase.objects.get(case_type="SETTLEMENT")


# ---------------------------------------------------------------------------
# B4 exception report
# ---------------------------------------------------------------------------
class ExceptionReportTests(CaseReportBase):
    def test_rows_amounts_and_summary(self):
        self.assert_read_matrix(EXC, JULY)
        services.take(self.both, self.mk)
        services.add_remark(self.both, self.mk, "first")
        services.add_remark(self.both, self.mk, "=checked with ops")
        services.submit(self.both, self.mk)
        services.approve(self.both, self.chk)
        services.mark_completed(self.both, self.mk, external_reference="NPCI-REF-1")
        body = self.ok(U, EXC, {**JULY, "status": "all"})
        self.assertNotIn("priority", json.dumps(body))  # owner 2026-11-08 "P"
        rows = {r["id"]: r for r in body["results"]}
        both = rows[self.both.pk]
        self.assertEqual((both["amount"], both["status"], both["last_remark"], both["link"]),
                         ("140.50", "COMPLETED", "=checked with ops", f"/upi/cases/{self.both.pk}"))
        self.assertEqual({k: both["proposed_action"][k] for k in ("decision", "reference")},
                         {"decision": "APPROVED", "reference": "NPCI-REF-1"})
        self.assertIsNotNone(both["closed_at"])
        self.assertEqual(rows[self.cbs_only.pk]["amount"], "-35.00")
        self.assertIsNone(rows[self.none.pk]["amount"])
        self.assertEqual(rows[self.settle.pk]["amount"], "12.00")
        self.assertIsNone(rows[self.cbs_only.pk]["closed_at"])
        self.assertEqual(rows[self.cbs_only.pk]["ageing_bucket"] != "", True)
        s = body["summary"]
        self.assertEqual(s["by_status"], [{"status": "OPEN", "count": 3}, {"status": "COMPLETED", "count": 1}])
        self.assertEqual({r["rule_code"]: (r["count"], r["open"]) for r in s["by_rule"]},
                         {"R01": (2, 1), "R13": (1, 1), "": (1, 1)})
        active = self.ok(U, EXC, JULY)  # default: active only, as the queue
        self.assertEqual(active["count"], 3)
        self.assertEqual(active["report"]["filters"]["status"], "active")
        lines = csv_lines(self.as_(U).get(EXC, {**JULY, "status": "all", "export": "csv"}))
        self.assertTrue(lines[10].startswith("Case,Business date,RRN,Case type,Rule"))
        self.assertIn("'=checked with ops", "\n".join(lines))
        self.assertIn('"=""100000000002"""', "\n".join(lines))
        self.assertIn(",-35.00,", "\n".join(lines))  # amounts are never prefixed
        self.assertEqual(self.exports().get().details["file_name"],
                         "UPI_exception_report_2026-07-01_to_2026-07-31_cases.csv")

    def test_filters(self):
        services.take(self.cbs_only, self.mk)
        ids = lambda **q: sorted(r["id"] for r in self.ok(U, EXC, {**JULY, **q})["results"])  # noqa: E731
        self.assertEqual(ids(rule_code="R01,R13"), sorted([self.both.pk, self.cbs_only.pk, self.none.pk]))
        self.assertEqual(ids(case_type="SETTLEMENT"), [self.settle.pk])
        self.assertEqual(ids(assigned_to=self.mk.username), [self.cbs_only.pk])
        self.assertEqual(ids(assigned_to="none"), sorted([self.both.pk, self.none.pk, self.settle.pk]))
        self.assertEqual(ids(status="IN_PROGRESS"), [self.cbs_only.pk])
        self.assertEqual(ids(date_from="2026-07-15", date_to="2026-07-15"), [self.none.pk])
        self.assertEqual(len(ids(overdue="false")) + len(ids(overdue="true")), 4)
        by_rrn = self.ok(U, EXC, {**JULY, "ordering": "-rrn"})["results"]
        self.assertEqual([r["rrn"] for r in by_rrn][:2], ["100000000003", "100000000002"])
        for params, field in (({**JULY, "status": "DONE"}, "status"), ({**JULY, "case_type": "X"}, "case_type"),
                              ({**JULY, "overdue": "maybe"}, "overdue"),
                              ({**JULY, "ordering": "priority"}, "ordering"),
                              ({"date_from": "2026-07-01"}, "date_to")):
            with self.subTest(params=params):
                self.bad(U, EXC, params, field)


# ---------------------------------------------------------------------------
# B5 ageing report, C2 ageing_bucket filter on the queue
# ---------------------------------------------------------------------------
class AgeingReportTests(CaseReportBase):
    def test_cells_links_and_not_aged(self):
        self.assert_read_matrix(AGE)
        # Age from the TAT start: 3 days old for the R13 case, overdue (due date passed).
        today = timezone.localdate()
        RuleResult.objects.filter(rrn="100000000002").update(tat_start_date=today - timedelta(days=3),
                                                              due_date=today - timedelta(days=1))
        RuleResult.objects.filter(rrn__in=["100000000001", "100000000003"]).update(tat_start_date=today,
                                                                                   due_date=today + timedelta(days=1))
        body = self.ok(M, AGE)
        self.assertNotIn("priority", json.dumps(body))
        labels = [b["label"] for b in body["buckets"]]
        self.assertEqual(labels[:2], ["0-1 d", "2-5 d"])
        rules = {r["rule_code"]: r for r in body["by_rule"]}
        self.assertEqual(list(rules), ["R01", "R13"])
        r13 = rules["R13"]["cells"][1]
        self.assertEqual((r13["bucket"], r13["count"], r13["amount"], r13["overdue"]), ("2-5 d", 1, "-35.00", 1))
        self.assertEqual(r13["link"], "/upi/cases?status=&rule_code=R13&ageing_bucket=2-5%20d")
        self.assertEqual(rules["R01"]["total"], {"count": 2, "amount": "140.50", "overdue": 0})
        self.assertEqual(body["totals"]["total"], {"count": 3, "amount": "105.50", "overdue": 1})
        self.assertEqual(body["totals"]["cells"][-1]["link"], "/upi/cases?status=&ageing_bucket=45%2B%20d")
        self.assertEqual(body["not_aged"], {"count": 1, "amount": "12.00",
                                            "link": "/upi/cases?status=&case_type=SETTLEMENT"})
        self.assertEqual(body["oldest_case"], {"id": self.cbs_only.pk, "rrn": "100000000002", "age_days": 3,
                                               "link": f"/upi/cases/{self.cbs_only.pk}"})
        filtered = self.ok(M, AGE, {"rule_code": "R13", "case_type": "TRANSACTION"})
        self.assertEqual(([r["rule_code"] for r in filtered["by_rule"]], filtered["not_aged"]["count"]), (["R13"], 0))
        self.assertIn("case_type=TRANSACTION", filtered["totals"]["cells"][0]["link"])
        self.bad(M, AGE, {"date_from": "2026-07-01"}, "date_from")
        self.bad(M, AGE, {"case_type": "X"}, "case_type")
        r = self.as_(M).get(AGE, {"export": "csv"})
        self.assertEqual(r["Content-Disposition"],
                         f'attachment; filename="UPI_ageing_as_of_{today.isoformat()}.csv"')
        lines = csv_lines(r)
        self.assertTrue(lines[10].startswith("Rule,0-1 d cases,0-1 d amount,0-1 d overdue"))
        self.assertTrue(lines[-2].startswith("No ageing bucket (settlement cases)"))

        # C2: the links' filter on the case queue
        queue = self.ok(M, f"{UPI}/cases/", {"status": "", "rule_code": "R13", "ageing_bucket": "2-5 d"})
        self.assertEqual([c["id"] for c in queue["results"]], [self.cbs_only.pk])
        two = self.ok(M, f"{UPI}/cases/", {"ageing_bucket": "0-1 d,2-5 d", "rule_code": "R01,R13"})
        self.assertEqual(two["count"], 3)
        self.bad(M, f"{UPI}/cases/", {"ageing_bucket": "3 d"}, "ageing_bucket")


# ---------------------------------------------------------------------------
# B6 / B6s approval report, C4 index
# ---------------------------------------------------------------------------
class ApprovalReportTests(CaseReportBase):
    def setUp(self):
        super().setUp()
        services.take(self.both, self.mk)
        services.submit(self.both, self.mk)
        services.approve(self.both, self.chk)
        services.take(self.cbs_only, self.mk)
        services.submit(self.cbs_only, self.mk, action_type="CLOSE_NO_ACTION", remarks="Found in CBS")
        services.reject(self.cbs_only, self.chk, reason="Wrong code")
        services.take(self.none, self.mk)
        services.submit(self.none, self.mk)  # pending
        self.today = timezone.localdate().isoformat()
        self.range = {"date_from": self.today, "date_to": self.today}

    def test_roles(self):
        for role in ALL_ROLES:
            want = 200 if role in (A, U, C) else 403
            for url in (APPR, APPR_S):
                self.assertEqual(self.as_(role).get(url).status_code, want, (role, url))
        self.assertEqual(self.as_(self.norole).get(APPR).status_code, 403)
        self.assertEqual(self.as_(None).get(APPR).status_code, 401)

    def test_actions_and_summary(self):
        body = self.ok(U, APPR, self.range)
        self.assertEqual(sorted(r["decision"] for r in body["results"]), ["APPROVED", "PENDING", "REJECTED"])
        rejected = next(r for r in body["results"] if r["decision"] == "REJECTED")
        self.assertEqual((rejected["rejection_reason"], rejected["maker"], rejected["checker"], rejected["rrn"],
                          rejected["link"]),
                         ("Wrong code", self.mk.username, self.chk.username, "100000000002",
                          f"/upi/cases/{self.cbs_only.pk}"))
        self.assertIsInstance(rejected["hours_to_decision"], float)
        pending = next(r for r in body["results"] if r["decision"] == "PENDING")
        self.assertEqual((pending["checker"], pending["hours_to_decision"]), (None, None))
        self.assertEqual(body["summary"]["by_checker"], [{"checker": self.chk.username, "approved": 1, "rejected": 1,
                                                          "self_approved": 0}])
        self.assertEqual(body["summary"]["pending_now"], 1)
        self.assertEqual(body["summary"]["oldest_pending"]["link"], f"/upi/cases/{self.none.pk}")
        ids = lambda **q: len(self.ok(C, APPR, {**self.range, **q})["results"])  # noqa: E731
        self.assertEqual((ids(decision="APPROVED,REJECTED"), ids(checker=self.chk.username),
                          ids(maker=self.mk.username), ids(self_approved="true"), ids(maker="nobody")), (2, 2, 3, 0, 0))
        # Date basis: the decision date (R7); last week has nothing.
        week_ago = (timezone.localdate() - timedelta(days=7)).isoformat()
        self.assertEqual(self.ok(U, APPR, {"date_from": week_ago, "date_to": week_ago})["count"], 0)
        self.bad(U, APPR, {**self.range, "decision": "MAYBE"}, "decision")
        self.bad(U, APPR, {**self.range, "action_type": "X"}, "action_type")
        self.assertIn("action_decided_at_idx", [i.name for i in ProposedAction._meta.indexes])  # C4

    def test_settings_changes_and_workbook(self):
        admin = self.users[A]
        tolerance.active_tolerance(self.module)  # seeds v1 (no proposer: left out)
        t = tolerance.propose_tolerance_change(self.module, admin, amount="5", txn_count=1, reason="Ops asked")
        tolerance.approve_tolerance_change(t, self.chk)
        rc = ResponseCode.objects.filter(payment_module=self.module).exclude(code=None).first()
        ResponseCodeChangeRequest.objects.create(
            response_code=rc, payment_module=self.module, flag=rc.flag, old_code=rc.code,
            old_description=rc.description, new_code="999", new_description="New", reason="Circular",
            status=ChangeRequestStatus.PENDING, requested_by=self.mk, requested_at=timezone.now())
        body = self.ok(C, APPR_S, self.range)
        self.assertEqual([(r["kind"], r["decision"]) for r in body["results"]],
                         [("response_code", "PENDING"), ("tolerance", "APPROVED")])
        tol = body["results"][1]
        self.assertEqual((tol["item_label"], tol["proposed_by"], tol["decided_by"], tol["self_approved"],
                          tol["link"]),
                         (f"Tolerance v{t.version}", admin.username, self.chk.username, False,
                          "/upi/settings/tolerance"))
        self.assertEqual(len(self.ok(C, APPR_S, {**self.range, "kind": "tolerance"})["results"]), 1)
        self.assertEqual(len(self.ok(C, APPR_S, {**self.range, "decision": "PENDING"})["results"]), 1)
        self.bad(C, APPR_S, {**self.range, "kind": "x"}, "kind")
        self.assertEqual(body["export"]["row_count"], 2)  # the settings sheet: its CSV default
        r = self.as_(C).get(APPR_S, {**self.range, "export": "csv"})
        self.assertIn("_settings.csv", r["Content-Disposition"])
        self.assertTrue(csv_lines(r)[10].startswith("Setting,Change,Proposed by"))
        r = self.as_(C).get(APPR, {**self.range, "export": "csv", "sheet": "summary"})
        lines = csv_lines(r)
        self.assertEqual(lines[11], f"{self.chk.username},1,1,0,")
        self.assertEqual(lines[12], "Pending now (all dates),,,,1")
