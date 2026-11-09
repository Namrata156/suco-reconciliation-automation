"""
NPCI file generation (docs/npci_file_design_draft_v1.txt + Amendment 1,
owner 2026-10-22/23, + Amendment 2, owner 2026-10-24): which cases go in (the
reply each case carries now: approved, submitted, or the rule's suggestion;
R06b / R07x / R04b / evidence replies only once approved; rejected and
flagged cases held back), the values and the exact format, partial files,
later files of the same date (_2, _3) holding only new rows, a case never
included twice, missing numeric codes, the wording taken from the
response-code repository, roles, audit, append-only storage. Synthetic data.
"""
from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction

from core.models import OperationLog, OperationType
from core.operations import RoleRequired, WrongState
from exception_management import code_changes, services
from exception_management.actions import ActionType
from exception_management.models import CaseStatus, ExceptionCase
from exception_management.roles import Role
from npci_files.compare import compare
from npci_files.formats import SSKUPI, FileLine, format_amount
from npci_files.generator import Reason, download_file, generate_file, plan_file
from npci_files.models import NpciFile, NpciFileImmutableError, NpciFileRow, ReplySource
from npci_files.tests.base import D, D2, NpciTestBase
from rule_engine.models import CodeFlag, ResponseCode

HEADER = "bankadjref,flag,Shtdat,adjamt,shser,shcrd,filename,reason,specifyother"


class FormatTests(NpciTestBase):
    def test_amount_without_trailing_zeros(self):
        for value, text in (("1", "1"), ("1.00", "1"), ("7.50", "7.5"), ("140.56", "140.56"), ("100", "100"),
                            ("0.05", "0.05"), ("1900.10", "1900.1")):
            with self.subTest(value=value):
                self.assertEqual(format_amount(Decimal(value)), text)

    def test_file_names(self):
        self.assertEqual(SSKUPI.file_name(date(2026, 7, 14), 1), "SSKUPI14072026.csv")
        self.assertEqual(SSKUPI.file_name(date(2026, 7, 14), 2), "SSKUPI14072026_2.csv")
        self.assertEqual(SSKUPI.file_name(date(2026, 9, 5), 3), "SSKUPI05092026_3.csv")

    def test_render_exact_bytes(self):
        lines = [FileLine("100000000001", "DRC", "102", D, Decimal("140.50"), "000000001111", "Online Reversal")]
        text = SSKUPI.render(lines, "SSKUPI14072026.csv")
        self.assertEqual(text, f"{HEADER}\r\n100000000001,DRC,14-07-2026,140.5,100000000001,000000001111,"
                               "SSKUPI14072026.csv,102,Online Reversal\r\n")


