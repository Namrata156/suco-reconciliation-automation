"""
The CBS credit file ("rev" file; owner decisions 2026-11-11,
npci_files.credit_voucher): which cases go in, the file layout, versions,
roles, the upload checks (all or nothing) and the one-step approval, then the
SSKUPI file. Synthetic data: every RRN, account and amount is made up.
"""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import override_settings

from api.tests.test_v1 import V1TestBase
from core.models import OperationLog, OperationType
from core.operations import InvalidInput, RoleRequired, WrongState
from exception_management import services
from exception_management.actions import ActionType
from exception_management.models import CaseEvent, CaseStatus, Decision, EventType, ExceptionCase, ProposedAction
from exception_management.roles import Role
from npci_files import credit_voucher as cv
from npci_files.generator import generate_file, plan_file
from npci_files.models import (
    CreditVoucher,
    CreditVoucherCredit,
    CreditVoucherImmutableError,
    CreditVoucherStatus,
)
from npci_files.tests.base import D, D2, NpciTestBase

UPI = "/api/v1/upi"
GL = "2000011535000000"
ACCT1, ACCT2, ACCT3 = "2000051050811083", "2000101050312119", "2000181050007707"


def fresh(case) -> ExceptionCase:
    return ExceptionCase.objects.get(pk=case.pk)


