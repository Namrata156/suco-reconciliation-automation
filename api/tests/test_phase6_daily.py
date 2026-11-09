"""
Phase 6 Slice 6.2 (docs/phase6_api_contract.txt B0-B3, B8, section A, C1,
C3): capabilities, report meta, the reconciliation / settlement / volume
reports, the shared CSV / Excel download and REPORT_EXPORTED. Every RRN,
amount and file here is made up.
"""
from __future__ import annotations

import sys
import types
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.utils import timezone

from api import export
from api.tests.test_slice2 import Slice2Base
from api.tests.test_v1 import ALL_ROLES, V1
from core.models import Direction, OperationLog, OperationType
from core.operations import log_operation
from exception_management.roles import Role
from reconciliation.models import ProcessingStatus as GroupStatus
from reconciliation.models import RegisterPresence, TransactionMatchGroup
from rule_engine.models import RuleEvaluationRun
from settlement.models import ResultStatus, SettlementCycleResult, SettlementResult, Side
from transactions.models import DailyVolume, TransactionFact
from transactions.models.staging import STAGING_MODEL_BY_REGISTER_TYPE
from transactions.services.volume import refresh_daily_volume

A, O, M, C, U = Role.ADMINISTRATOR, Role.OPERATIONS_USER, Role.MAKER, Role.CHECKER, Role.AUDITOR
D = date(2026, 7, 14)
D_AUG = date(2026, 8, 3)
UPI = f"{V1}/upi"
RECON, SETTLE, VOLUME = f"{UPI}/reports/reconciliation/", f"{UPI}/reports/settlement/", f"{UPI}/reports/volume/"
JUL_AUG = {"date_from": "2026-07-01", "date_to": "2026-08-31"}
JULY = {"date_from": "2026-07-01", "date_to": "2026-07-31"}


def body_of(response) -> bytes:
    return b"".join(response.streaming_content) if response.streaming else response.content


def csv_lines(response) -> list[str]:
    raw = body_of(response)
    assert raw.startswith(b"\xef\xbb\xbf"), raw[:10]
    return raw.decode("utf-8-sig").split("\r\n")


class Phase6Base(Slice2Base):
    def setUp(self):
        super().setUp()
        self._n = 0

    def ok(self, who, url, params=None):
        r = self.as_(who).get(url, params or {})
        self.assertEqual(r.status_code, 200, r.content[:500])
        return r.json()

    def bad(self, who, url, params, field):
        r = self.as_(who).get(url, params)
        self.assertEqual(r.status_code, 400, r.content[:300])
        self.assertIn(field, r.json()["errors"])
        return r.json()

    def fact(self, key, amount, *, d=D, direction=Direction.DEBIT, cycle="1C", code=None, uf=None, rrn=None):
        """A current fact of file type `key` (Slice2Base.cfg), with its staging
        row when `code` (NPCI response code, cell 5) is given."""
        self._n += 1
        cfg = self.cfg[key]
        if uf is None:
            uf = self.make_file(key, cycle if cfg.is_cycle_wise else None, d=d, business_date=d)
        staging_id = None
        if code is not None:
            staging_id = STAGING_MODEL_BY_REGISTER_TYPE[cfg.register_type].objects.create(
                uploaded_file=uf, row_number=self._n, row_reference=f":{self._n}", raw_cells={"5": code}).pk
        return TransactionFact.objects.create(
            payment_module=self.module, source_register_type=cfg.register_type, uploaded_file=uf, business_date=d,
            rrn=rrn or f"9{self._n:011d}", rrn_valid=True, amount=Decimal(str(amount)), direction=direction,
            row_reference=str(self._n), settlement_cycle=uf.settlement_cycle, staging_row_id=staging_id)

    def settlement(self, d, status=ResultStatus.TALLIED, unexplained="0", fees="12.50", **kw):
        return SettlementResult.objects.create(
            payment_module=self.module, business_date=d, status=status, computed_at=timezone.now(),
            unexplained_amount=Decimal(unexplained), fees_net=Decimal(fees), **kw)

    def exports(self):
        return OperationLog.objects.filter(operation=OperationType.REPORT_EXPORTED).order_by("id")