class SelectionAndValuesTests(NpciTestBase):
    def build_day(self):
        self.r01 = self.drc_case("100000000001", "R01", "140.50", "'000000001111")
        self.r03 = self.drc_case("100000000002", "R03", "20", "000000003333")
        self.r01b = self.drc_case("100000000003", "R01", "5", "000000004444")  # later NPCI row, same code
        self.r02 = self.drc_case("100000000004", "R02", "7", "000000005555")  # DRC 103
        self.r04a = self.timeout_case("100000000005", "R04a", "60", "'ABC0000006666")
        self.r05 = self.timeout_case("100000000006", "R05", "70", "000000007777")
        self.r06 = self.rrc_case("100000000007", "401", "9876543210", txn_date="24-04-2026")
        for case in (self.r01, self.r03, self.r01b, self.r02, self.r04a, self.r05, self.r06):
            self.approve(case)

    def test_rows_values_order_and_exact_file(self):
        self.build_day()
        f = generate_file(self.chk, self.module, D, source="test")
        self.assertEqual((f.file_name, f.sequence, f.partial, f.row_count), ("SSKUPI14072026.csv", 1, False, 7))
        name = "SSKUPI14072026.csv"
        expected = [
            HEADER,
            f"100000000001,DRC,14-07-2026,140.5,100000000001,000000001111,{name},102,Online reverse",
            f"100000000003,DRC,14-07-2026,5,100000000003,000000004444,{name},102,Online reverse",
            f"100000000004,DRC,14-07-2026,7,100000000004,000000005555,{name},103,Manual credit",  # owner 2026-11-09 wording
            f"100000000002,DRC,14-07-2026,20,100000000002,000000003333,{name},104,Not effected to CBS",
            # RRC: Shtdat = the Adjustment's Txndate, adjamt = Adjamount
            f"100000000007,RRC,24-04-2026,401,100000000007,9876543210,{name},501,Manual credit",
            f"100000000005,TCC,14-07-2026,60,100000000005,ABC0000006666,{name},102,Online credited",
            f"100000000006,RET,14-07-2026,70,100000000006,000000007777,{name},115,Account doesn't exist",
        ]
        self.assertEqual(f.content, "".join(line + "\r\n" for line in expected))
        data = f.content.encode("ascii")
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
        self.assertEqual((f.sha256, f.size_bytes), (hashlib.sha256(data).hexdigest(), len(data)))
        self.assertEqual(f.total_amount, Decimal("703.50"))
        self.assertEqual(list(f.rows.values_list("line", "rrn", "account_masked"))[0],
                         (1, "100000000001", "********1111"))
        self.assertEqual({(c["flag"], c["code"], c["rows"]) for c in f.counts},
                         {("DRC", "102", 2), ("DRC", "103", 1), ("DRC", "104", 1), ("RRC", "501", 1),
                          ("TCC", "102", 1), ("RET", "115", 1)})
        log = OperationLog.objects.get(operation=OperationType.NPCI_FILE_GENERATED)
        self.assertEqual((log.user, log.business_date, log.details["npci_file_id"], log.details["rows"],
                          log.details["sha256"], log.details["partial"]), (self.chk, D, f.pk, 7, f.sha256, False))

    def test_same_rows_straight_after_reconciliation_without_approval(self):
        """Amendment 2: open cases give their rules' replies, no approval first."""
        d3 = date(2026, 7, 20)
        self.drc_case("200000000001", "R01", d=d3)
        self.drc_case("200000000002", "R02", d=d3)
        self.timeout_case("200000000003", "R05", d=d3)
        f = generate_file(self.chk, self.module, d3)
        self.assertEqual((f.row_count, f.partial), (3, False))
        self.assertEqual(list(f.rows.values_list("flag", "code", "reply_source", "action_type", "action")),
                         [("DRC", "102", "RULE", "DRC_102", None), ("DRC", "103", "RULE", "DRC_103", None),
                          ("RET", "115", "RULE", "RET", None)])
        log = OperationLog.objects.get(operation=OperationType.NPCI_FILE_GENERATED)
        self.assertEqual(log.details["reply_sources"], {"RULE": 3})

    def test_approved_rows_record_their_action(self):
        self.build_day()
        f = generate_file(self.chk, self.module, D)
        self.assertEqual(set(f.rows.values_list("reply_source", flat=True)), {ReplySource.APPROVED})
        self.assertFalse(f.rows.filter(action__isnull=True).exists())

    def test_text_is_the_repository_description_at_generation(self):
        """Option B: change the wording through maker-checker, the next file uses it."""
        self.build_day()
        first = generate_file(self.chk, self.module, D)
        drc102 = ResponseCode.objects.get(payment_module=self.module, flag="DRC", code="102")
        code_changes.approve_code_change(code_changes.propose_code_change(
            drc102, self.mk, new_description="Online Reversal", reason="owner wording"), self.chk)
        later = self.drc_case("100000000009", "R01", "3", "000000008888")
        self.approve(later)
        second = generate_file(self.chk, self.module, D)
        self.assertIn(",102,Online Reversal\r\n", second.content)
        self.assertIn(",102,Online reverse\r\n", NpciFile.objects.get(pk=first.pk).content)  # stored file unchanged

    def test_rrc_goes_to_the_adjdate_file(self):
        """An adjustment for D's transaction that arrived on D+1 belongs to D+1's file."""
        late = self.rrc_case("100000000010", "50", "9000000001", adj_date=D2, txn_date="14-07-2026")
        on_time = self.drc_case("100000000011")
        self.approve(late)
        self.approve(on_time)
        day1 = plan_file(self.module, D)
        day2 = plan_file(self.module, D2)
        self.assertEqual([r.case.rrn for r in day1.rows], ["100000000011"])
        self.assertEqual([(r.case.rrn, r.line.shtdat) for r in day2.rows], [("100000000010", D)])
        f = generate_file(self.chk, self.module, D2)
        self.assertEqual(f.file_name, "SSKUPI15072026.csv")

    def test_non_reply_actions_are_not_rows(self):
        case = self.drc_case("100000000012")
        self.approve(case, action_type=ActionType.CLOSE_NO_ACTION, remarks="customer already refunded")
        plan = plan_file(self.module, D)
        self.assertEqual((plan.rows, plan.excluded), ([], []))
        with self.assertRaisesRegex(WrongState, "Nothing to include"):
            generate_file(self.chk, self.module, D)


