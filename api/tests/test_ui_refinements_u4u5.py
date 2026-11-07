"""
UI refinements U4 / U5 backend (docs/ui_refinements_api_contract_u4u5.txt):
the upload review checklist (detect), the files "ids" filter, the NPCI files
report and the dashboard. Every RRN, amount and file here is made up.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from api.tests.test_npci_files import UPI, NpciApiBase
from api.tests.test_slice2 import (
    CBS_NAME,
    DRC_NAME,
    FILES,
    NTSL_NAME,
    RAW_ACQ_NAME,
    Slice2Base,
    cbs_xml,
    drc_csv,
    ntsl_zip,
    raw_csv,
    upload,
)
from api.tests.test_v1 import ALL_ROLES, V1
from core.models import Direction, RegisterType
from exception_management.models import CaseStatus, ExceptionCase
from exception_management.roles import Role
from file_processing.models import ProcessingStatus, UploadedFile
from npci_files.tests.base import D
from rule_engine.models import RuleEvaluationRun
from transactions.models import TransactionFact

A, O, M, C, U = Role.ADMINISTRATOR, Role.OPERATIONS_USER, Role.MAKER, Role.CHECKER, Role.AUDITOR
REPORT = f"{UPI}/reports/npci-files/"
DASHBOARD = f"{UPI}/dashboard/"
JULY = {"date_from": "2026-07-01", "date_to": "2026-07-31"}


# ---------------------------------------------------------------------------
# E1 / E2 upload page
# ---------------------------------------------------------------------------
class UploadReviewTests(Slice2Base):
    url = f"{FILES}detect/"

    def setUp(self):
        super().setUp()
        self.loaded_2c = self.make_file("RAW_DATA_ACQ", "2C")
        self.failed_drc = self.make_file("DRC", None, status=ProcessingStatus.VALIDATION_FAILED)

    def detect(self, **data):
        files = [upload(RAW_ACQ_NAME, raw_csv()), upload(DRC_NAME, drc_csv()), upload(NTSL_NAME, ntsl_zip()),
                 upload(CBS_NAME, cbs_xml()), upload("notes.csv", b"a,b\n1,2\n"),
                 upload(RAW_ACQ_NAME.replace("_1C", "_2C"), raw_csv("b"))]
        r = self.as_(O).post(self.url, {"files": files, **data})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()["checklist"]

    def slot(self, checklist, key, cycle):
        cfg = self.cfg[key]
        return next(s for s in checklist["slots"] if s["file_type_config_id"] == cfg.pk and s["cycle"] == cycle)

    def test_expected_vs_batch_vs_missing(self):
        cl = self.detect()
        self.assertEqual(cl["business_date"], "2026-07-14")
        raw1 = self.slot(cl, "RAW_DATA_ACQ", "1C")
        self.assertEqual((raw1["state"], raw1["group"], raw1["batch_files"]), ("MISSING", "IN_BATCH", [0]))
        raw2 = self.slot(cl, "RAW_DATA_ACQ", "2C")
        self.assertEqual((raw2["state"], raw2["group"], raw2["batch_files"], raw2["existing_files"][0]["id"]),
                         ("LOADED", "ALREADY_PRESENT", [5], self.loaded_2c.pk))  # the upload will refuse file 5
        drc = self.slot(cl, "DRC", None)
        self.assertEqual((drc["state"], drc["group"], drc["batch_files"]), ("FAILED", "IN_BATCH", [1]))
        self.assertEqual(self.slot(cl, "NTSL", "DC1")["group"], "IN_BATCH")
        self.assertEqual(self.slot(cl, "CBS_GL_EXTRACT", None)["group"], "IN_BATCH")  # no date in its name
        self.assertEqual(self.slot(cl, "RAW_DATA_ACQ", "3C")["group"], "MISSING")
        s = cl["summary"]
        self.assertEqual((s["already_present"], s["in_batch"], s["outside_checklist"]), (1, 4, 1))
        self.assertEqual(s["missing_after_batch"], s["expected"] - 5)
        self.assertEqual(cl["outside_checklist"], [4])
        self.assertFalse(UploadedFile.objects.exclude(pk__in=[self.loaded_2c.pk, self.failed_drc.pk]).exists())

    def test_chosen_date_and_bad_date(self):
        cl = self.detect(business_date="2026-07-13")
        self.assertEqual(cl["business_date"], "2026-07-13")
        self.assertEqual(self.slot(cl, "CBS_GL_EXTRACT", None)["group"], "IN_BATCH")
        self.assertEqual(self.slot(cl, "RAW_DATA_ACQ", "1C")["group"], "MISSING")  # its name says 14 Jul
        self.assertEqual(cl["outside_checklist"], [0, 1, 2, 4, 5])
        r = self.as_(O).post(self.url, {"files": [upload(RAW_ACQ_NAME, raw_csv())], "business_date": "13/07"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("business_date", r.json()["errors"])

    def test_files_ids_filter(self):
        ids = lambda **q: [r["id"] for r in self.as_(O).get(FILES, q).json()["results"]]  # noqa: E731
        self.assertEqual(ids(ids=f"{self.loaded_2c.pk}"), [self.loaded_2c.pk])
        self.assertEqual(sorted(ids(ids=f"{self.loaded_2c.pk},{self.failed_drc.pk},999999")),
                         sorted([self.loaded_2c.pk, self.failed_drc.pk]))
        self.assertEqual(self.as_(O).get(FILES, {"ids": "1,x"}).status_code, 400)


# ---------------------------------------------------------------------------
# F1 NPCI files report
# ---------------------------------------------------------------------------
class NpciFilesReportTests(NpciApiBase):
    def test_roles_rows_totals_and_downloads(self):
        self.assert_read_matrix(REPORT, JULY)
        first = self.generate(confirm_partial=True).json()["id"]
        self.approve_held()
        second = self.generate().json()["id"]
        self.as_(O).get(f"{UPI}/npci-files/{first}/download/")
        body = self.ok(U, REPORT, JULY)
        self.assertEqual([r["id"] for r in body["results"]], [first, second])  # same date: by sequence
        row = body["results"][0]
        self.assertEqual((row["business_date"], row["file_name"], row["row_count"], row["download_count"],
                          row["downloads"][0]["by"], row["download_url"]),
                         ("2026-07-14", "SSKUPI14072026.csv", 2, 1, self.users[O].username, None))  # Auditor
        self.assertEqual(row["links"], {"file": f"/upi/npci-files/{first}", "date": "/upi/dates/2026-07-14"})
        self.assertEqual(body["totals"], {"files": 2, "rows": 3, "amount": body["totals"]["amount"],
                                          "downloaded": 1, "not_downloaded": 1})
        self.assertEqual(body["report"]["key"], "npci_files")
        self.assertEqual(body["report"]["filters"]["date_from"], "2026-07-01")
        self.assertIsNone(body["export"])
        self.assertIsNotNone(self.ok(O, REPORT, JULY)["results"][0]["download_url"])
        self.assertEqual([r["id"] for r in self.ok(U, REPORT, {**JULY, "downloaded": "false"})["results"]], [second])
        self.assertEqual(self.ok(U, REPORT, {**JULY, "downloaded": "true"})["totals"]["files"], 1)

    def test_date_rules(self):
        self.generate(confirm_partial=True)
        body = self.ok(U, REPORT)  # default: this month, which has no file
        today = timezone.localdate()
        self.assertEqual((body["count"], body["report"]["filters"]["date_from"]),
                         (0, today.replace(day=1).isoformat()))
        for bad, field in (({"date_from": "2026-07-01"}, "date_to"), ({"date_to": "2026-07-01"}, "date_from"),
                           ({"date_from": "2026-07-31", "date_to": "2026-07-01"}, "date_to"),
                           ({"date_from": "2026-01-01", "date_to": "2026-07-31"}, "date_to"),
                           ({"date_from": "x", "date_to": "2026-07-01"}, "date_from"),
                           ({**JULY, "ordering": "x"}, "ordering"), ({**JULY, "downloaded": "x"}, "downloaded")):
            with self.subTest(bad=bad):
                r = self.as_(U).get(REPORT, bad)
                self.assertEqual(r.status_code, 400)
                self.assertIn(field, r.json()["errors"])

    def test_reports_capability(self):
        for role in ALL_ROLES:
            self.assertTrue(self.ok(role, f"{V1}/me/")["permissions"]["reports.view"])


# ---------------------------------------------------------------------------
# F2 dashboard
# ---------------------------------------------------------------------------
class DashboardTests(NpciApiBase):
    def raw_fact(self, d, amount, register_type=RegisterType.RAW_DATA):
        uf = UploadedFile.objects.filter(business_date=D).first()
        return TransactionFact.objects.create(
            payment_module=self.module, source_register_type=register_type, uploaded_file=uf, business_date=d,
            rrn="100000000777", rrn_valid=True, amount=Decimal(amount), direction=Direction.DEBIT,
            row_reference=f"r{TransactionFact.objects.count() + 100}")

    def test_per_date_blocks(self):
        self.assert_read_matrix(DASHBOARD)
        self.raw_fact(D, "10.50")
        self.raw_fact(D, "4.50")
        self.raw_fact(D, "7.00", RegisterType.CBS_GL_EXTRACT)
        body = self.ok(U, DASHBOARD)
        self.assertEqual(body["range"], {"date_from": "2026-07-14", "date_to": "2026-07-14", "dates": 1})
        row = body["dates"][0]
        self.assertEqual(row["volume"], {"transactions": 3, "npci": {"count": 2, "amount": "15.00"},
                                         "cbs": {"count": 1, "amount": "7.00"}})
        self.assertEqual(row["exceptions"], {"DRC": 1, "TCC": 0, "RET": 1, "RRC": 1, "NEEDS_REVIEW": 0,
                                             "SETTLEMENT": 0, "total": 3})
        self.assertEqual((row["open_cases"], row["settlement"], row["link"]), (3, None, "/upi/dates/2026-07-14"))
        self.assertEqual(row["sskupi"], {"state": "NONE", "file_count": 0, "not_downloaded": 0, "pending": False})
        ages = body["open_cases_by_age"]
        self.assertEqual(ages["total"], 3)
        self.assertEqual(sum(b["count"] for b in ages["buckets"]), 3)
        self.assertEqual([v["value"] for v in body["reply_types"]],
                         ["DRC", "TCC", "RET", "RRC", "NEEDS_REVIEW", "SETTLEMENT"])

    def test_file_pending_without_a_plan(self):
        ExceptionCase.objects.filter(business_date=D).update(status=CaseStatus.COMPLETED)
        RuleEvaluationRun.objects.filter(business_date=D).update(started_at=timezone.now() + timedelta(minutes=5))
        row = self.ok(U, DASHBOARD)["dates"][0]
        self.assertEqual((row["status"], row["sskupi"]["pending"], row["open_cases"]), ("FILE_PENDING", True, 0))

    def test_range_and_fixed_query_count(self):
        def queries(params=None):
            client = self.as_(U)
            with CaptureQueriesContext(connection) as q:
                self.assertEqual(client.get(DASHBOARD, params or {}).status_code, 200)
            return len(q)

        before = queries()
        for n in range(1, 4):
            self.drc_case(f"10000000050{n}", d=D - timedelta(days=n))
        self.assertEqual(queries(), before)
        body = self.ok(U, DASHBOARD, {"date_from": "2026-07-12", "date_to": "2026-07-13"})
        self.assertEqual([r["business_date"] for r in body["dates"]], ["2026-07-12", "2026-07-13"])
        self.assertEqual(len(self.ok(U, DASHBOARD)["dates"]), 4)
        for bad in ({"date_from": "2026-07-01"}, {"date_from": "2026-01-01", "date_to": "2026-07-31"}):
            self.assertEqual(self.as_(U).get(DASHBOARD, bad).status_code, 400)