# ---------------------------------------------------------------------------
# C1 capabilities, B0 meta
# ---------------------------------------------------------------------------
class CapabilityAndMetaTests(Phase6Base):
    def test_capabilities(self):
        expected = {A: (True, True, True), O: (True, False, False), M: (True, False, False),
                    C: (True, True, False), U: (True, True, True)}
        for role in ALL_ROLES:
            perms = self.ok(role, f"{V1}/me/")["permissions"]
            self.assertEqual((perms["reports.view"], perms["reports.approvals"], perms["reports.user_activity"]),
                             expected[role], role)

    def test_meta(self):
        self.assert_read_matrix(f"{V1}/reports/meta/")
        body = self.ok(O, f"{V1}/reports/meta/")
        self.assertEqual((body["export_formats"], body["max_export_rows"], body["max_range_days"],
                          body["max_range_months"]), (["csv"], 100000, 92, 24))
        self.assertEqual([r["key"] for r in body["reports"]],
                         ["reconciliation", "settlement", "npci_files", "volume", "exceptions", "ageing",
                          "approvals", "user_activity"])
        ua = body["reports"][-1]
        self.assertEqual((ua["module_scoped"], ua["capability"]), (False, "reports.user_activity"))
        with mock.patch("api.export.xlsx_available", return_value=True):
            self.assertEqual(self.ok(O, f"{V1}/reports/meta/")["export_formats"], ["xlsx", "csv"])