class PartialAndLaterFileTests(NpciTestBase):
    def test_current_reply_of_every_case_and_partial_needs_confirmation(self):
        approved = self.drc_case("100000000001")
        self.approve(approved)
        self.drc_case("100000000002")  # open: the rule's reply
        pending = self.drc_case("100000000003")
        services.take(pending, self.mk)
        services.submit(pending, self.mk, action_type=ActionType.DRC_104)  # the Maker's reply, not yet approved
        reviewed = self.drc_case("100000000004")
        self.approve(reviewed)
        ExceptionCase.objects.filter(pk=reviewed.pk).update(needs_review=True, needs_review_reason="re-run")
        self.rrc_case("100000000005", rule="R06b")  # needs a Checker's approval first
        plan = plan_file(self.module, D)
        self.assertEqual([(r.case.rrn, r.code, r.source) for r in plan.rows],
                         [("100000000001", "102", ReplySource.APPROVED), ("100000000002", "102", ReplySource.RULE),
                          ("100000000003", "104", ReplySource.SUBMITTED)])
        self.assertEqual(plan.excluded_counts(), {Reason.NEEDS_REVIEW: 1, Reason.NEEDS_APPROVAL: 1})
        self.assertTrue(any("flagged for review" in w for w in plan.warnings))
        self.assertTrue(any("until a Checker approves their reply (R06b: 1)" in w for w in plan.warnings))
        with self.assertRaises(WrongState) as ctx:
            generate_file(self.chk, self.module, D)
        self.assertIn("confirm_partial", ctx.exception.errors)
        self.assertFalse(NpciFile.objects.exists())
        f = generate_file(self.chk, self.module, D, confirm_partial=True, remarks="RET deadline")
        self.assertTrue(f.partial)
        self.assertEqual(len(f.not_included), 2)
        self.assertEqual(f.remarks, "RET deadline")
        self.assertEqual(list(f.rows.values_list("reply_source", flat=True)), ["APPROVED", "RULE", "SUBMITTED"])

    def test_no_confirmation_when_nothing_is_left_out(self):
        self.drc_case("100000000001")
        self.timeout_case("100000000002")
        f = generate_file(self.chk, self.module, D)  # no confirm_partial needed
        self.assertEqual((f.row_count, f.partial, f.not_included), (2, False, []))

    def test_second_file_holds_only_new_rows_and_no_case_twice(self):
        # R02 (money): the cases stay open after the file (R01 would close, owner 2026-11-09)
        first_cases = [self.drc_case(f"10000000010{i}", "R02") for i in range(3)]
        f1 = generate_file(self.chk, self.module, D)
        self.assertEqual((f1.file_name, f1.row_count, f1.partial), ("SSKUPI14072026.csv", 3, False))
        with self.assertRaisesRegex(WrongState, "Nothing to include"):  # nothing new yet
            generate_file(self.chk, self.module, D, confirm_partial=True)
        for case in first_cases:  # approving an included case adds nothing
            self.approve(case)
        with self.assertRaisesRegex(WrongState, "Nothing to include"):
            generate_file(self.chk, self.module, D, confirm_partial=True)
        # A re-run brings two new cases.
        mapping = {c.rrn: "R02" for c in first_cases}
        mapping.update({"100000000200": "R01", "100000000201": "R03"})
        self.rerun(mapping)
        self.sync()
        later_cases = [self.case("100000000200"), self.case("100000000201")]
        for case in later_cases:
            self.dispute(case, "DRC", "10", "000000009999")
        plan = plan_file(self.module, D)
        self.assertEqual(len(plan.already_sent), 3)
        f2 = generate_file(self.chk, self.module, D)
        self.assertEqual((f2.file_name, f2.sequence, f2.row_count, f2.partial), ("SSKUPI14072026_2.csv", 2, 2, False))
        self.assertEqual(sorted(f2.rows.values_list("rrn", flat=True)), [c.rrn for c in later_cases])
        self.assertTrue(all(",SSKUPI14072026_2.csv," in line for line in f2.content.split("\r\n")[1:-1]))
        self.assertEqual(NpciFileRow.objects.count(), 5)
        self.assertEqual(NpciFileRow.objects.values("case").distinct().count(), 5)
        with self.assertRaisesRegex(WrongState, "Nothing to include"):
            generate_file(self.chk, self.module, D)
        # The database refuses a second row for an included case too.
        row = f1.rows.first()
        with self.assertRaises(IntegrityError), transaction.atomic():
            NpciFileRow.objects.create(npci_file=f2, line=99, case=row.case, action=row.action,
                                       case_business_date=D, rrn=row.rrn, flag="DRC", code="102", shtdat=D,
                                       amount=1, account_masked="", text="x", dispute_fact_id=row.dispute_fact_id)

    def test_sent_case_flagged_later_gives_a_warning_not_a_row(self):
        case = self.drc_case("100000000001")
        self.approve(case)
        generate_file(self.chk, self.module, D)
        ExceptionCase.objects.filter(pk=case.pk).update(needs_review=True, needs_review_reason="re-run")
        plan = plan_file(self.module, D)
        self.assertEqual((plan.rows, plan.excluded, len(plan.already_sent)), ([], [], 1))
        self.assertTrue(any("already included in an earlier file" in w for w in plan.warnings))
        self.assertFalse(any("sent" in w for w in plan.warnings))

    def test_included_case_that_changes_later_gives_a_warning_not_a_row(self):
        """Included with the rule's reply; then the Maker chooses another one,
        or a re-run closes the case: the file never changes, a warning shows.
        R02 (money): R01 cases would be closed by the file itself (owner 2026-11-09)."""
        other = self.drc_case("100000000001", "R02")
        closed = self.drc_case("100000000002", "R02")
        generate_file(self.chk, self.module, D)
        services.take(other, self.mk)
        services.submit(other, self.mk, action_type=ActionType.DRC_104)
        plan = plan_file(self.module, D)
        self.assertEqual((plan.rows, plan.excluded, len(plan.already_sent)), ([], [], 2))
        self.assertEqual([c.pk for c, _r in plan.changed_since_included], [other.pk])
        self.rerun({"100000000001": "R02"})  # 100000000002 no longer needs action
        self.sync()
        self.assertEqual(self.case("100000000002").status, CaseStatus.CLOSED_SUPERSEDED)
        plan = plan_file(self.module, D)
        self.assertEqual({c.pk for c, _r in plan.changed_since_included}, {other.pk, closed.pk})
        self.assertTrue(any(w.startswith("2 case(s) already included in an earlier file have changed since")
                            for w in plan.warnings))

    def test_missing_numeric_code_left_out_then_picked_up(self):
        closed = ResponseCode.objects.get(payment_module=self.module, flag=CodeFlag.RET, description="Account closed")
        ret = self.timeout_case("100000000001", "R05")
        self.approve(ret, action_type=ActionType.RET, response_code=closed)
        ok = self.drc_case("100000000002")
        self.approve(ok)
        plan = plan_file(self.module, D)
        self.assertEqual(plan.excluded_counts(), {Reason.NO_NUMERIC_CODE: 1})
        self.assertIn("RET Account closed: 1", plan.warnings[0])
        f1 = generate_file(self.chk, self.module, D, confirm_partial=True)
        self.assertEqual(f1.row_count, 1)
        code_changes.approve_code_change(
            code_changes.propose_code_change(closed, self.mk, new_code="114", reason="NPCI list (synthetic)"),
            self.chk)
        f2 = generate_file(self.chk, self.module, D)
        self.assertEqual((f2.file_name, f2.row_count, f2.partial), ("SSKUPI14072026_2.csv", 1, False))
        self.assertEqual(list(f2.rows.values_list("flag", "code", "text")), [("RET", "114", "Account closed")])

    def test_bad_source_value_is_left_out_not_written(self):
        case = self.drc_case("100000000001", account="0000,1111")
        self.approve(case)
        missing = self.drc_case("100000000002", account="")
        self.approve(missing)
        plan = plan_file(self.module, D)
        self.assertEqual(plan.excluded_counts(), {Reason.BAD_SOURCE_DATA: 2})
        self.assertIn("cannot hold", plan.excluded[0].detail)

    def test_no_dispute_row(self):
        case = self.make_case("100000000001", "R01")  # no DRC row attached
        self.approve(case)
        self.assertEqual(plan_file(self.module, D).excluded_counts(), {Reason.NO_NPCI_ROW: 1})

    def test_manual_credit_warning_until_marked_done(self):
        case = self.drc_case("100000000001", "R02")  # DRC 103
        self.assertTrue(any("credited by hand" in w for w in plan_file(self.module, D).warnings))  # open too
        self.approve(case)
        self.assertTrue(any("credited by hand" in w for w in plan_file(self.module, D).warnings))
        services.mark_completed(case, self.mk, external_reference="CBS-REF-1")
        self.assertFalse(any("credited by hand" in w for w in plan_file(self.module, D).warnings))
        self.assertEqual(len(plan_file(self.module, D).rows), 1)  # COMPLETED still goes in

    def test_rule_defaults_verification_mode_saves_nothing(self):
        self.drc_case("100000000001")
        self.timeout_case("100000000002")
        plan = plan_file(self.module, D, rule_defaults=True)
        self.assertEqual([r.flag for r in plan.rows], ["DRC", "RET"])
        self.assertIsNone(plan.rows[0].action)
        self.assertFalse(NpciFile.objects.exists())


