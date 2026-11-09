"""
UI refinements U2 / U3 backend (docs/ui_refinements_api_contract.txt): the
NPCI reply, amount and rule name per case with the reply-type tabs, "what
happened" on a case, the date status and SSKUPI blocks, the home "day" block,
response codes by reply type, Find an RRN, Download original, the settlement
summary and unexplained entries, and the audit "everything" timeline.

Every RRN, account, amount and file here is made up in the test.
"""
from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from api.tests.test_npci_files import UPI, NpciApiBase
from api.tests.test_slice2 import RAW_ACQ_NAME, Slice2Base, raw_csv
from api.tests.test_v1 import V1, V1TestBase
from core.models import OperationLog, OperationType, UserActivity, UserActivityType
from core.operations import log_operation
from exception_management import code_changes, services
from exception_management.models import CaseStatus, ExceptionCase
from npci_files.replies import date_file_summaries, finish_date_statuses
from exception_management.roles import Role
from file_processing.models import UploadedFile
from npci_files.tests.base import D
from reconciliation.models import RegisterPresence, RunStatus, TransactionMatchGroup
from reconciliation.overview import DateStatus, date_status
from rule_engine.models import CodeFlag, ResponseCode, RuleEvaluationRun
from settlement.models import ResultStatus, SettlementResult

A, O, M, C, U = Role.ADMINISTRATOR, Role.OPERATIONS_USER, Role.MAKER, Role.CHECKER, Role.AUDITOR
CASES = f"{UPI}/cases/"
DATES = f"{UPI}/dates/"
TABS = {"DRC", "TCC", "RET", "RRC", "NEEDS_REVIEW", "SETTLEMENT"}


def code(flag, number):
    return ResponseCode.objects.get(flag=flag, code=number)