# ---------------------------------------------------------------------------
# B1 reconciliation report
# ---------------------------------------------------------------------------
class ReconciliationReportTests(Phase6Base):
    def setUp(self):
        super().setUp()
        self.make_file("CBS_GL_EXTRACT", None, d=D, business_date=D)
        self.make_file("CBS_GL_EXTRACT", None, d=D_AUG, business_date=D_AUG)
        for rrn, rule in (("100000000001", "R01"), ("100000000002", "R01"), ("100000000003", "R13")):
            self.make_case(rrn, rule, D)
        TransactionMatchGroup.objects.filter(rrn="100000000001").update(
            processing_status=GroupStatus.MATCHED, is_on_us=True)
        TransactionMatchGroup.objects.filter(rrn="100000000003").update(cbs_reversed=True)
        RuleEvaluationRun.objects.filter(business_date=D).update(rule_counts={"R01": 2, "R13": 1, "R10": 5})
        self.make_case("100000000004", "R01", D_AUG)
        RuleEvaluationRun.objects.filter(business_date=D_AUG).update(rule_counts={"R01": 1})
        self.settlement(D, unexplained="0")

    def test_rows_totals_and_months(self):
        self.assert_read_matrix(RECON, JUL_AUG)
        body = self.ok(M, RECON, JUL_AUG)
        self.assertEqual([r["business_date"] for r in body["results"]], ["2026-08-03", "2026-07-14"])
        row = body["results"][1]
        self.assertEqual(row["match_groups"], {"total": 3, "on_us": 1, "cbs_reversed": 1, "by_status": {
            "MATCHED": 1, "UNMATCHED": 0, "PENDING_RULE_EVALUATION": 2}})
        self.assertEqual(row["rule_counts"], {"R01": 2, "R13": 1, "R10": 5})
        cats = row["results_by_category"]
        self.assertEqual(sum(cats.values()), 8)
        self.assertEqual(row["cases"], {"created": 3, "open": 3})
        self.assertEqual(row["settlement"], {"status": "TALLIED", "unexplained_amount": "0.00"})
        self.assertEqual(row["rule_evaluation"]["rule_set"], self.rule_set.version_label)
        self.assertEqual(row["files"]["received"], 1)
        self.assertEqual(row["links"], {"results": "/upi/dates/2026-07-14/results",
                                        "settlement": "/upi/dates/2026-07-14/settlement",
                                        "date": "/upi/dates/2026-07-14"})
        self.assertIsNone(body["results"][0]["links"]["settlement"])
        t = body["totals"]
        self.assertEqual((t["dates"], t["match_groups"]["total"], t["rule_counts"]["R01"], t["cases"]["open"]),
                         (2, 4, 3, 4))
        self.assertEqual(t["settlement"]["by_status"]["TALLIED"], 1)
        self.assertEqual(t["settlement"]["by_status"]["NONE"], 1)
        self.assertEqual([m["month"] for m in body["months"]], ["2026-08", "2026-07"])
        self.assertEqual(body["months"][1]["rule_counts"], {"R01": 2, "R13": 1, "R10": 5})
        self.assertEqual(body["rule_codes"][:2], ["R01", "R10"] if body["rule_codes"].index("R10") < 2
                         else body["rule_codes"][:2])
        self.assertEqual(set(body["rule_codes"]), {"R01", "R13", "R10"})
        rep = body["report"]
        self.assertEqual((rep["key"], rep["module"], rep["filters"]["date_from"], rep["versions"]["rule_sets"]),
                         ("reconciliation", "upi", "2026-07-01", [self.rule_set.version_label]))
        self.assertEqual(body["export"], {"formats": ["csv"], "row_count": 5, "limit": 100000, "allowed": True,
                                          "sheets": [{"key": "dates", "label": "Dates", "row_count": 5},
                                                     {"key": "rules", "label": "Rules by date", "row_count": 3}]})
        self.assertEqual(self.ok(M, RECON, JULY)["months"], [])

    def test_filters_paging_and_errors(self):
        body = self.ok(U, RECON, {**JUL_AUG, "ordering": "business_date", "page_size": 1})
        self.assertEqual((body["count"], body["results"][0]["business_date"]), (2, "2026-07-14"))
        from exception_management.models import ExceptionCase, CaseStatus
        ExceptionCase.objects.filter(business_date=D_AUG).update(status=CaseStatus.COMPLETED)
        only = self.ok(U, RECON, {**JUL_AUG, "open_cases_only": "true"})
        self.assertEqual([r["business_date"] for r in only["results"]], ["2026-07-14"])
        self.assertEqual(only["totals"]["dates"], 1)
        default = self.ok(U, RECON)  # this month: nothing loaded
        self.assertEqual((default["count"], default["report"]["filters"]["date_from"]),
                         (0, timezone.localdate().replace(day=1).isoformat()))
        for params, field in (({"date_from": "2026-07-01"}, "date_to"), ({"date_to": "2026-07-01"}, "date_from"),
                              ({"date_from": "2026-07-31", "date_to": "2026-07-01"}, "date_to"),
                              ({"date_from": "2026-01-01", "date_to": "2026-07-31"}, "date_to"),
                              ({**JULY, "ordering": "rrn"}, "ordering"),
                              ({**JULY, "open_cases_only": "yes"}, "open_cases_only")):
            with self.subTest(params=params):
                self.bad(U, RECON, params, field)
        from api.reports import ReportDatePagination
        self.assertEqual((ReportDatePagination.page_size, ReportDatePagination.max_page_size), (50, 92))

    def test_csv_download_and_log(self):
        r = self.as_(O).get(RECON, {**JUL_AUG, "export": "csv"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(r["Content-Disposition"],
                         'attachment; filename="UPI_reconciliation_2026-07-01_to_2026-08-31_dates.csv"')
        lines = csv_lines(r)
        self.assertEqual(lines[0], "Bank,SUCO Souharda Sahakari Bank")
        self.assertEqual(lines[1:3], ["Module,UPI", "Report,Daily reconciliation report"])
        self.assertTrue(lines[3].startswith("Filters,date_from: 2026-07-01; date_to: 2026-08-31"))
        self.assertEqual(lines[9], "")
        self.assertTrue(lines[10].startswith("Business date,Files expected"))
        self.assertTrue(lines[11].startswith("2026-08-03,"))
        self.assertTrue(lines[13].startswith("Total 2026-08,"))
        self.assertTrue(lines[15].startswith("Total,"))
        log = self.exports().get()
        self.assertEqual((log.username, log.payment_module, log.business_date), (self.ops.username, self.module, None))
        self.assertEqual(log.details, {"report": "reconciliation", "format": "csv", "sheet": "dates",
                                       "filters": {"date_from": "2026-07-01", "date_to": "2026-08-31",
                                                   "open_cases_only": False, "ordering": "-business_date"},
                                       "row_count": 5,
                                       "file_name": "UPI_reconciliation_2026-07-01_to_2026-08-31_dates.csv"})
        rules = csv_lines(self.as_(O).get(RECON, {**JUL_AUG, "export": "csv", "sheet": "rules"}))
        self.assertTrue(rules[10].startswith("Business date,R01"))
        self.assertEqual(rules[-2].split(",")[0], "Total")

    def test_refused_downloads_are_not_logged(self):
        for params, field, message in (
                ({"export": "pdf"}, "export", "Unknown export format."),
                ({"export": "xlsx"}, "export", "Excel download is not available on this server; use CSV."),
                ({"export": "csv", "sheet": "x"}, "sheet", "Unknown sheet; allowed: dates, rules")):
            with self.subTest(params=params):
                body = self.bad(O, RECON, {**JUL_AUG, **params}, field)
                self.assertEqual(body["errors"][field], [message])
        with mock.patch.object(export, "EXPORT_LIMIT", 2):
            body = self.bad(O, RECON, {**JUL_AUG, "export": "csv"}, "export")
            self.assertEqual((body["row_count"], body["limit"]), (5, 2))
            self.assertEqual(body["errors"]["export"],
                             ["5 rows is more than the 2 a download may hold; narrow the filters."])
            self.assertFalse(self.ok(O, RECON, JUL_AUG)["export"]["allowed"])
        self.assertFalse(self.exports().exists())


# ---------------------------------------------------------------------------
# section A: cell rules, the Excel path (with a stand-in openpyxl)
# ---------------------------------------------------------------------------
class _Cell:
    def __init__(self, ws, value=None):
        self.value, self.number_format, self.data_type = value, "General", "n"


class _Sheet:
    def __init__(self, title):
        self.title, self.rows = title, []

    def append(self, row):
        self.rows.append(list(row))


class _Workbook:
    last = None

    def __init__(self, write_only=False):
        assert write_only
        self.sheets = []
        _Workbook.last = self

    def create_sheet(self, title):
        self.sheets.append(_Sheet(title))
        return self.sheets[-1]

    def save(self, f):
        f.write(b"PK-stub")


class ExportFormatTests(Phase6Base):
    def test_cells(self):
        self.assertEqual(export.safe_text("=SUM(A1)"), "'=SUM(A1)")
        for start in ("+1", "-x", "@a", "\tb", "\rc"):
            self.assertEqual(export.safe_text(start), "'" + start)
        self.assertEqual(export.safe_text("ok -1"), "ok -1")
        self.assertEqual(export._csv_cell(export.AMOUNT, Decimal("-35")), "-35.00")
        self.assertEqual(export._csv_cell(export.AMOUNT, "1070.5"), "1070.50")
        self.assertEqual(export._csv_cell(export.RRN, "000123456789"), '="000123456789"')
        self.assertEqual(export._csv_cell(export.TEXT, "-35.00"), "'-35.00")
        self.assertEqual(export._csv_cell(export.BOOL, False), "No")
        self.assertEqual(export._csv_cell(export.DATE, D), "2026-07-14")
        self.assertEqual(export._csv_cell(export.TEXT, None), "")

    def test_xlsx_when_openpyxl_is_installed(self):
        self.make_file("CBS_GL_EXTRACT", None, d=D, business_date=D)
        self.make_case("000000000123", "R01", D)
        cell_mod = types.ModuleType("openpyxl.cell")
        cell_mod.WriteOnlyCell = _Cell
        root = types.ModuleType("openpyxl")
        root.Workbook = _Workbook
        with mock.patch.dict(sys.modules, {"openpyxl": root, "openpyxl.cell": cell_mod}), \
                mock.patch("api.export.xlsx_available", return_value=True):
            r = self.as_(U).get(f"{UPI}/dates/2026-07-14/results/", {"export": "xlsx"})
            raw = body_of(r)
            self.assertEqual((r.status_code, raw), (200, b"PK-stub"))
            self.assertEqual(r["Content-Type"], export.XLSX_CONTENT_TYPE)
            self.assertEqual(r["Content-Disposition"], 'attachment; filename="UPI_results_2026-07-14.xlsx"')
            ws = _Workbook.last.sheets[0]
            self.assertEqual(ws.title, "Results")
            self.assertEqual(ws.rows[0][1].value, "SUCO Souharda Sahakari Bank")
            self.assertEqual(ws.rows[9], [])
            header = [c.value for c in ws.rows[10]]
            data = ws.rows[11]
            rrn = data[header.index("RRN")]
            self.assertEqual((rrn.value, rrn.data_type), ("000000000123", "s"))
            self.assertEqual(data[header.index("Business date")].number_format, "dd-mmm-yyyy")
            self.assertEqual(self.exports().get().details["format"], "xlsx")
            self.assertIsNone(self.exports().get().details["sheet"])


# ---------------------------------------------------------------------------
# B2 settlement report
# ---------------------------------------------------------------------------
class SettlementReportTests(Phase6Base):
    def setUp(self):
        super().setUp()
        for d in (D, D + timedelta(days=1), D + timedelta(days=2)):
            self.make_file("CBS_GL_EXTRACT", None, d=d, business_date=d)
        self.tallied = self.settlement(D, sop_cbs_net=Decimal("100"), sop_ntsl_net=Decimal("90"),
                                       sop_difference=Decimal("10"), sop_explained=Decimal("10"),
                                       sop_unexplained=Decimal("0"))
        self.unexplained = self.settlement(D + timedelta(days=1), ResultStatus.UNEXPLAINED, unexplained="-35.00",
                                           fees="-1.00", reasons=["Cycle 1C remitter differs"])
        SettlementCycleResult.objects.create(result=self.tallied, settlement_cycle=self.cycle("1C"),
                                             side=Side.REMITTER, ntsl_amount=Decimal("90"), ntsl_count=3,
                                             matched_amount=Decimal("90"), within_tolerance=True)
        SettlementCycleResult.objects.create(result=self.unexplained, settlement_cycle=None, side=Side.BENEFICIARY,
                                             ntsl_amount=Decimal("5"), unexplained_amount=Decimal("-35"),
                                             within_tolerance=False)

    def test_rows_filters_totals(self):
        self.assert_read_matrix(SETTLE, JULY)
        body = self.ok(C, SETTLE, JULY)
        self.assertEqual([(r["business_date"], r["status"]) for r in body["results"]],
                         [("2026-07-16", None), ("2026-07-15", "UNEXPLAINED"), ("2026-07-14", "TALLIED")])
        none, unexp, tallied = body["results"]
        self.assertEqual((none["sop"], none["reasons"], none["links"]["settlement"]), (None, [], None))
        self.assertEqual(tallied["sop"], {"cbs_net": "100.00", "ntsl_net": "90.00", "difference": "10.00",
                                          "explained": "10.00", "unexplained": "0.00"})
        self.assertEqual((unexp["unexplained_amount"], unexp["reasons"], unexp["status_label"]),
                         ("-35.00", ["Cycle 1C remitter differs"], "Unexplained difference"))
        self.assertEqual(tallied["self_check"], {"cycles_pass": 0, "cycles_total": 0})
        self.assertEqual(body["totals"], {"by_status": {"TALLIED": 1, "UNEXPLAINED": 1, "NOT_READY": 0, "NONE": 1},
                                          "unexplained_amount": "-35.00", "fees_net": "11.50"})
        self.assertEqual([s["row_count"] for s in body["export"]["sheets"]], [4, 2])
        only = self.ok(C, SETTLE, {**JULY, "status": "NONE,TALLIED", "ordering": "business_date"})
        self.assertEqual([r["business_date"] for r in only["results"]], ["2026-07-14", "2026-07-16"])
        self.assertEqual(only["report"]["filters"]["status"], ["NONE", "TALLIED"])
        self.bad(C, SETTLE, {**JULY, "status": "DONE"}, "status")

    def test_cycles_sheet(self):
        r = self.as_(M).get(SETTLE, {**JULY, "export": "csv", "sheet": "cycles"})
        self.assertEqual(r["Content-Disposition"],
                         'attachment; filename="UPI_settlement_2026-07-01_to_2026-07-31_cycles.csv"')
        lines = csv_lines(r)
        self.assertEqual(lines[10], "Business date,Cycle,Side,NTSL amount,NTSL count,Matched amount,"
                                    "Explained amount,Unexplained amount,Within tolerance")
        self.assertEqual(lines[11], "2026-07-14,1C,Remitter (SSK pays),90.00,3,90.00,0.00,0.00,Yes")
        self.assertEqual(lines[12], "2026-07-15,(no cycle),Beneficiary (SSK receives),5.00,,0.00,0.00,-35.00,No")


# ---------------------------------------------------------------------------
# B3 volume report and its summary table (R4a)
# ---------------------------------------------------------------------------
class VolumeTests(Phase6Base):
    def setUp(self):
        super().setUp()
        acq_1c = self.make_file("RAW_DATA_ACQ", "1C", d=D, business_date=D)
        for code in ("00", "RB", "U3", "'00"):
            self.fact("RAW_DATA_ACQ", "100", uf=acq_1c, code=code)
        self.fact("RAW_DATA_ACQ", "7", cycle="2C", code="ZM")
        self.fact("RAW_DATA_ISS", "50", direction=Direction.CREDIT, code="00")
        self.fact("CBS_GL_EXTRACT", "20", direction=Direction.DEBIT)
        self.fact("CBS_GL_EXTRACT", "30", direction=Direction.CREDIT)
        self.fact("CBS_GL_EXTRACT", "1", direction=Direction.UNKNOWN)
        self.fact("DRC", "40")
        self.fact("CBS_GL_EXTRACT", "5", d=D_AUG)
        old = self.make_file("CBS_GL_EXTRACT", None, d=D, business_date=D, is_current=False)
        self.fact("CBS_GL_EXTRACT", "999", uf=old)  # a superseded file: never counted

    def summary(self, d=D):
        return {(v.file_type_code, v.side, v.cycle_code): (v.row_count, v.amount, v.approved_count,
                                                           v.declined_count)
                for v in DailyVolume.objects.filter(business_date=d)}

    def test_refresh(self):
        r = refresh_daily_volume(self.module, D, source="test")
        self.assertEqual((r.facts, r.by_file_type["RAW_DATA"]), (10, 6))
        self.assertEqual(self.summary(), {
            ("RAW_DATA", "ACQUIRER", "1C"): (4, Decimal("400.00"), 3, 1),
            ("RAW_DATA", "ACQUIRER", "2C"): (1, Decimal("7.00"), 0, 1),
            ("RAW_DATA", "ISSUER", "1C"): (1, Decimal("50.00"), 1, 0),
            ("CBS_GL_EXTRACT", "DEBIT", ""): (1, Decimal("20.00"), None, None),
            ("CBS_GL_EXTRACT", "CREDIT", ""): (1, Decimal("30.00"), None, None),
            ("CBS_GL_EXTRACT", "UNKNOWN", ""): (1, Decimal("1.00"), None, None),
            ("DRC", "ALL", ""): (1, Decimal("40.00"), None, None),
        })
        # A refresh replaces the date's rows (a corrected file, a new run).
        TransactionFact.objects.filter(source_register_type="DRC").delete()
        refresh_daily_volume(self.module, D)
        self.assertNotIn(("DRC", "ALL", ""), self.summary())

    def test_response_code_from_archived_staging_row(self):
        from transactions.models.history import HISTORY_MODEL_BY_STAGING_MODEL
        fact = self.fact("RAW_DATA_ISS", "10", d=D_AUG, code="U3")
        live = STAGING_MODEL_BY_REGISTER_TYPE["RAW_DATA"].objects.get(pk=fact.staging_row_id)
        HISTORY_MODEL_BY_STAGING_MODEL[type(live)].objects.create(
            original_id=live.pk, uploaded_file=live.uploaded_file, row_number=live.row_number,
            row_reference=live.row_reference, raw_cells={"5": "00"}, created_at=live.created_at)
        live.delete()
        refresh_daily_volume(self.module, D_AUG)
        self.assertEqual(self.summary(D_AUG)[("RAW_DATA", "ISSUER", "1C")][2:], (1, 0))

    def test_report(self):
        refresh_daily_volume(self.module, D)
        self.assert_read_matrix(VOLUME, JULY)
        body = self.ok(O, VOLUME, JULY)
        self.assertEqual([(r["period"], r["file_type"], r["cycle"]) for r in body["results"]],
                         [("2026-07-14", "CBS_GL_EXTRACT", None), ("2026-07-14", "RAW_DATA", None),
                          ("2026-07-14", "DRC", None)])
        raw = body["results"][1]
        self.assertEqual(raw["file_type_label"], "Raw Data")
        self.assertEqual(raw["sides"], [{"side": "ACQUIRER", "side_label": "Acquirer", "count": 5, "amount": "407.00"},
                                        {"side": "ISSUER", "side_label": "Issuer", "count": 1, "amount": "50.00"}])
        self.assertEqual((raw["total_count"], raw["total_amount"], raw["npci"]),
                         (6, "457.00", {"approved": 4, "declined": 2}))
        self.assertIsNone(body["results"][0]["npci"])
        self.assertEqual([s["side"] for s in body["results"][0]["sides"]], ["DEBIT", "CREDIT", "UNKNOWN"])
        self.assertEqual([t["file_type"] for t in body["totals"]], ["CBS_GL_EXTRACT", "RAW_DATA", "DRC"])
        self.assertEqual(body["missing_dates"], [])
        cyc = self.ok(O, VOLUME, {**JULY, "by_cycle": "true", "file_type": "RAW_DATA"})
        self.assertEqual([(r["cycle"], r["total_count"]) for r in cyc["results"]], [("1C", 5), ("2C", 1)])

    def test_months_gaps_and_errors(self):
        refresh_daily_volume(self.module, D)
        body = self.ok(O, VOLUME, {"date_from": "2026-07-20", "date_to": "2026-08-05", "group_by": "month"})
        self.assertEqual(body["report"]["filters"]["date_from"], "2026-07-01")
        self.assertEqual(body["report"]["filters"]["date_to"], "2026-08-31")
        self.assertEqual({r["period"] for r in body["results"]}, {"2026-07"})
        self.assertEqual(body["missing_dates"], ["2026-08-03"])  # files, never summarised
        self.ok(O, VOLUME, {"date_from": "2025-01-01", "date_to": "2026-12-31", "group_by": "month"})
        self.bad(O, VOLUME, {"date_from": "2025-01-01", "date_to": "2027-01-01", "group_by": "month"}, "date_to")
        self.bad(O, VOLUME, {"date_from": "2026-01-01", "date_to": "2026-07-31"}, "date_to")
        self.bad(O, VOLUME, {**JULY, "group_by": "week"}, "group_by")
        self.bad(O, VOLUME, {**JULY, "file_type": "RAW_DATA,XYZ"}, "file_type")
        self.bad(O, VOLUME, {**JULY, "ordering": "x"}, "ordering")
        lines = csv_lines(self.as_(O).get(VOLUME, {**JULY, "export": "csv"}))
        self.assertIn("Acquirer count,Acquirer amount,Issuer count,Issuer amount", lines[10])
        self.assertTrue(lines[-2].startswith("Total,Debit Reversal Confirmation Report"))

    def test_run_fills_the_summary_and_never_fails_it(self):
        from reconciliation.models import ReconciliationRun, RunStatus
        from reconciliation.runs import execute_run

        def run():
            r = ReconciliationRun.objects.create(payment_module=self.module, business_date=D, status=RunStatus.QUEUED,
                                                 requested_by_username="ops")
            return execute_run(r.pk)

        with mock.patch("reconciliation.runs._presence", return_value={}), \
                mock.patch("reconciliation.runs._rules", return_value={}), \
                mock.patch("reconciliation.runs._settlement", return_value={}):
            self.assertEqual(run().status, RunStatus.SUCCEEDED)
            self.assertEqual(self.summary()[("DRC", "ALL", "")][0], 1)
            DailyVolume.objects.all().delete()
            with mock.patch("transactions.services.volume.refresh_daily_volume", side_effect=RuntimeError("x")), \
                    self.assertLogs("reconciliation.runs", "ERROR"):
                self.assertEqual(run().status, RunStatus.SUCCEEDED)
            self.assertFalse(DailyVolume.objects.exists())
        with mock.patch("reconciliation.runs._presence", side_effect=RuntimeError("boom")), \
                self.assertLogs("reconciliation.runs", "ERROR"):
            self.assertEqual(run().status, RunStatus.FAILED)
        self.assertTrue(DailyVolume.objects.filter(business_date=D).exists())  # filled on failure too

    def test_fill_command(self):
        out = StringIO()
        call_command("fill_daily_volume", stdout=out)
        self.assertIn("Filled 2 business date(s).", out.getvalue())
        self.assertTrue(DailyVolume.objects.filter(business_date=D_AUG).exists())
        out = StringIO()
        call_command("fill_daily_volume", "--missing-only", stdout=out)
        self.assertIn("No business date to fill.", out.getvalue())
        out = StringIO()
        call_command("fill_daily_volume", "--date", "2026-07-14", stdout=out)
        self.assertIn("Filled 1 business date(s).", out.getvalue())


# ---------------------------------------------------------------------------
# B8 one date's results as a file
# ---------------------------------------------------------------------------
class ResultsExportTests(Phase6Base):
    def setUp(self):
        super().setUp()
        self.make_file("CBS_GL_EXTRACT", None, d=D, business_date=D)
        self.r01 = self.make_case("000000000777", "R01", D)
        self.make_case("100000000002", "R13", D)
        RegisterPresence.objects.create(match_group=self.r01.match_group, leg="NPCI", present=True,
                                        amount=Decimal("-35.00"), direction="DEBIT", response_code="00")

    def test_rows_follow_the_screen_filters(self):
        url = f"{UPI}/dates/2026-07-14/results/"
        for role in ALL_ROLES:
            self.assertEqual(self.as_(role).get(url, {"export": "csv"}).status_code, 200, role)
        r = self.as_(U).get(url, {"export": "csv", "rule_code": "R01"})
        self.assertEqual(r["Content-Disposition"], 'attachment; filename="UPI_results_2026-07-14.csv"')
        lines = csv_lines(r)
        self.assertNotIn("riority", lines[10])  # owner 2026-11-08 "P"
        self.assertEqual(len(lines), 13)  # header block, blank, header, 1 row, final ""
        cols = lines[10].split(",")
        row = dict(zip(cols, lines[11].split(",")))
        self.assertEqual((row["RRN"], row["NPCI amount"], row["NPCI response code"], row["Rule"], row["Case"]),
                         ('"=""000000000777"""', "-35.00", '"=""00"""', "R01", str(self.r01.pk)))
        self.assertEqual(self.exports().last().details["filters"],
                         {"business_date": "2026-07-14", "rule_code": "R01", "superseded": "false"})
        self.assertEqual(self.exports().last().details["row_count"], 1)
        self.bad(U, url, {"export": "csv", "presence": "x"}, "presence")
        self.assertEqual(self.as_(U).get(f"{UPI}/dates/2026-07-13/results/", {"export": "csv"}).status_code, 404)
        self.assertEqual(self.as_(U).get(url).status_code, 200)  # the screen is unchanged

    def test_download_refuses_priority(self):
        """Cleanup 2026-11-09: no priority in any report (owner 2026-11-08 "P"),
        so the download takes no priority filter or ordering; the screen keeps them."""
        url = f"{UPI}/dates/2026-07-14/results/"
        self.bad(U, url, {"export": "csv", "priority": "HIGH"}, "priority")
        self.bad(U, url, {"export": "csv", "ordering": "rule_code,-priority"}, "ordering")
        self.assertFalse(self.exports().exists())
        self.assertEqual(self.as_(U).get(url, {"priority": "LOW"}).status_code, 200)
        self.assertEqual(self.as_(U).get(url, {"ordering": "priority"}).status_code, 200)
        self.assertEqual(self.as_(U).get(url, {"export": "csv", "ordering": "rule_code"}).status_code, 200)


# ---------------------------------------------------------------------------
# C3 REPORT_EXPORTED in the audit screens (incl. a download with no module)
# ---------------------------------------------------------------------------
class AuditTests(Phase6Base):
    def test_listed_and_summarised(self):
        types_ = [t["value"] for t in self.ok(U, f"{V1}/audit/meta/")["operation_types"]]
        self.assertIn("REPORT_EXPORTED", types_)
        log_operation(OperationType.REPORT_EXPORTED, user=self.users[U], payment_module=None, source="api",
                      details={"report": "user_activity", "row_count": 7, "file_name": "user_activity_x.csv"})
        log_operation(OperationType.REPORT_EXPORTED, user=self.users[U], payment_module=self.module, source="api",
                      details={"report": "volume", "row_count": 3, "file_name": "UPI_volume.csv"})
        rows = self.ok(U, f"{V1}/audit/operations/", {"event_type": "REPORT_EXPORTED"})["results"]
        self.assertEqual([(r["module"], r["link"]) for r in rows], [("upi", None), (None, None)])
        only_upi = self.ok(U, f"{V1}/audit/operations/", {"event_type": "REPORT_EXPORTED", "module": "upi"})
        self.assertEqual(only_upi["count"], 1)
        everything = self.ok(U, f"{V1}/audit/everything/", {"event_type": "REPORT_EXPORTED"})["results"]
        self.assertEqual(everything[1]["summary"],
                         f"{self.users[U].username} downloaded the report user_activity_x.csv (7 rows)")
        self.assertIsNone(everything[1]["module"])