class RolesAuditStorageTests(NpciTestBase):
    def setUp(self):
        super().setUp()
        case = self.drc_case("100000000001")
        self.approve(case)

    def test_only_checker_generates(self):
        for role in (Role.MAKER, Role.ADMINISTRATOR, Role.OPERATIONS_USER, Role.AUDITOR):
            with self.subTest(role=role), self.assertRaises(RoleRequired):
                generate_file(self.user(role), self.module, D)
        self.assertFalse(NpciFile.objects.exists())

    def test_download_roles_and_log(self):
        f = generate_file(self.chk, self.module, D)
        for role in (Role.MAKER, Role.CHECKER, Role.OPERATIONS_USER):
            with self.subTest(role=role):
                self.assertEqual(download_file(self.user(role), f, source="test"), f.content.encode("ascii"))
        for role in (Role.ADMINISTRATOR, Role.AUDITOR):
            with self.subTest(role=role), self.assertRaises(RoleRequired):
                download_file(self.user(role), f)
        logs = OperationLog.objects.filter(operation=OperationType.NPCI_FILE_DOWNLOADED)
        self.assertEqual(logs.count(), 3)
        self.assertEqual(logs.first().details, {"npci_file_id": f.pk, "file_name": f.file_name, "sha256": f.sha256})

    def test_files_and_rows_are_append_only(self):
        f = generate_file(self.chk, self.module, D)
        row = f.rows.get()
        for fn in (lambda: NpciFile.objects.filter(pk=f.pk).update(remarks="x"), f.delete,
                   lambda: NpciFile.objects.all().delete(), row.delete,
                   lambda: NpciFileRow.objects.update(text="x")):
            with self.subTest(fn=fn), self.assertRaises(NpciFileImmutableError), transaction.atomic():
                fn()
        f.content = "changed"
        with self.assertRaises(NpciFileImmutableError), transaction.atomic():
            f.save()