# ---------------------------------------------------------------------------
# A3 / A4 cases: reply, amount, rule name, tabs, what happened
# ---------------------------------------------------------------------------
class CaseReplyTests(NpciApiBase):
    def setUp(self):
        super().setUp()
        # approved = R01 DRC approved; open_case = R05 RET (rule); held = R06b RRC (needs the Checker)
        self.r13 = self.make_case("100000000004", "R13")  # CBS only: no SSKUPI reply
        self.submitted = self.drc_case("100000000005", "R02", "75.25")
        self.submit_only(self.submitted)

    def rows(self, who=M, **params):
        body = self.ok(who, CASES, {"page_size": 50, **params})
        return body, {r["id"]: r for r in body["results"]}

    def test_reply_amount_rule_name_and_file(self):
        body, rows = self.rows()
        drc102 = code("DRC", "102")
        a = rows[self.approved.pk]
        self.assertEqual(a["reply"], {
            "reply_type": "DRC", "reply_type_label": "DRC", "action_type": "DRC_102",
            "action_type_label": "DRC reply 102 (customer reversed online)", "flag": "DRC", "code": "102",
            "text": drc102.description, "label": f"DRC 102 - {drc102.description}", "source": "APPROVED",
            "source_label": "The Checker-approved reply"})
        self.assertEqual((a["amount"], a["amount_source"], a["rule_name"], a["npci_file"]),
                         ("140.50", "DRC", self.rules["R01"].name, None))
        s = rows[self.submitted.pk]["reply"]
        self.assertEqual((s["reply_type"], s["code"], s["source"]), ("DRC", "103", "SUBMITTED"))
        self.assertEqual(rows[self.submitted.pk]["amount"], "75.25")
        o = rows[self.open_case.pk]["reply"]
        self.assertEqual((o["reply_type"], o["flag"], o["code"], o["source"]), ("RET", "RET", "115", "RULE"))
        self.assertEqual(rows[self.held.pk]["reply"]["reply_type"], "RRC")
        r13 = rows[self.r13.pk]
        self.assertEqual((r13["reply"]["reply_type"], r13["reply"]["flag"], r13["reply"]["label"], r13["amount"],
                          r13["amount_source"]), ("NEEDS_REVIEW", None, None, None, None))
        self.generate(confirm_partial=True)
        # R01 DRC 102 is a no-money reply: the file closes the case (owner 2026-11-09), so it
        # leaves the active queue; status=all still lists it.
        _body, rows = self.rows(status="all")
        self.assertEqual(rows[self.approved.pk]["npci_file"]["file_name"], "SSKUPI14072026.csv")
        self.assertEqual((rows[self.approved.pk]["status"], rows[self.approved.pk]["closed_by_system"]),
                         ("COMPLETED", True))
        self.assertIsNone(rows[self.held.pk]["npci_file"])

    def test_tabs_filter_and_counts(self):
        body, _rows = self.rows()
        self.assertEqual(body["reply_type_counts"],
                         {"DRC": 2, "TCC": 0, "RET": 1, "RRC": 1, "NEEDS_REVIEW": 1, "SETTLEMENT": 0})
        body, rows = self.rows(reply_type="DRC,RET")
        self.assertEqual(set(rows), {self.approved.pk, self.submitted.pk, self.open_case.pk})
        self.assertEqual(body["count"], 3)
        self.assertEqual(body["reply_type_counts"]["NEEDS_REVIEW"], 1)  # counts ignore the tab itself
        body, rows = self.rows(reply_type="NEEDS_REVIEW")
        self.assertEqual(list(rows), [self.r13.pk])
        # Other filters do narrow the counts.
        body, _rows = self.rows(status="PENDING_APPROVAL")
        self.assertEqual(body["reply_type_counts"], {**{t: 0 for t in TABS}, "DRC": 1})
        r = self.as_(M).get(CASES, {"reply_type": "DRC,XYZ"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("reply_type", r.json()["errors"])

    def test_list_queries_do_not_grow_with_the_page(self):
        for n in range(6, 12):
            self.make_case(f"1000000001{n:02d}", "R13")

        def queries(size):
            client = self.as_(M)
            with CaptureQueriesContext(connection) as q:
                self.assertEqual(client.get(CASES, {"page_size": size}).status_code, 200)
            return len(q)

        self.assertEqual(queries(2), queries(10))

    def test_what_happened(self):
        RegisterPresence.objects.create(match_group=self.r13.match_group, leg="NPCI", present=True,
                                        amount=Decimal("12.00"), direction="CREDIT", response_code="U3")
        RegisterPresence.objects.create(match_group=self.r13.match_group, leg="CBS", present=True,
                                        amount=Decimal("12.00"), direction="CREDIT")
        wh = self.ok(U, f"{CASES}{self.r13.pk}/")["what_happened"]
        self.assertEqual(wh["cbs"], {"present": True, "amount": "12.00", "direction": "CREDIT"})
        self.assertEqual(wh["npci"], {"present": True, "amount": "12.00", "direction": "CREDIT",
                                      "response_code": "U3", "status": "DECLINED"})
        self.assertEqual((wh["sskupi"]["status"], wh["reply"]["reply_type"]), ("NO_FILE_REPLY", "NEEDS_REVIEW"))

        open_case = self.ok(U, f"{CASES}{self.open_case.pk}/")
        self.assertEqual(open_case["what_happened"]["cbs"]["present"], False)
        sk = open_case["what_happened"]["sskupi"]
        self.assertEqual((sk["status"], sk["file_date"], sk["file"], sk["reason"]),
                         ("NOT_YET_IN_FILE", "2026-07-14", None, None))
        sk = self.ok(U, f"{CASES}{self.held.pk}/")["what_happened"]["sskupi"]
        self.assertEqual((sk["status"], sk["reason"]), ("HELD_FOR_CHECKER", "NEEDS_APPROVAL"))
        self.assertIn("R06b", sk["detail"])

        self.generate(confirm_partial=True)
        sk = self.ok(U, f"{CASES}{self.approved.pk}/")["what_happened"]["sskupi"]
        self.assertEqual((sk["status"], sk["file"]["file_name"], sk["file"]["line"], sk["changed_since_included"]),
                         ("IN_FILE", "SSKUPI14072026.csv", 1, False))

    def test_rejected_reply_is_left_out_and_transitions_carry_the_reply(self):
        services.reject(self.submitted, self.chk, reason="Wrong code")
        sk = self.ok(M, f"{CASES}{self.submitted.pk}/")["what_happened"]["sskupi"]
        self.assertEqual((sk["status"], sk["reason"], sk["detail"]), ("LEFT_OUT", "REJECTED", "Wrong code"))
        # The answer to a transition is the same detail: the button label is there after each click.
        r = self.post(self.as_(M), f"{CASES}{self.r13.pk}/take/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["reply"]["reply_type"], r.json()["what_happened"]["sskupi"]["status"]),
                         ("NEEDS_REVIEW", "NO_FILE_REPLY"))
        approve = self.ok(C, f"{CASES}{self.held.pk}/")
        self.assertEqual(approve["reply"]["label"], f"RRC 501 - {code('RRC', '501').description}")


# ---------------------------------------------------------------------------
# A0 date status (pure function)
# ---------------------------------------------------------------------------
class DateStatusTests(V1TestBase):
    def test_rules(self):
        now = timezone.now()
        all_loaded = {"expected": 3, "loaded": 3}
        missing = {"expected": 3, "loaded": 1}
        ev = SimpleNamespace(started_at=now)
        running = SimpleNamespace(pk=5, status=RunStatus.RUNNING, get_status_display=lambda: "Running", error="")
        failed = SimpleNamespace(pk=6, status=RunStatus.FAILED, error="boom")
        done = SimpleNamespace(pk=7, status=RunStatus.SUCCEEDED, error="")
        cases = [
            ((all_loaded, ev, running, 4, None), DateStatus.RUNNING),
            ((missing, None, None, 0, now), DateStatus.WAITING_FOR_FILES),
            ((all_loaded, None, None, 0, now), DateStatus.READY_TO_RUN),
            ((all_loaded, None, failed, 0, now), DateStatus.READY_TO_RUN),
            ((all_loaded, ev, done, 0, now + timedelta(minutes=1)), DateStatus.READY_TO_RUN),
            ((missing, ev, done, 2, now + timedelta(minutes=1)), DateStatus.WAITING_FOR_FILES),
            ((missing, ev, done, 2, now - timedelta(minutes=1)), DateStatus.CASES_OPEN),
            ((all_loaded, ev, done, 0, now - timedelta(minutes=1)), DateStatus.DONE),
            ((all_loaded, ev, None, 0, None), DateStatus.DONE),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(date_status(*args)[0], expected)
        self.assertEqual(date_status(missing, None, None, 0, now)[1], "2 expected file(s) are not loaded yet.")
        self.assertIn("failed: boom", date_status(all_loaded, None, failed, 0, now)[1])
        self.assertIn("failed: boom", date_status(all_loaded, ev, failed, 1, now - timedelta(minutes=1))[1])


# ---------------------------------------------------------------------------
# A1 / A2 dates list, date detail and the home "day" block
# ---------------------------------------------------------------------------
class DaySskupiTests(NpciApiBase):
    def test_dates_list_and_detail_carry_status_and_sskupi(self):
        row = self.ok(U, DATES)["results"][0]
        self.assertEqual(row["business_date"], "2026-07-14")
        self.assertIn(row["status"], DateStatus.values)
        self.assertEqual(row["status_label"], DateStatus(row["status"]).label)
        self.assertEqual(row["sskupi"], {"state": "NONE", "state_label": "Not generated", "file_count": 0,
                                         "latest": None, "not_downloaded": []})
        file_id = self.generate(confirm_partial=True).json()["id"]
        sk = self.ok(U, DATES)["results"][0]["sskupi"]
        self.assertEqual((sk["state"], sk["file_count"], sk["latest"]["id"], sk["latest"]["file_name"],
                          sk["latest"]["row_count"], sk["latest"]["last_download"]),
                         ("GENERATED", 1, file_id, "SSKUPI14072026.csv", 2, None))
        self.as_(O).get(f"{UPI}/npci-files/{file_id}/download/")
        sk = self.ok(U, f"{DATES}2026-07-14/")["sskupi"]
        self.assertEqual((sk["state"], sk["latest"]["download_count"], sk["latest"]["last_download"]["by"]),
                         ("DOWNLOADED", 1, self.users[O].username))

    def test_dates_list_queries_per_page(self):
        def queries():
            client = self.as_(U)
            with CaptureQueriesContext(connection) as q:
                client.get(DATES)
            return len(q)

        before = queries()
        for n in range(3):
            self.drc_case(f"10000000020{n}", d=D - timedelta(days=n + 1))
        self.assertEqual(queries(), before)

    def test_day_block(self):
        from api.views import day_block

        day = day_block(self.module, D, self.users[C])
        self.assertEqual(day["business_date"], D)
        self.assertEqual(day["link"], "/upi/dates/2026-07-14")
        self.assertEqual(day["run"]["state"], "DONE")  # results written without a ReconciliationRun
        self.assertEqual((day["results"]["exceptions"], day["cases"]), (3, {"open": 3, "total": 3}))
        sk = day["sskupi"]
        self.assertEqual((sk["state"], sk["waiting_rows"], sk["held_for_checker"], sk["left_out"],
                          sk["next_file_name"], sk["can_generate"]),
                         ("NONE", 2, 1, 0, "SSKUPI14072026.csv", True))
        self.assertFalse(day_block(self.module, D, self.users[U])["sskupi"]["can_generate"])
        self.generate(confirm_partial=True)
        sk = day_block(self.module, D, self.users[C])["sskupi"]
        self.assertEqual((sk["state"], sk["file_count"], sk["waiting_rows"], sk["next_file_name"],
                          [f["sequence"] for f in sk["files"]]),
                         ("GENERATED", 1, 0, "SSKUPI14072026_2.csv", [1]))

    def finish_cases(self, d=D):
        """Every case of d worked (COMPLETED) and the last evaluation newer than every file."""
        ExceptionCase.objects.filter(business_date=d).update(status=CaseStatus.COMPLETED)
        RuleEvaluationRun.objects.filter(business_date=d).update(started_at=timezone.now() + timedelta(minutes=5))

    def date_status(self):
        detail = self.ok(U, f"{DATES}2026-07-14/")
        listed = self.ok(U, DATES)["results"][0]
        self.assertEqual((listed["status"], listed["status_detail"]), (detail["status"], detail["status_detail"]))
        return detail["status"], detail["status_detail"]

    def test_file_pending_until_generated_and_downloaded(self):
        """Owner Q2 (2026-10-27): DONE only once the SSKUPI file is generated AND downloaded, nothing waiting."""
        self.assertEqual(self.date_status()[0], "WAITING_FOR_FILES")  # files newer than the evaluation
        self.finish_cases()
        status, detail = self.date_status()
        self.assertEqual(status, "FILE_PENDING")
        self.assertIn("3 reply row(s) wait for the SSKUPI file: generate SSKUPI14072026.csv", detail)
        file_id = self.generate().json()["id"]
        status, detail = self.date_status()
        self.assertEqual((status, detail), ("FILE_PENDING", "All cases worked. Download SSKUPI14072026.csv."))
        self.assertEqual(self.ok(U, DATES)["results"][0]["sskupi"]["not_downloaded"], ["SSKUPI14072026.csv"])
        self.as_(O).get(f"{UPI}/npci-files/{file_id}/download/")
        status, detail = self.date_status()
        self.assertEqual((status, detail), ("DONE", "No case open; SSKUPI file(s) generated and downloaded."))
        from api.views import day_block
        self.assertEqual(day_block(self.module, D, self.users[U])["status"], "DONE")
        # A later reply for the date (a _2 file) makes it pending again.
        late = self.drc_case("100000000009", "R01")
        ExceptionCase.objects.filter(pk=late.pk).update(status=CaseStatus.COMPLETED)
        self.finish_cases()
        self.assertIn("generate SSKUPI14072026_2.csv", self.date_status()[1])

    def test_nothing_to_send_is_done(self):
        d = D - timedelta(days=1)
        self.make_case("100000000301", "R13", d)  # CBS only: no SSKUPI reply
        self.finish_cases(d)
        rows = {d: {"status": "DONE", "status_label": "Done", "status_detail": "", "last_run": None}}
        finish_date_statuses(self.module, rows, date_file_summaries(self.module, [d]))
        self.assertEqual((rows[d]["status"], rows[d]["status_detail"]),
                         ("DONE", "No case open; nothing to send in an SSKUPI file."))

    def test_summary_has_the_day_for_every_role(self):
        for role in (A, O, M, C, U):
            with self.subTest(role=role):
                body = self.ok(role, f"{UPI}/summary/")
                self.assertEqual(body["day"]["business_date"], body["business_date"])
                self.assertEqual(body["day"]["run"]["state"], "NOT_RUN")
                self.assertEqual(body["day"]["sskupi"]["state"], "NONE")


# ---------------------------------------------------------------------------
# A6 response codes
# ---------------------------------------------------------------------------
class ResponseCodeTests(NpciApiBase):
    URL = f"{UPI}/settings/response-codes/"

    def test_sskupi_text_and_reply_types(self):
        body = self.ok(U, self.URL, {"ordering": "reply_type", "page_size": 500})
        rows = body["results"]
        order = ["DRC", "RRC", "TCC", "RET", "OTHER"]
        ranks = [order.index(r["reply_type"]) for r in rows]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(sum(t["count"] for t in body["reply_types"]), body["count"])
        self.assertEqual([t["value"] for t in body["reply_types"]], [t for t in order if t in
                                                                     {r["reply_type"] for r in rows}])
        drc = next(r for r in rows if r["flag"] == "DRC" and r["code"] == "102")
        self.assertEqual((drc["in_sskupi_file"], drc["sskupi_text"], drc["sskupi_ready"], drc["sskupi_problem"]),
                         (True, drc["description"], True, None))
        for r in rows:
            if r["flag"] == CodeFlag.NPCI_RESPONSE:
                self.assertEqual((r["reply_type"], r["in_sskupi_file"], r["sskupi_text"]), ("OTHER", False, None))
            if r["flag"] in ("DRC", "TCC", "RET", "RRC") and r["code"] is None:
                self.assertFalse(r["sskupi_ready"])
        self.assertEqual(self.as_(U).get(self.URL, {"ordering": "x"}).status_code, 400)
        self.assertEqual(self.ok(U, self.URL, {"flag": "RET"})["reply_types"][0]["value"], "RET")


# ---------------------------------------------------------------------------
# B1 Find an RRN
# ---------------------------------------------------------------------------
class RrnLookupTests(NpciApiBase):
    def url(self, rrn):
        return f"{UPI}/rrn/{rrn}/"

    def test_roles_found_and_not_found(self):
        self.assert_read_matrix(self.url("100000000001"))
        body = self.ok(U, self.url("100000000001"))
        self.assertTrue(body["found"])
        g = TransactionMatchGroup.objects.get(rrn="100000000001")
        self.assertEqual(body["transactions"][0]["id"], g.pk)
        self.assertEqual(body["transactions"][0]["link"], f"/upi/dates/2026-07-14/groups/{g.pk}")
        self.assertEqual(body["transactions"][0]["rule_code"], "R01")
        case = body["cases"][0]
        self.assertEqual((case["id"], case["link"], case["rule_name"]),
                         (self.approved.pk, f"/upi/cases/{self.approved.pk}", self.rules["R01"].name))
        self.assertTrue(case["reply_label"].startswith("DRC 102 - "))
        self.assertEqual([(r["register_type"], r["amount"]) for r in body["file_rows"]], [("DRC", "140.50")])
        self.assertEqual(self.ok(U, self.url("%27100000000001"))["rrn"], "100000000001")  # Excel apostrophe
        empty = self.ok(U, self.url("999999999999"))
        self.assertEqual((empty["found"], empty["transactions"], empty["cases"], empty["file_rows"]),
                         (False, [], [], []))
        r = self.as_(U).get(self.url("12-34"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("rrn", r.json()["errors"])

    def test_replaced_transactions(self):
        TransactionMatchGroup.objects.filter(rrn="100000000002").update(is_superseded=True)
        body = self.ok(U, self.url("100000000002"))
        self.assertEqual((len(body["transactions"]), body["replaced_count"], body["found"]), (0, 1, True))
        body = self.ok(U, self.url("100000000002"), {"include_replaced": "true"})
        self.assertEqual((len(body["transactions"]), body["transactions"][0]["is_superseded"]), (1, True))
        self.assertEqual(self.as_(U).get(self.url("100000000002"), {"include_replaced": "x"}).status_code, 400)


# ---------------------------------------------------------------------------
# B2 Download original
# ---------------------------------------------------------------------------
class OriginalDownloadTests(Slice2Base):
    def setUp(self):
        super().setUp()
        r = self.do_upload((RAW_ACQ_NAME, raw_csv("orig"), "RAW_DATA_ACQ", "1C"))
        self.assertEqual(r.status_code, 201, r.content)
        self.f = UploadedFile.objects.get(pk=r.json()["accepted"][0]["id"])
        self.url = f"{V1}/upi/files/{self.f.pk}/original/"

    def fetch(self, who):
        r = self.as_(who).get(self.url)
        content = None
        if r.status_code == 200:
            content = b"".join(r.streaming_content)  # the test client closes the response (and file) at the end
        return r, content

    def logs(self):
        return OperationLog.objects.filter(operation=OperationType.ORIGINAL_FILE_DOWNLOADED)

    def test_every_role_gets_the_exact_bytes_and_it_is_logged(self):
        for role in (A, O, M, C, Role.AUDITOR):
            with self.subTest(role=role):
                r, content = self.fetch(role)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(content, raw_csv("orig"))
                self.assertEqual(r["Content-Type"], "text/csv")
                self.assertIn(f'filename="{RAW_ACQ_NAME}"', r["Content-Disposition"])
                self.assertEqual(r["X-Content-SHA256"], self.f.sha256_hash)
        self.assertEqual(self.as_(self.norole).get(self.url).status_code, 403)
        self.assertEqual(self.as_(None).get(self.url).status_code, 401)
        self.assertEqual(self.logs().count(), 5)
        log = self.logs().first()
        self.assertEqual((log.uploaded_file_id, log.business_date, log.details["filename"]),
                         (self.f.pk, D, RAW_ACQ_NAME))
        detail = self.as_(U).get(f"{V1}/upi/files/{self.f.pk}/").json()
        self.assertEqual((detail["original"]["available"], detail["download_count"]), (True, 5))
        self.assertTrue(detail["original"]["download_url"].endswith(f"/files/{self.f.pk}/original/"))
        self.assertEqual(detail["history"][-1]["operation"], OperationType.ORIGINAL_FILE_DOWNLOADED)
        audit = self.as_(A).get(f"{V1}/audit/operations/", {"event_type": "ORIGINAL_FILE_DOWNLOADED"}).json()
        self.assertEqual((audit["count"], audit["results"][0]["link"]), (5, f"/upi/files/{self.f.pk}"))
        meta = self.as_(A).get(f"{V1}/audit/meta/").json()
        self.assertIn("ORIGINAL_FILE_DOWNLOADED", [t["value"] for t in meta["operation_types"]])

    def refused(self, status, reason):
        r = self.as_(O).get(self.url)
        self.assertEqual((r.status_code, r.json()["reason"]), (status, reason))
        self.assertEqual(self.logs().count(), 0)
        detail = self.as_(O).get(f"{V1}/upi/files/{self.f.pk}/").json()["original"]
        self.assertEqual((detail["available"], detail["reason"], detail["download_url"]), (False, reason, None))

    def test_missing_and_changed(self):
        stored = Path(self.f.path)
        stored.write_bytes(raw_csv("orig") + b"extra")
        self.refused(410, "CHANGED")
        stored.unlink()
        self.refused(410, "MISSING")

    def test_never_serves_outside_the_upload_folder(self):
        outside = Path(self.work_dir) / "secret.txt"
        outside.write_text("not an upload")
        for path in (str(outside), str(Path(self.work_dir) / "uploads" / ".." / "secret.txt"), "/nowhere", ""):
            with self.subTest(path=path):
                UploadedFile.objects.filter(pk=self.f.pk).update(path=path)
                self.refused(404, "NOT_STORED")
        self.assertEqual(self.as_(O).get(f"{V1}/upi/files/999999/original/").status_code, 404)


# ---------------------------------------------------------------------------
# B3 / B4 settlement summary and unexplained entries
# ---------------------------------------------------------------------------
SNAPSHOT = {
    "sop": {"items": [["100000000011", "R02", "G07", "-100.00"], ["100000000012", "R05", "G04", "50.00"],
                      ["100000000013", "R13", "G08", "-10.00"], ["100000000014", "R06b", "G06", "20.00"],
                      ["100000000015", "R02", "G07", "-1.00"]],
            "cbs_rows_without_valid_rrn": {"count": 1, "amount": "9.00"}},
    "g09": {"rows": [["100000000016", "Credit Adjustment", "SSK", "OTHER", "30.00", "2026-07-14T10:00:00+05:30",
                      "DC1", "30.00"]],
            "row_format": ["rrn", "adjtype", "remitter", "beneficiary", "amount", "raised_at", "cycle", "signed"],
            "not_in_sign_table": [["100000000017", "Odd type", "SSK", "SSK", "5.00", None]],
            "unassigned": []},
    "contributing": [["100000000018", "UNATTRIBUTED", "R15", "1C", "REMITTER", "70.00", "00", "PRIMARY"],
                     ["100000000012", "G04", "R05", "1C", "REMITTER", "50.00", "00", "PRIMARY"]],
    "contributing_format": ["rrn", "g_rule", "rule", "cycle", "side", "amount", "npci_code", "role"],
    "not_placed": [],
    "cycles": [{"cycle": "1C", "side": "UNCOVERED",
                "detail": {"lines": [{"category": "OTHER", "description": "Synthetic line", "debit": "0.00",
                                      "credit": "3.00"}]}}],
}


class SettlementSummaryTests(V1TestBase):
    URL = f"{UPI}/dates/2026-07-14/settlement/"

    def result(self, **kw):
        values = dict(payment_module=self.module, business_date=D, status=ResultStatus.UNEXPLAINED,
                      reasons=["1C REMITTER: unexplained Rs 70.00, 1 txn"], sop_cbs_net=Decimal("-500.00"),
                      sop_ntsl_net=Decimal("-459.00"), sop_difference=Decimal("-41.00"),
                      sop_explained=Decimal("-41.00"), sop_unexplained=Decimal("0.00"),
                      unexplained_amount=Decimal("70.00"), unexplained_count=1, fees_net=Decimal("0"),
                      snapshot=SNAPSHOT, computed_at=timezone.now())
        values.update(kw)
        return SettlementResult.objects.create(**values)

    def test_summary(self):
        self.result()
        s = self.as_(U).get(self.URL).json()["summary"]
        self.assertEqual((s["status"], s["tallied"], s["cbs_net"], s["ntsl_net"], s["explained"], s["unexplained"],
                          s["unexplained_entries"], s["self_check_failed"], s["settlement_case"]),
                         ("UNEXPLAINED", False, "-500.00", "-459.00", "-41.00", "0.00", 3, False, None))
        self.assertEqual([(t["type"], t["count"], t["amount"], t["in_sop_check"]) for t in s["explained_by_type"]],
                         [("DRC", 2, "-101.00", True), ("RET", 1, "50.00", True), ("RRC", 1, "20.00", True),
                          ("LATE_ADJUSTMENTS", 1, "30.00", False), ("OTHER", 1, "-10.00", True)])
        drc = s["explained_by_type"][0]["rules"]
        self.assertEqual(drc, [{"rule": "R02", "g_rule": "G07", "g_label": "DRC debited, not reversed (CBS side)",
                                "count": 2, "amount": "-101.00"}])
        self.assertEqual(s["cbs_rows_without_valid_rrn"], {"count": 1, "amount": "9.00"})

    def test_late_adjustments_can_become_a_note(self):
        """Owner Q3 (waiting on ops): one switch moves G09 out of the breakdown into a note."""
        self.result()
        self.assertEqual(self.as_(U).get(self.URL).json()["summary"]["notes"], {})
        with mock.patch("api.results.LATE_ADJUSTMENTS_IN_BREAKDOWN", False):
            s = self.as_(U).get(self.URL).json()["summary"]
        self.assertNotIn("LATE_ADJUSTMENTS", [t["type"] for t in s["explained_by_type"]])
        self.assertEqual(s["notes"]["late_adjustments"]["count"], 1)
        self.assertEqual(s["notes"]["late_adjustments"]["amount"], "30.00")

    def test_not_ready_and_no_result(self):
        self.assertEqual(self.as_(U).get(self.URL).status_code, 404)
        self.result(status=ResultStatus.NOT_READY, sop_cbs_net=None, sop_ntsl_net=None, sop_difference=None,
                    sop_explained=None, sop_unexplained=None, snapshot={"not_ready": ["No NTSL lines"]})
        s = self.as_(U).get(self.URL).json()["summary"]
        self.assertEqual((s["status"], s["cbs_net"], s["explained_by_type"], s["unexplained_entries"]),
                         ("NOT_READY", None, [], 0))

    def test_unexplained_entries(self):
        self.result()
        url = f"{self.URL}transactions/"
        body = self.as_(U).get(url, {"kind": "unexplained"}).json()
        self.assertEqual(body["count"], 3)
        self.assertEqual([r["source"] for r in body["results"]],
                         ["NPCI_NOT_ATTRIBUTED", "ADJUSTMENT_NOT_SIGNED", "NTSL_LINE_NOT_COVERED"])
        self.assertEqual((body["results"][0]["amount"], body["results"][2]["amount"], body["results"][2]["rrn"]),
                         ("70.00", "3.00", None))
        self.assertEqual(body["counts"]["unexplained"], 3)
        self.assertEqual(self.as_(U).get(url, {"kind": "unexplained", "rrn": "100000000017"}).json()["count"], 1)
        npci = self.as_(U).get(url, {"kind": "npci", "unexplained": "true"}).json()
        self.assertEqual([r["rrn"] for r in npci["results"]], ["100000000018"])
        self.assertEqual(self.as_(U).get(url, {"kind": "npci"}).json()["count"], 2)
        self.assertEqual(self.as_(U).get(url, {"unexplained": "x"}).status_code, 400)


# ---------------------------------------------------------------------------
# B5 audit "everything"
# ---------------------------------------------------------------------------
class EverythingAuditTests(NpciApiBase):
    URL = f"{V1}/audit/everything/"

    def setUp(self):
        super().setUp()  # case events: created, taken, submitted, approved
        call_command("seed_settlement_cycles", stdout=StringIO())
        call_command("seed_ntsl_category_map", stdout=StringIO())  # a SEEDED settings event (system)
        code_changes.propose_code_change(code("DRC", "102"), self.mk, new_description="Synthetic text", reason="why")
        UserActivity.objects.create(activity_type=UserActivityType.LOGIN, user=self.mk, username=self.mk.username)
        UserActivity.objects.create(activity_type=UserActivityType.LOGIN_FAILED, username="nobody9")
        log_operation(OperationType.RUN_STARTED, user=self.users[O], payment_module=self.module, business_date=D,
                      run_id=41)
        self.admin = self.as_(A)  # one session: each new login would add a LOGIN row to the timeline

    def get(self, params=None):
        r = self.admin.get(self.URL, params or {})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def all_rows(self, **params):
        body = self.get({"page_size": 500, **params})
        return body, body["results"]

    def test_roles(self):
        for role in (A, U):
            self.assertEqual(self.as_(role).get(self.URL).status_code, 200)
        for role in (O, M, C):
            self.assertEqual(self.as_(role).get(self.URL).status_code, 403)
        self.assertEqual(self.as_(self.norole).get(self.URL).status_code, 403)
        self.assertEqual(self.as_(None).get(self.URL).status_code, 401)

    def test_one_timeline_with_sentences(self):
        body, rows = self.all_rows()
        self.assertEqual({r["source"] for r in rows}, {"case_event", "settings_event", "user_activity", "operation"})
        times = [r["created_at"] for r in rows]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual(len({r["id"] for r in rows}), len(rows))
        summaries = [r["summary"] for r in rows]
        mk, chk, ops = self.mk.username, self.chk.username, self.users[O].username
        self.assertIn(f"{chk} approved reply DRC 102 - {code('DRC', '102').description} on case #{self.approved.pk}",
                      summaries)
        self.assertIn(f"{mk} took case #{self.approved.pk}", summaries)
        self.assertIn(f"The system created case #{self.approved.pk}", summaries)
        self.assertIn(f"{mk} logged in", summaries)
        self.assertIn("Failed login for nobody9", summaries)
        self.assertIn(f"{ops} started reconciliation run #41 for 14 Jul 2026", summaries)
        self.assertTrue(any(s.startswith(f"{mk} proposed the response code change") for s in summaries))
        approved = next(r for r in rows if r["event_type"] == "APPROVED" and r["source"] == "case_event")
        self.assertEqual((approved["link"], approved["module"], approved["details"]["case_id"]),
                         (f"/upi/cases/{self.approved.pk}", "upi", self.approved.pk))
        login = next(r for r in rows if r["source"] == "user_activity")
        self.assertEqual((login["module"], login["link"]), (None, None))
        oldest = self.get({"ordering": "created_at", "page_size": 500})["results"]
        self.assertEqual([r["id"] for r in oldest], [r["id"] for r in reversed(rows)])

    def test_filters_and_paging(self):
        total, rows = self.all_rows()
        page = self.get({"page_size": 2, "page": 2})
        self.assertEqual((page["count"], [r["id"] for r in page["results"]]),
                         (total["count"], [r["id"] for r in rows[2:4]]))
        _b, rows = self.all_rows(source="user_activity,operation")
        self.assertEqual({r["source"] for r in rows}, {"user_activity", "operation"})
        _b, rows = self.all_rows(module="upi")
        self.assertNotIn("user_activity", {r["source"] for r in rows})  # logins have no module
        _b, rows = self.all_rows(event_type="APPROVED")
        self.assertEqual({(r["source"], r["event_type"]) for r in rows}, {("case_event", "APPROVED")})
        _b, rows = self.all_rows(event_type="LOGIN,RUN_STARTED")
        self.assertEqual({r["event_type"] for r in rows}, {"LOGIN", "RUN_STARTED"})
        _b, rows = self.all_rows(user=self.mk.username.upper())
        self.assertTrue(rows and all(r["username"] == self.mk.username for r in rows))
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertEqual(self.get({"date_from": tomorrow})["count"], 0)
        for bad in ({"source": "x"}, {"event_type": "x"}, {"ordering": "x"}, {"date_from": "x"}):
            with self.subTest(bad=bad):
                self.assertEqual(self.admin.get(self.URL, bad).status_code, 400)
        meta = self.ok(A, f"{V1}/audit/meta/")
        self.assertEqual([s["value"] for s in meta["everything_sources"]],
                         ["case_event", "settings_event", "user_activity", "operation"])

    def test_queries_do_not_grow_with_the_page(self):
        def queries():
            with CaptureQueriesContext(connection) as q:
                self.admin.get(self.URL, {"page_size": 500})
            return len(q)

        before = queries()
        for n in range(20):
            UserActivity.objects.create(activity_type=UserActivityType.LOGOUT, username=f"user{n}")
            log_operation(OperationType.RUN_STARTED, user=self.users[O], payment_module=self.module,
                          business_date=D, run_id=n)
        self.assertEqual(queries(), before)