def rows_of(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


class CreditVoucherBase(NpciTestBase):
    def setUp(self):
        super().setUp()
        self.ops = self.user(Role.OPERATIONS_USER)
        self.chk2 = self.checker()

    def r07r_case(self, rrn, amount="46", account=ACCT1, d=D, txn_date=None, adj_date=None):
        case = self.make_case(rrn, "R07r", d)
        self.dispute(case, "ADJUSTMENT", amount, "9876543210", d=adj_date or d,
                     txn_date=txn_date or f"{d:%d-%m-%Y}",
                     extra_cells={"4": "Refund Reversal Confirmation", "40": account})
        return case

    def generate(self, d=D, user=None):
        return cv.generate_voucher(user or self.ops, self.module, d)

    def edited(self, voucher, *, drop=(), accounts=None, amounts=None, narrations=None, debit=None,
               keep_debit=False) -> bytes:
        """The voucher's CSV as a user would upload it after editing."""
        accounts, amounts, narrations = accounts or {}, amounts or {}, narrations or {}
        rows = rows_of(voucher.content)
        header, debit_row, credits = rows[0], rows[1], rows[2:]
        out = []
        for r in credits:
            rrn = cv.CREDIT_NARRATION_RE.search(r[6]).group(1)
            if rrn in drop:
                continue
            if rrn in accounts:
                r[1] = accounts[rrn]
            if rrn in amounts:
                r[3] = amounts[rrn]
            if rrn in narrations:
                r[6] = narrations[rrn]
            out.append(r)
        if debit is not None:
            debit_row[2] = debit
        elif not keep_debit:
            debit_row[2] = f"{sum(Decimal(r[3] or '0') for r in out):.2f}"
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\r\n").writerows([header, debit_row] + out)
        return buf.getvalue().encode("utf-8")

    def upload(self, voucher, data, user=None):
        return cv.approve_upload(user or self.chk, voucher, "checked.csv", data)


class SelectionTests(CreditVoucherBase):
    def test_money_rules_and_exclusions(self):
        r07r = self.r07r_case("100000000001")
        r02 = self.drc_case("100000000002", "R02", amount="200")
        r04b_open = self.timeout_case("100000000003", "R04b")  # Pending Investigation, nobody decided
        r04b_103 = self.timeout_case("100000000004", "R04b", amount="300")
        self.approve(r04b_103, action_type=ActionType.TCC_103)  # a Checker approved the manual credit
        r04b_102 = self.timeout_case("100000000005", "R04b")
        self.approve(r04b_102, action_type=ActionType.TCC_102)  # decided: no credit
        self.rrc_case("100000000006", rule="R06b")  # never in the file
        self.rrc_case("100000000007")  # R06: RRC 501, closed through SSKUPI, no credit now
        flagged = self.r07r_case("100000000008")
        ExceptionCase.objects.filter(pk=flagged.pk).update(needs_review=True, needs_review_reason="re-run")
        other = self.drc_case("100000000009", "R02")
        services.take(other, self.mk)
        services.submit(other, self.mk, action_type=ActionType.DRC_102)  # the Maker chose another reply
        rejected = self.drc_case("100000000010", "R02")
        services.take(rejected, self.mk)
        services.submit(rejected, self.mk)
        services.reject(rejected, self.chk, reason="not debited")
        pending = self.drc_case("100000000011", "R02", amount="5")
        self.submit_only(pending)  # DRC 103 submitted: in the file, approved on upload

        plan = cv.plan_voucher(self.module, D)
        self.assertEqual([(r.case.pk, r.action_type) for r in plan.rows],
                         [(r07r.pk, ActionType.REFUND_CUSTOMER), (r02.pk, ActionType.DRC_103),
                          (pending.pk, ActionType.DRC_103), (r04b_103.pk, ActionType.TCC_103)])
        self.assertEqual({e.case.pk: e.reason for e in plan.excluded}, {
            r04b_open.pk: cv.Reason.NEEDS_DECISION, flagged.pk: cv.Reason.NEEDS_REVIEW,
            other.pk: cv.Reason.OTHER_REPLY, rejected.pk: cv.Reason.REJECTED})
        self.assertEqual(plan.total_amount, Decimal("551"))
        self.assertEqual([(c["rule_code"], c["rows"]) for c in plan.counts()], [("R07r", 1), ("R02", 2), ("R04b", 1)])
        self.assertNotIn(r04b_102.pk, [e.case.pk for e in plan.excluded])

    def test_r07r_goes_in_the_file_of_the_adjustment_date(self):
        case = self.r07r_case("100000000001", d=D, adj_date=D2)  # on its Txndate's group, arrived on D2
        self.assertEqual(cv.plan_voucher(self.module, D).rows, [])
        self.assertEqual([r.case.pk for r in cv.plan_voucher(self.module, D2).rows], [case.pk])
        r02 = self.drc_case("100000000002", "R02")
        self.assertEqual([r.case.pk for r in cv.plan_voucher(self.module, D).rows], [r02.pk])

    def test_nothing_is_credited_twice(self):
        first = self.r07r_case("100000000001")
        v1 = self.generate()
        self.upload(v1, self.edited(v1), user=self.chk)
        self.assertEqual(fresh(first).status, CaseStatus.COMPLETED)
        with self.assertRaises(WrongState):
            self.generate()  # nothing left for D
        # the same RRN on another date (e.g. after a re-run moved the adjustment): never again
        again = self.r07r_case("100000000001", d=D2)
        plan = cv.plan_voucher(self.module, D2)
        self.assertEqual(plan.rows, [])
        self.assertEqual([(e.case.pk, e.reason, e.detail) for e in plan.excluded],
                         [(again.pk, cv.Reason.ALREADY_CREDITED, v1.file_name)])
        # and the database refuses a second credit of the RRN whatever the path
        with self.assertRaises(IntegrityError), transaction.atomic():
            c = CreditVoucherCredit.objects.get()
            CreditVoucherCredit.objects.create(approval=c.approval, voucher_row=c.voucher_row, case=again,
                                               payment_module=self.module, rrn=c.rrn, amount=1, account=ACCT1)

    def test_a_later_version_holds_only_what_was_left_open(self):
        a, b = self.r07r_case("100000000001"), self.r07r_case("100000000002", account=ACCT2)
        v1 = self.generate()
        self.upload(v1, self.edited(v1, drop=("100000000002",)))
        v2 = self.generate()
        self.assertEqual((v2.version, v2.file_name, list(v2.rows.values_list("case_id", flat=True))),
                         (2, "upi rev 14072026_2.csv", [b.pk]))
        self.assertEqual((cv.voucher_status(v1), cv.voucher_status(v2)),
                         (CreditVoucherStatus.APPROVED, CreditVoucherStatus.GENERATED))
        self.assertEqual(fresh(a).status, CaseStatus.COMPLETED)


class FileLayoutTests(CreditVoucherBase):
    def test_csv_layout(self):
        self.r07r_case("630574834996", amount="46", txn_date="01-11-2026")
        self.r07r_case("640606922084", amount="1000", account="'012501001860", txn_date="24-07-2026")
        self.r07r_case("704915529434", amount="20", account="", txn_date="01-11-2026")
        self.drc_case("100000000002", "R02", amount="200.5")
        v = self.generate()
        self.assertEqual(v.file_name, "upi rev 14072026.csv")
        self.assertTrue(v.content.endswith("\r\n"))
        self.assertEqual(rows_of(v.content), [
            ["Sr. No.", "Account NBR", "Debit Amount", "Credit Amount", "Instrument Number", "Instrument Date",
             "Narration", "Check"],
            ["1", f"'{GL}", "1266.50", "", "", "", "UPI TO RRC DRC TCC REVERSAL DATED ON JUL 14", ""],
            ["2", "'012501001860", "", "1000.00", "", "", "UPI reversal entry RRN/640606922084  Dated  24072026",
             cv.CHECK_BAD_ACCOUNT],
            ["3", f"'{ACCT1}", "", "46.00", "", "", "UPI reversal entry RRN/630574834996  Dated  1112026", ""],
            ["4", "", "", "20.00", "", "", "UPI reversal entry RRN/704915529434  Dated  1112026", cv.CHECK_NO_ACCOUNT],
            ["5", "", "", "200.50", "", "", "UPI reversal entry RRN/100000000002  Dated  14072026",
             cv.CHECK_NO_ACCOUNT_REPORT],
        ])
        self.assertEqual((v.row_count, v.total_amount, v.rows_to_check), (4, Decimal("1266.50"), 3))
        self.assertEqual(v.rows.get(rrn="640606922084").account, "012501001860")  # no conversion attempted

    def test_narration_dates(self):
        self.assertEqual(cv.dmmyyyy(date(2026, 11, 1)), "1112026")
        self.assertEqual(cv.dmmyyyy(date(2026, 7, 24)), "24072026")
        self.assertEqual(cv.credit_narration("001605645493", date(2026, 11, 1)),
                         "UPI reversal entry RRN/001605645493  Dated  1112026")
        # owner 2026-11-12: the file's business (reconciliation) date
        self.assertEqual(cv.debit_narration(date(2026, 11, 2)), "UPI TO RRC DRC TCC REVERSAL DATED ON NOV 02")

    def test_debit_account_is_a_setting(self):
        self.r07r_case("100000000001")
        with self.settings(CREDIT_VOUCHER_DEBIT_ACCOUNT="2000019999000000"):
            v = self.generate()
        self.assertEqual(rows_of(v.content)[1][1], "'2000019999000000")

    def test_versions_supersede_and_files_never_change(self):
        self.r07r_case("100000000001")
        v1 = self.generate()
        v2 = self.generate()
        self.assertEqual((cv.voucher_status(v1), cv.voucher_status(v2)),
                         (CreditVoucherStatus.SUPERSEDED, CreditVoucherStatus.GENERATED))
        log = OperationLog.objects.filter(operation=OperationType.CREDIT_VOUCHER_GENERATED).latest("id")
        self.assertEqual(log.details["superseded"], [v1.file_name])
        with self.assertRaises(CreditVoucherImmutableError):
            CreditVoucher.objects.filter(pk=v1.pk).update(remarks="x")
        with self.assertRaises(CreditVoucherImmutableError):
            v1.delete()
        with self.assertRaises(WrongState):
            self.upload(v1, self.edited(v1))  # a superseded version cannot be uploaded

    def test_roles_and_download_log(self):
        self.r07r_case("100000000001")
        for user in (self.chk, self.user(Role.AUDITOR)):
            with self.assertRaises(RoleRequired):
                self.generate(user=user)
        v = self.generate(user=self.mk)
        self.assertEqual(cv.download_voucher(self.ops, v, source="test"), v.content.encode("utf-8"))
        cv.download_voucher(self.chk, v)  # owner 2026-11-12: the Checker may download too
        with self.assertRaises(RoleRequired):
            cv.download_voucher(self.user(Role.AUDITOR), v)
        log = OperationLog.objects.filter(operation=OperationType.CREDIT_VOUCHER_DOWNLOADED).earliest("id")
        self.assertEqual((log.username, log.details["credit_voucher_id"]), (self.ops.username, v.pk))


class UploadCheckTests(CreditVoucherBase):
    def setUp(self):
        super().setUp()
        self.a = self.r07r_case("100000000001", amount="46")
        self.b = self.r07r_case("100000000002", amount="20", account="012501001860")
        self.c = self.drc_case("100000000003", "R02", amount="200")
        self.v = self.generate()
        self.fixed = {"100000000002": f"'{ACCT2}", "100000000003": ACCT3}

    def refused(self, data, user=None) -> dict:
        with self.assertRaises(InvalidInput) as ctx:
            self.upload(self.v, data, user=user)
        for case in (self.a, self.b, self.c):
            self.assertEqual(fresh(case).status, CaseStatus.OPEN)  # all or nothing
        self.assertFalse(CreditVoucherCredit.objects.exists())
        self.assertEqual(cv.voucher_status(self.v), CreditVoucherStatus.GENERATED)
        return ctx.exception.errors

    def row_errors(self, errors) -> dict:
        return {r["rrn"]: r["errors"] for r in errors.get("rows", [])}

    def test_amount_changed(self):
        errors = self.refused(self.edited(self.v, accounts=self.fixed, amounts={"100000000001": "47.00"}))
        self.assertIn("The credit amount was changed: the file had 46.00, the upload has 47.00.",
                      self.row_errors(errors)["100000000001"])

    def test_unknown_rrn_and_changed_narration(self):
        errors = self.refused(self.edited(self.v, accounts=self.fixed, narrations={
            "100000000001": "UPI reversal entry RRN/999999999999  Dated  14072026",
            "100000000003": "UPI reversal entry RRN/100000000003  Dated  15072026"}))
        rows = self.row_errors(errors)
        self.assertEqual(rows["999999999999"], ["This RRN is not in this credit file."])
        self.assertEqual(rows["100000000003"], ["The narration was changed; only accounts may be filled in or corrected."])

    def test_bad_and_missing_accounts(self):
        errors = self.refused(self.edited(self.v, accounts={"100000000003": "2.00005E+15"}))
        rows = self.row_errors(errors)
        self.assertIn("must be 16 digits", rows["100000000002"][0])  # the old 12-digit number, not corrected
        self.assertIn("must be 16 digits", rows["100000000003"][0])
        errors = self.refused(self.edited(self.v, accounts={"100000000002": f"'{ACCT2}"}))
        self.assertEqual(self.row_errors(errors)["100000000003"], ["The account number is missing."])

    def test_debit_must_equal_the_credits(self):
        errors = self.refused(self.edited(self.v, accounts=self.fixed, drop=("100000000003",), keep_debit=True))
        self.assertEqual(errors["file"], ["The debit row (266.00) must equal the sum of the credit rows (66.00). "
                                          "After deleting rows, correct the debit amount."])
        errors = self.refused(self.edited(self.v, accounts=self.fixed, debit=""))
        self.assertIn("exactly one debit row", errors["file"][0])

    def test_wrong_header_or_empty(self):
        self.assertIn("column names", self.refused(b"a,b,c\r\n1,2,3\r\n")["file"][0])
        self.assertEqual(self.refused(b"")["file"], ["The file is empty."])
        self.assertEqual(OperationLog.objects.filter(operation=OperationType.CREDIT_VOUCHER_REFUSED).count(), 2)

    def test_same_user_and_roles(self):
        both = self.user(Role.MAKER, Role.CHECKER)
        v = cv.generate_voucher(both, self.module, D)
        with self.assertRaises(RoleRequired) as ctx:
            self.upload(v, self.edited(v, accounts=self.fixed), user=both)
        self.assertIn("other than the user who generated it", ctx.exception.detail)
        with self.assertRaises(RoleRequired):
            self.upload(v, self.edited(v, accounts=self.fixed), user=self.ops)  # not a Checker

    def test_case_changed_since_generation(self):
        ExceptionCase.objects.filter(pk=self.c.pk).update(needs_review=True, needs_review_reason="re-run")
        with self.assertRaises(WrongState) as ctx:
            self.upload(self.v, self.edited(self.v, accounts=self.fixed))
        self.assertEqual([(c["rrn"], c["reason"]) for c in ctx.exception.errors["cases"]],
                         [("100000000003", cv.Reason.NEEDS_REVIEW)])
        self.assertEqual(fresh(self.a).status, CaseStatus.OPEN)
        self.assertEqual(OperationLog.objects.filter(operation=OperationType.CREDIT_VOUCHER_REFUSED).count(), 1)


class ApprovalTests(CreditVoucherBase):
    def test_partial_upload_approves_the_listed_cases_in_one_step(self):
        r07r = self.r07r_case("100000000001", amount="46")
        removed = self.r07r_case("100000000002", amount="20")
        r02 = self.drc_case("100000000003", "R02", amount="200")
        pending = self.drc_case("100000000004", "R02", amount="5")
        pending_action = self.submit_only(pending)
        r04b = self.timeout_case("100000000005", "R04b", amount="300")
        approved_action = self.approve(r04b, action_type=ActionType.TCC_103)
        v = self.generate(user=self.ops)
        data = self.edited(v, drop=("100000000002",), accounts={
            "100000000003": ACCT2, "100000000004": f"'{ACCT3}", "100000000005": ACCT3})
        result = cv.approve_upload(self.chk, v, "upi rev 14072026 checked.csv", data, remarks="CBS batch 7",
                                   source="test")
        self.assertEqual(([r.case_id for r in result.approved], [r.case_id for r in result.left_open]),
                         ([r07r.pk, r02.pk, pending.pk, r04b.pk], [removed.pk]))
        self.assertEqual(cv.voucher_status(v), CreditVoucherStatus.APPROVED)
        self.assertEqual((result.approval.approved_count, result.approval.removed_count,
                          result.approval.total_amount), (4, 1, Decimal("551")))
        self.assertEqual(fresh(removed).status, CaseStatus.OPEN)
        for case, action_type in ((r07r, ActionType.REFUND_CUSTOMER), (r02, ActionType.DRC_103),
                                  (pending, ActionType.DRC_103), (r04b, ActionType.TCC_103)):
            with self.subTest(rule=case.rule_code, rrn=case.rrn):
                self.assertEqual(fresh(case).status, CaseStatus.COMPLETED)
                a = ProposedAction.objects.get(case=case, decision=Decision.APPROVED)
                self.assertEqual((a.action_type, a.completed_by, a.external_reference),
                                 (action_type, self.chk, v.file_name))
                e = CaseEvent.objects.get(case=case, event_type=EventType.CREDIT_FILE_APPROVED)
                self.assertEqual((e.actor, e.new_status, e.changes["credit_voucher_id"]),
                                 (self.chk, CaseStatus.COMPLETED, [None, v.pk]))
                self.assertIn("CBS batch 7", e.remarks)
        # open cases: proposed by the generating user, approved by the uploader
        a = ProposedAction.objects.get(case=r02)
        self.assertEqual((a.submitted_by, a.decided_by, a.self_approved, a.response_code.code),
                         (self.ops, self.chk, False, "103"))
        self.assertIsNone(ProposedAction.objects.get(case=r07r).response_code)
        pending_action.refresh_from_db()
        self.assertEqual((pending_action.decision, pending_action.decided_by), (Decision.APPROVED, self.chk))
        approved_action.refresh_from_db()
        self.assertEqual(approved_action.external_reference, v.file_name)
        self.assertEqual(CaseEvent.objects.filter(event_type=EventType.CREDIT_FILE_APPROVED).count(), 4)
        credits = {c.rrn: c for c in CreditVoucherCredit.objects.all()}
        self.assertEqual((credits["100000000001"].account, credits["100000000001"].account_changed),
                         (ACCT1, False))
        self.assertEqual((credits["100000000003"].account, credits["100000000003"].account_changed), (ACCT2, True))
        log = OperationLog.objects.get(operation=OperationType.CREDIT_VOUCHER_APPROVED)
        self.assertEqual((log.details["approved"], log.details["left_open"], log.details["accounts_changed"]),
                         (4, 1, 3))
        with self.assertRaises(WrongState):
            cv.approve_upload(self.chk2, v, "again.csv", data)  # approved once only

        # then the SSKUPI file: the approved DRC 103 / TCC 103 go in; R07r has no reply
        plan = plan_file(self.module, D)
        self.assertEqual(sorted((r.case.rrn, r.flag, r.code, r.source) for r in plan.rows),
                         [("100000000003", "DRC", "103", "APPROVED"), ("100000000004", "DRC", "103", "APPROVED"),
                          ("100000000005", "TCC", "103", "APPROVED")])
        self.assertFalse(any("credited by hand" in w for w in plan.warnings))
        f = generate_file(self.chk, self.module, D)
        self.assertEqual(f.row_count, 3)
        self.assertEqual(fresh(r07r).status, CaseStatus.COMPLETED)

    def test_rerun_keeps_credited_cases_closed(self):
        case = self.r07r_case("100000000001")
        v = self.generate()
        self.upload(v, self.edited(v))
        self.rerun({"100000000001": "R07r"})
        self.sync()
        c = fresh(case)
        self.assertEqual((c.status, c.needs_review), (CaseStatus.COMPLETED, False))


class CreditVoucherApiTests(V1TestBase, CreditVoucherBase):
    def setUp(self):
        super().setUp()
        self.ops, self.chk, self.mk = (self.users[Role.OPERATIONS_USER], self.users[Role.CHECKER],
                                       self.users[Role.MAKER])
        self.case1 = self.r07r_case("100000000001", amount="46")
        self.case2 = self.drc_case("100000000002", "R02", amount="200")
        self.base = f"{UPI}/dates/{D.isoformat()}/credit-voucher/"

    def test_preview_generate_list_detail_download_upload(self):
        self.assert_read_matrix(f"{self.base}preview/")
        p = self.as_(Role.OPERATIONS_USER).get(f"{self.base}preview/").json()
        self.assertEqual((p["next_file_name"], p["row_count"], p["total_amount"], p["rows_to_check"],
                          p["can_generate"]), ("upi rev 14072026.csv", 2, "246.00", 1, True))
        self.assertEqual(p["rows"][0]["account_masked"], "************1083")
        self.assertFalse(self.as_(Role.CHECKER).get(f"{self.base}preview/").json()["can_generate"])

        self.assertEqual(self.post(self.as_(Role.CHECKER), self.base).status_code, 403)
        r = self.post(self.as_(Role.OPERATIONS_USER), self.base, {"remarks": "first"})
        self.assertEqual(r.status_code, 201, r.content[:300])
        body = r.json()
        vid = body["id"]
        self.assertEqual((body["status"], body["version"], body["row_count"]), ("GENERATED", 1, 2))
        self.assertNotIn(ACCT1, r.content.decode())  # accounts masked in JSON
        self.assertEqual(body["history"][0]["operation"], "CREDIT_VOUCHER_GENERATED")

        lst = self.as_(Role.CHECKER).get(self.base).json()
        self.assertEqual((len(lst["results"]), lst["pending_approval"], lst["latest"]["can_upload"]), (1, True, True))
        self.assertFalse(self.as_(self.ops).get(self.base).json()["latest"]["can_upload"])

        self.assertEqual(self.as_(Role.AUDITOR).get(f"{self.base}{vid}/download/").status_code, 403)
        dl = self.as_(Role.MAKER).get(f"{self.base}{vid}/download/")
        self.assertEqual(dl.status_code, 200)
        self.assertEqual(dl["Content-Disposition"], 'attachment; filename="upi rev 14072026.csv"')
        v = CreditVoucher.objects.get(pk=vid)

        bad = self.edited(v)  # the R02 account is still blank
        r = self.as_(Role.CHECKER).post(f"{self.base}{vid}/upload/", {"file": SimpleUploadedFile("c.csv", bad)})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["errors"]["rows"][0]["rrn"], "100000000002")
        r = self.as_(Role.OPERATIONS_USER).post(f"{self.base}{vid}/upload/", {"file": SimpleUploadedFile("c.csv", bad)})
        self.assertEqual(r.status_code, 403)

        good = self.edited(v, accounts={"100000000002": ACCT2})
        r = self.as_(Role.CHECKER).post(f"{self.base}{vid}/upload/",
                                        {"file": SimpleUploadedFile("c.csv", good), "remarks": "ok"})
        self.assertEqual(r.status_code, 200, r.content[:400])
        b = r.json()
        self.assertEqual((b["approved_count"], b["left_open_count"], b["accounts_changed"], b["voucher"]["status"]),
                         (2, 0, 1, "APPROVED"))
        self.assertEqual(b["voucher"]["approval"]["approved_by"], self.chk.username)
        self.assertTrue(all(row["approved"] for row in b["voucher"]["rows"]))
        r = self.as_(Role.CHECKER).post(f"{self.base}{vid}/upload/", {"file": SimpleUploadedFile("c.csv", good)})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(fresh(self.case1).status, CaseStatus.COMPLETED)

        audit = self.as_(Role.AUDITOR).get(f"{UPI}/cases/{self.case1.pk}/").json()
        self.assertIn(EventType.CREDIT_FILE_APPROVED, [e["event_type"] for e in audit["events"]])

    def test_can_generate_is_false_for_today(self):
        from django.utils import timezone

        today = timezone.localdate()
        self.r07r_case("100000000009", d=today)
        base = f"{UPI}/dates/{today.isoformat()}/credit-voucher/"
        p = self.as_(Role.OPERATIONS_USER).get(f"{base}preview/").json()
        self.assertEqual((p["row_count"], p["can_generate"]), (1, False))
        self.assertFalse(self.as_(Role.OPERATIONS_USER).get(base).json()["can_generate"])
        self.assertTrue(self.as_(Role.OPERATIONS_USER).get(self.base).json()["can_generate"])

    def test_upload_needs_a_file(self):
        v = self.generate(user=self.ops)
        r = self.as_(Role.CHECKER).post(f"{self.base}{v.pk}/upload/", {})
        self.assertEqual((r.status_code, list(r.json()["errors"])), (400, ["file"]))
        self.assertEqual(self.as_(Role.CHECKER).get(f"{UPI}/dates/2026-07-15/credit-voucher/{v.pk}/").status_code,
                         404)


@override_settings(ROLES_ENFORCED=False)
class RolesOffTests(CreditVoucherBase):
    """Owner 2026-11-12: roles switched off -- a user with no group does every step,
    including uploading the file they generated; every step is logged by username."""

    def test_one_plain_user_does_everything(self):
        plain = self.user()  # no group at all
        case = self.r07r_case("100000000001")
        v = cv.generate_voucher(plain, self.module, D)
        self.assertEqual(cv.download_voucher(plain, v), v.content.encode("utf-8"))
        result = cv.approve_upload(plain, v, "checked.csv", self.edited(v))
        self.assertEqual([r.case_id for r in result.approved], [case.pk])
        a = ProposedAction.objects.get(case=case)
        self.assertEqual((a.submitted_by, a.decided_by, a.self_approved), (plain, plain, True))
        self.assertEqual(set(OperationLog.objects.filter(operation__startswith="CREDIT_VOUCHER")
                             .values_list("username", flat=True)), {plain.username})