class CompareTests(NpciTestBase):
    def test_compare_counts_and_masks(self):
        ref = (f"{HEADER}\r\n100000000001,DRC,14-07-2026,5,100000000001,000000001111,F.csv,102,online reverse\r\n"
               "100000000002,DRC,14-07-2026,6,100000000002,000000002222,F.csv,104,x\r\n").encode()
        gen = (f"{HEADER}\r\n100000000001,DRC,14-07-2026,5,100000000001,180000000001111,F.csv,102,Online Reversal\r\n"
               "100000000003,DRC,14-07-2026,6,100000000003,000000002222,F.csv,104,x\r\n").encode()
        result = compare(gen, ref)
        self.assertEqual((result.both, result.fields_compared, result.fields_matching), (1, 8, 7))
        self.assertEqual(result.column_mismatch_counts, {"shcrd": 1})
        self.assertEqual(result.mismatches[0]["generated"], "***********1111")
        self.assertNotIn("100000000002", "\n".join(result.lines()))


class HeldUntilApprovedTests(NpciTestBase):
    """Amendment 2: replies that need evidence or a person's decision (R06b,
    R07x, R04b, RRC 501 without a new credit) wait for a Checker; a reply the
    Checker rejected never goes in through the rule's suggestion."""

    EVIDENCE = {"evidence_cbs_reversal_date": D, "evidence_cbs_reversal_amount": "100",
                "evidence_cbs_reversal_rrn": "900000000001"}

    def test_r06b_needs_the_checker_with_evidence(self):
        case = self.rrc_case("100000000001", rule="R06b")
        plan = plan_file(self.module, D)
        self.assertEqual(plan.rows, [])
        self.assertEqual([(e.reason, e.flag, e.code) for e in plan.excluded], [(Reason.NEEDS_APPROVAL, "RRC", "501")])
        self.assertIn("R06b", plan.excluded[0].detail)
        self.assertIn("CBS reversal", plan.warnings[0])
        with self.assertRaisesRegex(WrongState, "Nothing to include"):
            generate_file(self.chk, self.module, D, confirm_partial=True)
        services.take(case, self.mk)
        services.submit(case, self.mk, **self.EVIDENCE)  # submitted with evidence: still the Checker's call
        self.assertEqual(plan_file(self.module, D).excluded_counts(), {Reason.NEEDS_APPROVAL: 1})
        services.approve(case, self.chk)
        f = generate_file(self.chk, self.module, D)
        self.assertEqual(list(f.rows.values_list("flag", "code", "reply_source", "action_type")),
                         [("RRC", "501", "APPROVED", "RRC_501_NO_CREDIT")])

    def test_r06b_switched_to_a_manual_credit_still_waits_for_the_checker(self):
        case = self.rrc_case("100000000001", rule="R06b")
        services.take(case, self.mk)
        services.submit(case, self.mk, action_type=ActionType.MANUAL_CREDIT_RRC_501)
        self.assertEqual(plan_file(self.module, D).excluded_counts(), {Reason.NEEDS_APPROVAL: 1})

    def test_evidence_reply_on_another_rule_waits_for_the_checker(self):
        case = self.rrc_case("100000000001", rule="R06")
        self.assertEqual(len(plan_file(self.module, D).rows), 1)  # R06's own reply: no approval needed
        services.take(case, self.mk)
        services.submit(case, self.mk, action_type=ActionType.RRC_501_NO_CREDIT, **self.EVIDENCE)
        plan = plan_file(self.module, D)
        self.assertEqual(plan.excluded_counts(), {Reason.NEEDS_APPROVAL: 1})
        self.assertIn("RRC_501_NO_CREDIT", plan.excluded[0].detail)

    def test_r07x_reply_only_once_approved(self):
        case = self.drc_case("100000000001", "R07x")
        plan = plan_file(self.module, D)
        self.assertEqual((plan.rows, plan.excluded), ([], []))  # no suggested reply: nothing listed
        services.take(case, self.mk)
        services.submit(case, self.mk, action_type=ActionType.DRC_102)
        plan = plan_file(self.module, D)
        self.assertEqual(plan.excluded_counts(), {Reason.NEEDS_APPROVAL: 1})
        self.assertIn("35 days", plan.excluded[0].detail)
        services.approve(case, self.chk)
        self.assertEqual([r.source for r in plan_file(self.module, D).rows], [ReplySource.APPROVED])

    def test_r04b_waits_for_the_account_check(self):
        case = self.timeout_case("100000000001", "R04b")
        self.assertEqual(plan_file(self.module, D).excluded_counts(), {Reason.NEEDS_APPROVAL: 1})
        self.approve(case)
        self.assertEqual([(r.flag, r.code) for r in plan_file(self.module, D).rows], [("TCC", "102")])

    def test_rejected_reply_waits_for_a_new_submission(self):
        case = self.drc_case("100000000001")
        services.take(case, self.mk)
        services.submit(case, self.mk)
        services.reject(case, self.chk, reason="customer not reversed yet")
        plan = plan_file(self.module, D)
        self.assertEqual([(e.reason, e.detail) for e in plan.excluded],
                         [(Reason.REJECTED, "customer not reversed yet")])
        self.assertTrue(any("rejected their last reply" in w for w in plan.warnings))
        services.submit(case, self.mk, action_type=ActionType.DRC_104)
        self.assertEqual([(r.code, r.source) for r in plan_file(self.module, D).rows],
                         [("104", ReplySource.SUBMITTED)])

    def test_rule_defaults_mode_ignores_the_holds(self):
        self.rrc_case("100000000001", rule="R06b")
        self.timeout_case("100000000002", "R04b")
        plan = plan_file(self.module, D, rule_defaults=True)
        self.assertEqual(([r.flag for r in plan.rows], plan.excluded), (["RRC", "TCC"], []))
