"""
/api/v1/ Phase 5 Slice 2 (design docs/phase5_design_draft_v1.txt 4.8-4.11,
P5, P6, P10b, Amendment 1): upload detection, upload, files list / detail,
CBS date selection, business-date overview and the run. For each endpoint:
the five roles allowed and refused, 401 anonymous, CSRF on writes, filters,
error codes and the core.OperationLog audit row.

Every file here is a tiny synthetic fixture built in the test (made-up RRNs
and amounts); nothing is read from sample_decrypted_files/.
"""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import zipfile
from datetime import date
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, override_settings

from api.tests.test_v1 import ALL_ROLES, V1, V1TestBase, csrf_client
from core.models import OperationLog, OperationType, SettlementCycle
from exception_management.roles import Role
from file_processing import intake
from file_processing.models import (
    FileTypeConfig,
    FileValidationException,
    ProcessingStatus,
    UploadedFile,
    ValidationSeverity,
)
from reconciliation.models import ReconciliationRun, RunStatus, TransactionMatchGroup
from reconciliation.runs import execute_run
from rule_engine.models import RuleEvaluationRun

D = date(2026, 7, 14)
FILES = f"{V1}/upi/files/"
DATES = f"{V1}/upi/dates/"
OPS = Role.OPERATIONS_USER

# --- synthetic fixtures ------------------------------------------------------
RAW_ACQ_NAME = "UPIRAWDATAACQSSK140726_1C.csv"
DRC_NAME = "Debit Reversal Confirmation Report__SSK_2026-07-14.csv"
NTSL_NAME = "UPI_NTSLSSK140726_DC1.xls"
CBS_NAME = "b5rg090oa.xls"  # CBS has no fixed name: recognised by content


def raw_csv(tag: str = "a") -> bytes:
    return (f"HT,SYNTHETIC,{tag}\n"
            "TX,X,X,900000000001,00,X,X,10000\n"
            "FT,1\n").encode()


def drc_csv(tag: str = "a") -> bytes:
    header = ",".join(f"H{i}" for i in range(1, 14))
    rows = []
    for n, rrn in enumerate(("900000000101", "900000000102"), start=1):
        cells = [""] * 13
        cells[2] = "14/07/2026"  # col 3 TXN Date
        cells[5] = "102"  # col 6 response code
        cells[6] = "'" + rrn  # col 7 RRN
        cells[8] = "SSK"  # col 9 Remitter
        cells[9] = "OTHERBANK"  # col 10 Beneficiary
        cells[12] = f"{n}0.00"  # col 13 amount
        cells[0] = tag
        rows.append(",".join(cells))
    return (header + "\n" + "\n".join(rows) + "\n").encode()


def cbs_xml(sheet: str = "Report") -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<?mso-application progid="Excel.Sheet"?>\n'
            '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
            'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
            f'<Worksheet ss:Name="{sheet}"><Table><Row><Cell><Data ss:Type="String">Synthetic</Data></Cell></Row>'
            '</Table></Worksheet></Workbook>').encode()


def ntsl_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", "<workbook/>")
    return buf.getvalue()


def upload(name: str, content: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type="application/octet-stream")


class Slice2Base(V1TestBase):
    def setUp(self):
        super().setUp()
        call_command("seed_settlement_cycles", stdout=StringIO())
        call_command("seed_file_type_configs", stdout=StringIO())
        self.work_dir = tempfile.mkdtemp(prefix="slice2-")
        self.addCleanup(shutil.rmtree, self.work_dir, ignore_errors=True)
        override = override_settings(FILE_PROCESSING_WORK_DIR=self.work_dir)
        override.enable()
        self.addCleanup(override.disable)
        self.cfg = {}
        for c in FileTypeConfig.objects.filter(payment_module=self.module):
            key = c.file_type_code + {"ACQ": "_ACQ", "ISS": "_ISS"}.get(intake.config_label(c).split()[-1], "")
            self.cfg[key] = c
        self.ops = self.users[OPS]
        # Processing is queued after commit; tests check it, never run Celery.
        patcher = mock.patch("file_processing.intake.build_processing_chain")
        self.chain_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def cycle(self, code):
        return SettlementCycle.objects.get(payment_module=self.module, cycle_code=code)

    def post_upload(self, client, files, items, business_date=D.isoformat(), **extra):
        data = {"business_date": business_date, "files": files, "items": json.dumps(items)}
        return client.post(FILES, data, **extra)

    def do_upload(self, *pairs, user=None, business_date=D.isoformat()):
        """pairs: (name, content, config key, cycle)."""
        files = [upload(n, c) for n, c, _, _ in pairs]
        items = [{"file_type_config_id": self.cfg[k].pk, "cycle": cy} for _, _, k, cy in pairs]
        with self.captureOnCommitCallbacks(execute=True):
            return self.post_upload(self.as_(user or self.ops), files, items, business_date)

    def make_file(self, key="RAW_DATA_ACQ", cycle="1C", status=ProcessingStatus.LOADED, d=D, n=[0], **kw):
        n[0] += 1
        cfg = self.cfg[key]
        exact = cfg.date_validation_mode == "EXACT_MATCH"
        return UploadedFile.objects.create(
            filename=kw.pop("filename", f"synthetic-{n[0]}.csv"), path="/nowhere", payment_module=self.module,
            file_type_config=cfg, business_date=kw.pop("business_date", d if exact else None),
            batch_business_date=d, settlement_cycle=self.cycle(cycle) if cycle else None, size_bytes=1,
            sha256_hash=kw.pop("sha256_hash", f"synthetic-{n[0]}"), processing_status=status, uploaded_by=self.ops, **kw)


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------
class DetectTests(Slice2Base):
    url = f"{FILES}detect/"

    def files(self):
        return [upload(RAW_ACQ_NAME, raw_csv()), upload(DRC_NAME, drc_csv()), upload(NTSL_NAME, ntsl_zip()),
                upload(CBS_NAME, cbs_xml()), upload("notes.csv", b"a,b\n1,2\n")]

    def test_roles_anonymous_and_csrf(self):
        for role in ALL_ROLES:
            with self.subTest(role=role):
                r = self.as_(role).post(self.url, {"files": [upload(RAW_ACQ_NAME, raw_csv())]})
                self.assertEqual(r.status_code, 200 if role == OPS else 403)
        self.assertEqual(self.as_(self.norole).post(self.url, {"files": [upload(DRC_NAME, b"x")]}).status_code, 403)
        self.assertEqual(self.as_(None).post(self.url, {"files": [upload(DRC_NAME, b"x")]}).status_code, 401)
        c, token = csrf_client(self.ops)
        self.assertEqual(c.post(self.url, {"files": [upload(DRC_NAME, b"x")]}).status_code, 403)
        self.assertEqual(c.post(self.url, {"files": [upload(DRC_NAME, b"x")]}, HTTP_X_CSRFTOKEN=token).status_code,
                         200)

    def test_proposals_from_names_and_content(self):
        r = self.as_(OPS).post(self.url, {"files": self.files()})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["business_date"], "2026-07-14")
        self.assertEqual(body["business_dates_found"], ["2026-07-14"])
        self.assertEqual(len(body["file_types"]), 10)
        self.assertEqual(body["detect_head_bytes"], 64 * 1024)
        raw, drc, ntsl, cbs, unknown = body["files"]
        self.assertEqual((raw["proposal"]["file_type_config_id"], raw["proposal"]["cycle"],
                          raw["proposal"]["business_date"], raw["proposal"]["matched_by"], raw["proposal"]["label"]),
                         (self.cfg["RAW_DATA_ACQ"].pk, "1C", "2026-07-14", "filename", "Raw Data ACQ"))
        self.assertEqual((drc["proposal"]["file_type_code"], drc["proposal"]["cycle"],
                          drc["proposal"]["business_date"]), ("DRC", None, "2026-07-14"))
        self.assertEqual((ntsl["proposal"]["file_type_code"], ntsl["proposal"]["cycle"], ntsl["detected_format"]),
                         ("NTSL", "DC1", "OOXML_ZIP"))
        self.assertEqual((cbs["proposal"]["file_type_code"], cbs["proposal"]["matched_by"],
                          cbs["proposal"]["business_date"], cbs["detected_format"]),
                         ("CBS_GL_EXTRACT", "content", None, "SPREADSHEETML_XML"))
        self.assertTrue(any("several dates" in w for w in cbs["warnings"]))
        self.assertIsNone(unknown["proposal"])
        self.assertTrue(unknown["warnings"])
        # Dry run: nothing saved, nothing logged.
        self.assertFalse(UploadedFile.objects.exists())
        self.assertFalse(OperationLog.objects.exists())

    def test_partial_zip_head_and_format_mismatch_warning(self):
        head = ntsl_zip()[:10]  # only the start of a zip: not checkable here
        r = self.as_(OPS).post(self.url, {"files": [upload(NTSL_NAME, head), upload(RAW_ACQ_NAME, cbs_xml())]})
        ntsl, raw = r.json()["files"]
        self.assertEqual(ntsl["detected_format"], "ZIP")
        self.assertFalse(any("refused" in w for w in ntsl["warnings"]))
        self.assertTrue(any("will be refused" in w for w in raw["warnings"]))

    def test_unknown_worksheet_layout_warns(self):
        r = self.as_(OPS).post(self.url, {"files": [upload(CBS_NAME, cbs_xml("Sheet1"))]})
        self.assertTrue(any("not one of the known" in w for w in r.json()["files"][0]["warnings"]))

    def test_duplicate_by_sha256_and_mixed_dates(self):
        existing = self.make_file(sha256_hash="ab" * 32, filename="old.csv")
        other = "UPIRAWDATAISSSSK150726_2C.csv"
        r = self.as_(OPS).post(self.url, {"files": [upload(RAW_ACQ_NAME, raw_csv()), upload(other, raw_csv())],
                                          "sha256": ["AB" * 32, ""]})
        body = r.json()
        dup = body["files"][0]["duplicate"]
        self.assertEqual((dup["uploaded_file_id"], dup["uploaded_by"]), (existing.pk, self.ops.username))
        self.assertIn(f"by {self.ops.username}", dup["message"])
        self.assertIsNone(body["files"][1]["duplicate"])
        self.assertEqual(body["business_dates_found"], ["2026-07-14", "2026-07-15"])
        self.assertTrue(body["warnings"])

    def test_slot_already_loaded_warns(self):
        self.make_file("RAW_DATA_ACQ", "1C", filename="first.csv")
        r = self.as_(OPS).post(self.url, {"files": [upload(RAW_ACQ_NAME, raw_csv())]})
        self.assertTrue(any("already loaded" in w for w in r.json()["files"][0]["warnings"]))

    def test_no_files(self):
        self.assertEqual(self.as_(OPS).post(self.url, {}).status_code, 400)


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------
class UploadTests(Slice2Base):
    def test_roles_anonymous_and_csrf(self):
        items = [{"file_type_config_id": self.cfg["DRC"].pk, "cycle": None}]
        for role in ALL_ROLES:
            if role == OPS:
                continue
            with self.subTest(role=role):
                r = self.post_upload(self.as_(role), [upload(DRC_NAME, drc_csv())], items)
                self.assertEqual(r.status_code, 403)
        self.assertEqual(self.post_upload(self.as_(self.norole), [upload(DRC_NAME, drc_csv())], items).status_code,
                         403)
        self.assertEqual(self.post_upload(self.as_(None), [upload(DRC_NAME, drc_csv())], items).status_code, 401)
        c, token = csrf_client(self.ops)
        self.assertEqual(self.post_upload(c, [upload(DRC_NAME, drc_csv())], items).status_code, 403)
        self.assertFalse(UploadedFile.objects.exists())
        with self.captureOnCommitCallbacks(execute=True):
            r = self.post_upload(c, [upload(DRC_NAME, drc_csv())], items, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 201)

    def test_superuser_holds_the_operations_role(self):
        admin = self.user(superuser=True)
        r = self.do_upload((DRC_NAME, drc_csv(), "DRC", None), user=admin)
        self.assertEqual(r.status_code, 201)

    def test_accepts_stores_logs_and_queues(self):
        r = self.do_upload((RAW_ACQ_NAME, raw_csv(), "RAW_DATA_ACQ", "1C"), (DRC_NAME, drc_csv(), "DRC", None),
                           (NTSL_NAME, ntsl_zip(), "NTSL", "DC1"), (CBS_NAME, cbs_xml(), "CBS_GL_EXTRACT", None))
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual((len(body["accepted"]), body["refused"]), (4, []))
        raw = UploadedFile.objects.get(filename=RAW_ACQ_NAME)
        self.assertEqual((raw.business_date, raw.batch_business_date, raw.settlement_cycle.cycle_code,
                          raw.uploaded_by, raw.processing_status, raw.source_system, raw.declared_format),
                         (D, D, "1C", self.ops, ProcessingStatus.RECEIVED, "manual web upload", ".csv"))
        path = Path(raw.path)
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), raw_csv())
        self.assertTrue(str(path).startswith(str(Path(self.work_dir).resolve())))
        self.assertEqual(path.parent.name, "2026-07-14")
        self.assertEqual(len(raw.sha256_hash), 64)
        cbs = UploadedFile.objects.get(filename=CBS_NAME)
        self.assertEqual((cbs.business_date, cbs.batch_business_date), (None, D))
        # The existing chain is queued once per file, after commit.
        self.assertEqual(sorted(c.args[0] for c in self.chain_mock.call_args_list),
                         sorted(UploadedFile.objects.values_list("pk", flat=True)))
        self.assertEqual(self.chain_mock.return_value.apply_async.call_count, 4)
        logs = OperationLog.objects.filter(operation=OperationType.FILE_UPLOADED)
        self.assertEqual(logs.count(), 4)
        log = logs.get(uploaded_file_id=raw.pk)
        self.assertEqual((log.user, log.username, log.business_date, log.source, log.details["sha256"]),
                         (self.ops, self.ops.username, D, "api", raw.sha256_hash))
        row = body["accepted"][0]
        self.assertEqual(set(row) >= {"id", "file_type", "cycle", "business_date", "processing_status", "uploaded_by",
                                      "sha256", "exception_counts", "date_selection"}, True)
        self.assertEqual(list(Path(self.work_dir).resolve().joinpath("uploads", "_incoming").iterdir()), [])

    def test_duplicate_refused_with_who_and_when(self):
        self.do_upload((DRC_NAME, drc_csv(), "DRC", None))
        r = self.do_upload((DRC_NAME, drc_csv(), "DRC", None))
        self.assertEqual(r.status_code, 400)
        body = r.json()
        self.assertEqual(body["accepted"], [])
        self.assertIn("Already uploaded on", body["refused"][0]["errors"][0])
        self.assertIn(f"by {self.ops.username}", body["refused"][0]["errors"][0])
        self.assertEqual(UploadedFile.objects.count(), 1)
        refused = OperationLog.objects.get(operation=OperationType.FILE_REFUSED)
        self.assertEqual((refused.user, refused.details["filename"]), (self.ops, DRC_NAME))
        self.assertTrue(refused.details["errors"])

    def test_one_refusal_does_not_stop_the_others(self):
        r = self.do_upload((RAW_ACQ_NAME, raw_csv("x"), "RAW_DATA_ACQ", "1C"),
                           ("UPIRAWDATAACQSSK140726_2C.csv", raw_csv("x"), "RAW_DATA_ACQ", "2C"))
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual((len(body["accepted"]), body["refused"][0]["index"]), (1, 1))
        self.assertIn("chosen twice", body["refused"][0]["errors"][0])

    def test_refusals(self):
        cases = [
            ("name date", ("UPIRAWDATAACQSSK150726_1C.csv", raw_csv("n"), "RAW_DATA_ACQ", "1C"), "2026-07-15"),
            ("content", (NTSL_NAME, b"not,a,zip\n", "NTSL", "DC1"), "content does not match"),
            ("no cycle", ("x1.csv", raw_csv("c"), "RAW_DATA_ACQ", None), "choose the cycle"),
            ("bad cycle", ("x2.csv", raw_csv("d"), "RAW_DATA_ACQ", "DC1"), "not a cycle of"),
            ("cycle on daily file", ("x3.csv", drc_csv("e"), "DRC", "1C"), "not per cycle"),
        ]
        for label, pair, expected in cases:
            with self.subTest(label):
                r = self.do_upload(pair)
                self.assertEqual(r.status_code, 400, r.content)
                self.assertIn(expected, " ".join(r.json()["refused"][0]["errors"]))
        self.assertFalse(UploadedFile.objects.exists())
        self.assertEqual(OperationLog.objects.filter(operation=OperationType.FILE_REFUSED).count(), len(cases))

    def test_unknown_or_other_module_config(self):
        from core.models import PaymentModule

        imps = PaymentModule.objects.create(code="IMPS", name="IMPS")
        other = FileTypeConfig.objects.create(payment_module=imps, file_type_code="DRC", register_type="DRC",
                                              payload_format="CSV")
        with self.captureOnCommitCallbacks(execute=True):
            r = self.post_upload(self.as_(OPS), [upload(DRC_NAME, drc_csv())],
                                 [{"file_type_config_id": other.pk, "cycle": None}])
        self.assertEqual(r.status_code, 400)
        self.assertIn("Choose a file type", r.json()["refused"][0]["errors"][0])

    def test_slot_taken_unless_failed(self):
        first = self.make_file("RAW_DATA_ACQ", "1C", filename="first.csv")
        r = self.do_upload((RAW_ACQ_NAME, raw_csv("s"), "RAW_DATA_ACQ", "1C"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("already loaded", r.json()["refused"][0]["errors"][0])
        UploadedFile.objects.filter(pk=first.pk).update(processing_status=ProcessingStatus.VALIDATION_FAILED)
        self.assertEqual(self.do_upload((RAW_ACQ_NAME, raw_csv("s"), "RAW_DATA_ACQ", "1C")).status_code, 201)
        # CBS: a second CBS file for a batch date already taken (awaiting) is refused too.
        self.make_file("CBS_GL_EXTRACT", None, status=ProcessingStatus.AWAITING_DATE_SELECTION)
        r = self.do_upload((CBS_NAME, cbs_xml(), "CBS_GL_EXTRACT", None))
        self.assertIn("already waiting for its business date", r.json()["refused"][0]["errors"][0])

    def test_archived_date_refused(self):
        with mock.patch("file_processing.intake.date_is_archived", return_value=True):
            r = self.do_upload((DRC_NAME, drc_csv(), "DRC", None))
        self.assertEqual(r.status_code, 409)
        self.assertIn("archived", r.json()["detail"])
        self.assertFalse(UploadedFile.objects.exists())

    def test_invalid_input(self):
        c = self.as_(OPS)
        f = [upload(DRC_NAME, drc_csv())]
        item = [{"file_type_config_id": self.cfg["DRC"].pk}]
        for label, resp, field in (
            ("no date", self.post_upload(c, f, item, business_date=""), "business_date"),
            ("bad date", self.post_upload(c, [upload(DRC_NAME, drc_csv())], item, business_date="14-07-2026"),
             "business_date"),
            ("no files", self.post_upload(c, [], item), "files"),
            ("items count", self.post_upload(c, [upload(DRC_NAME, drc_csv())], item * 2), "items"),
            ("items json", c.post(FILES, {"business_date": "2026-07-14", "files": [upload(DRC_NAME, drc_csv())],
                                          "items": "{nope"}), "items"),
        ):
            with self.subTest(label):
                self.assertEqual(resp.status_code, 400)
                self.assertIn(field, resp.json()["errors"])

    @override_settings(UPLOAD_MAX_FILE_BYTES=10)
    def test_size_limit(self):
        r = self.do_upload((DRC_NAME, drc_csv(), "DRC", None))
        self.assertIn("the limit is 10", r.json()["refused"][0]["errors"][0])

    def test_queue_failure_is_recorded(self):
        self.chain_mock.return_value.apply_async.side_effect = ConnectionError("broker down")
        r = self.do_upload((DRC_NAME, drc_csv(), "DRC", None))
        self.assertEqual(r.status_code, 201)
        f = UploadedFile.objects.get()
        self.assertEqual(f.processing_status, ProcessingStatus.RECEIVED)
        self.assertTrue(FileValidationException.objects.filter(uploaded_file=f, rule_code="QUEUE_FAILED").exists())


class UploadThroughPipelineTests(Slice2Base):
    """The uploaded file goes through the EXISTING processing chain (run
    eagerly here) to LOADED, then the run builds the date."""

    def test_drc_upload_loads_and_runs(self):
        from file_processing.tasks import build_processing_chain

        with mock.patch.object(intake, "_queue_processing", lambda pk: build_processing_chain(pk).apply()):
            r = self.do_upload((DRC_NAME, drc_csv(), "DRC", None))
        self.assertEqual(r.status_code, 201, r.content)
        f = UploadedFile.objects.get()
        self.assertEqual((f.processing_status, f.valid_record_count), (ProcessingStatus.LOADED, 2))
        with mock.patch("reconciliation.tasks.run_business_date.delay") as delay,                 self.captureOnCommitCallbacks(execute=True):
            r = self.as_(OPS).post(f"{DATES}2026-07-14/run/")
        self.assertEqual(r.status_code, 202, r.content)
        delay.assert_called_once_with(r.json()["id"])
        run = execute_run(r.json()["id"])
        self.assertEqual(run.status, RunStatus.SUCCEEDED, run.error)
        self.assertTrue(TransactionMatchGroup.objects.filter(business_date=D, is_superseded=False).exists())


# ---------------------------------------------------------------------------
# files list / detail / exceptions
# ---------------------------------------------------------------------------
class FileReadTests(Slice2Base):
    def setUp(self):
        super().setUp()
        self.raw = self.make_file("RAW_DATA_ACQ", "1C", filename="raw1.csv")
        self.raw2 = self.make_file("RAW_DATA_ISS", "2C", status=ProcessingStatus.PARSED, filename="raw2.csv")
        self.cbs = self.make_file("CBS_GL_EXTRACT", None, status=ProcessingStatus.AWAITING_DATE_SELECTION,
                                  detected_dates=["2026-07-13", "2026-07-14", "2026-07-15"], filename="cbs.xls")
        self.old = self.make_file("DRC", None, d=date(2026, 7, 13), filename="drc13.csv")
        for sev in ("ERROR", "WARNING", "WARNING"):
            FileValidationException.objects.create(uploaded_file=self.raw, rule_code=f"R_{sev}", severity=sev,
                                                   message="synthetic")

    def test_read_matrix(self):
        self.assert_read_matrix(FILES)
        self.assert_read_matrix(f"{FILES}{self.raw.pk}/")
        self.assert_read_matrix(f"{FILES}{self.raw.pk}/exceptions/")

    def ids(self, params=None):
        r = self.as_(Role.AUDITOR).get(FILES, params or {})
        self.assertEqual(r.status_code, 200, r.content)
        return [row["id"] for row in r.json()["results"]]

    def test_list_filters_and_ordering(self):
        self.assertEqual(self.ids(), [self.old.pk, self.cbs.pk, self.raw2.pk, self.raw.pk])
        self.assertEqual(set(self.ids({"business_date": "2026-07-14"})), {self.raw.pk, self.raw2.pk, self.cbs.pk})
        self.assertEqual(set(self.ids({"file_type": "RAW_DATA"})), {self.raw.pk, self.raw2.pk})
        self.assertEqual(self.ids({"file_type_config": str(self.cfg["RAW_DATA_ISS"].pk)}), [self.raw2.pk])
        self.assertEqual(self.ids({"cycle": "1C,2C", "ordering": "filename"}), [self.raw.pk, self.raw2.pk])
        self.assertEqual(self.ids({"status": "AWAITING_DATE_SELECTION"}), [self.cbs.pk])
        self.assertEqual(self.ids({"current": "false"}), [])
        for bad in ({"status": "NOPE"}, {"business_date": "x"}, {"ordering": "size"}, {"file_type_config": "a"}):
            with self.subTest(bad):
                self.assertEqual(self.as_(OPS).get(FILES, bad).status_code, 400)

    def test_list_row_and_processing_count(self):
        body = self.as_(Role.MAKER).get(FILES, {"page_size": 2}).json()
        self.assertEqual((body["count"], len(body["results"]), body["processing_count"]), (4, 2, 1))
        row = next(r for r in self.as_(OPS).get(FILES).json()["results"] if r["id"] == self.raw.pk)
        self.assertEqual(row["exception_counts"], {"INFO": 0, "WARNING": 2, "ERROR": 1})
        self.assertEqual((row["file_type"]["label"], row["cycle"], row["state"], row["uploaded_by"]),
                         ("Raw Data ACQ", "1C", "LOADED", self.ops.username))

    def test_detail_with_date_selection_and_history(self):
        self.do_upload((CBS_NAME, cbs_xml(), "CBS_GL_EXTRACT", None), business_date="2026-07-16")
        f = UploadedFile.objects.get(filename=CBS_NAME)
        UploadedFile.objects.filter(pk=f.pk).update(processing_status=ProcessingStatus.AWAITING_DATE_SELECTION,
                                                    detected_dates=["2026-07-15", "2026-07-16", "2026-07-17"])
        body = self.as_(Role.CHECKER).get(f"{FILES}{f.pk}/").json()
        self.assertEqual(body["date_selection"], {"detected_dates": ["2026-07-15", "2026-07-16", "2026-07-17"],
                                                  "batch_business_date": "2026-07-16", "preselected": "2026-07-16"})
        self.assertEqual([h["operation"] for h in body["history"]], ["FILE_UPLOADED"])
        self.assertEqual(body["history"][0]["username"], self.ops.username)
        # Batch date not among the detected dates: nothing pre-selected.
        body = self.as_(OPS).get(f"{FILES}{self.cbs.pk}/").json()
        self.assertEqual(body["date_selection"]["preselected"], "2026-07-14")
        UploadedFile.objects.filter(pk=self.cbs.pk).update(batch_business_date=date(2026, 7, 20))
        self.assertIsNone(self.as_(OPS).get(f"{FILES}{self.cbs.pk}/").json()["date_selection"]["preselected"])
        self.assertIsNone(self.as_(OPS).get(f"{FILES}{self.raw.pk}/").json()["date_selection"])

    def test_exceptions_list(self):
        url = f"{FILES}{self.raw.pk}/exceptions/"
        body = self.as_(OPS).get(url, {"severity": "WARNING", "page_size": 1}).json()
        self.assertEqual((body["count"], len(body["results"]), body["results"][0]["severity"]), (2, 1, "WARNING"))
        self.assertEqual(self.as_(OPS).get(url, {"severity": "BAD"}).status_code, 400)
        self.assertEqual(self.as_(OPS).get(url, {"rule_code": "R_ERROR"}).json()["count"], 1)

    def test_other_module_and_unknown_are_404(self):
        from core.models import PaymentModule

        PaymentModule.objects.create(code="IMPS", name="IMPS")
        self.assertEqual(self.as_(OPS).get(f"{V1}/imps/files/{self.raw.pk}/").status_code, 404)
        self.assertEqual(self.as_(OPS).get(f"{FILES}999999/").status_code, 404)
        self.assertEqual(self.as_(OPS).get(f"{V1}/nope/files/").status_code, 404)


# ---------------------------------------------------------------------------
# CBS date selection
# ---------------------------------------------------------------------------
class SelectDateTests(Slice2Base):
    def setUp(self):
        super().setUp()
        self.cbs = self.make_file("CBS_GL_EXTRACT", None, status=ProcessingStatus.AWAITING_DATE_SELECTION,
                                  detected_dates=["2026-07-13", "2026-07-14", "2026-07-15"], filename="cbs.xls")
        self.url = f"{FILES}{self.cbs.pk}/select-date/"
        patcher = mock.patch("file_processing.intake.chain")
        self.resume = patcher.start()
        self.addCleanup(patcher.stop)

    def select(self, who=OPS, value="2026-07-14"):
        with self.captureOnCommitCallbacks(execute=True):
            return self.post(self.as_(who), self.url, {"business_date": value})

    def test_operations_user_selects_and_it_is_audited(self):
        r = self.select()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["business_date"], r.json()["processing_status"]), ("2026-07-14", "STAGED"))
        self.cbs.refresh_from_db()
        self.assertEqual((self.cbs.business_date, self.cbs.processing_status), (D, ProcessingStatus.STAGED))
        normalize_sig, promote_sig = self.resume.call_args.args
        self.assertEqual(normalize_sig.task, "file_processing.tasks.normalize_rows")
        self.assertEqual(normalize_sig.args, ({"uploaded_file_id": self.cbs.pk, "halted": False},))
        self.assertEqual(promote_sig.task, "file_processing.tasks.promote_to_fact")
        self.resume.return_value.apply_async.assert_called_once()
        log = OperationLog.objects.get(operation=OperationType.BUSINESS_DATE_SELECTED)
        self.assertEqual((log.user, log.business_date, log.uploaded_file_id, log.source,
                          log.details["detected_dates"], log.details["batch_business_date"]),
                         (self.ops, D, self.cbs.pk, "api", ["2026-07-13", "2026-07-14", "2026-07-15"], "2026-07-14"))
        self.assertEqual([h["operation"] for h in r.json()["history"]], ["BUSINESS_DATE_SELECTED"])

    def test_refusals(self):
        for role in ALL_ROLES:
            if role != OPS:
                with self.subTest(role=role):
                    self.assertEqual(self.select(role).status_code, 403)  # incl. Administrator (P10b)
        self.assertEqual(self.select(None).status_code, 401)
        r = self.select(value="2026-07-16")
        self.assertEqual(r.status_code, 400)
        self.assertIn("business_date", r.json()["errors"])
        self.assertEqual(self.select(value="14/07/2026").status_code, 400)
        self.assertEqual(self.select(value="").status_code, 400)
        self.resume.assert_not_called()
        self.assertFalse(OperationLog.objects.exists())
        self.assertEqual(self.select().status_code, 200)
        self.assertEqual(self.select().status_code, 409)  # not awaiting any more
        c, _ = csrf_client(self.ops)
        self.assertEqual(c.post(self.url, {"business_date": "2026-07-14"}, content_type="application/json").status_code,
                         403)

    def test_date_already_has_a_cbs_file(self):
        self.make_file("CBS_GL_EXTRACT", None, filename="cbs-loaded.xls", business_date=D)
        r = self.select()
        self.assertEqual(r.status_code, 409)
        self.assertIn("already loaded", r.json()["detail"])

    def test_archived_date(self):
        with mock.patch("file_processing.intake.date_is_archived", return_value=True):
            self.assertEqual(self.select().status_code, 409)


# ---------------------------------------------------------------------------
# business dates and the run
# ---------------------------------------------------------------------------
class DateTests(Slice2Base):
    def setUp(self):
        super().setUp()
        self.raw = self.make_file("RAW_DATA_ACQ", "1C")
        self.failed = self.make_file("RAW_DATA_ACQ", "2C", status=ProcessingStatus.VALIDATION_FAILED)
        self.cbs = self.make_file("CBS_GL_EXTRACT", None, status=ProcessingStatus.AWAITING_DATE_SELECTION)
        self.make_file("DRC", None, d=date(2026, 7, 13))
        self.run_url = f"{DATES}2026-07-14/run/"

    def test_read_matrix(self):
        self.assert_read_matrix(DATES)
        self.assert_read_matrix(f"{DATES}2026-07-14/")

    def test_list(self):
        body = self.as_(Role.MAKER).get(DATES).json()
        self.assertEqual([r["business_date"] for r in body["results"]], ["2026-07-14", "2026-07-13"])
        row = body["results"][0]
        expected = 1 + 10 + 10 + 10 + 10 + 10 + 1 + 1 + 1 + 12  # CBS, Raw x2, Merchant x2, BBP, DRC, TO, ADJ, NTSL
        self.assertEqual(row["files"]["expected"], expected)
        self.assertEqual((row["files"]["loaded"], row["files"]["failed"], row["files"]["awaiting_date_selection"],
                          row["files"]["missing"], row["files"]["files_received"]), (1, 1, 1, expected - 3, 3))
        self.assertIn({"file_type_config_id": self.cfg["DRC"].pk, "label": "Debit Reversal Confirmation Report",
                       "cycle": None}, row["missing"])
        self.assertEqual(row["matrix"], {"built": False, "match_groups": 0, "built_at": None})
        self.assertEqual((row["rule_evaluation"], row["settlement"], row["last_run"]), (None, None, None))
        self.assertEqual(row["cases"], {"total": 0, "active": 0})
        self.assertEqual(row["run_blockers"], [])
        self.assertEqual([r["business_date"] for r in self.as_(OPS).get(DATES, {"ordering": "business_date"})
                          .json()["results"]], ["2026-07-13", "2026-07-14"])
        self.assertEqual(self.as_(OPS).get(DATES, {"date_from": "2026-07-14"}).json()["count"], 1)
        self.assertEqual(self.as_(OPS).get(DATES, {"date_to": "2026-07-13"}).json()["count"], 1)
        for bad in ({"date_from": "x"}, {"ordering": "x"}):
            self.assertEqual(self.as_(OPS).get(DATES, bad).status_code, 400)

    def test_detail(self):
        body = self.as_(Role.AUDITOR).get(f"{DATES}2026-07-14/").json()
        self.assertEqual(len(body["checklist"]), body["files"]["expected"])
        slot = next(s for s in body["checklist"] if s["file_type_code"] == "RAW_DATA" and s["cycle"] == "1C"
                    and s["label"] == "Raw Data ACQ")
        self.assertEqual((slot["state"], slot["files"][0]["id"]), ("LOADED", self.raw.pk))
        cbs = next(s for s in body["checklist"] if s["file_type_code"] == "CBS_GL_EXTRACT")
        self.assertEqual(cbs["state"], "AWAITING_DATE_SELECTION")
        self.assertEqual((body["archived"], body["runs"], body["unexpected_files"]), (False, [], []))
        self.assertEqual(self.as_(OPS).get(f"{DATES}2026-08-01/").status_code, 404)
        self.assertEqual(self.as_(OPS).get(f"{DATES}not-a-date/").status_code, 400)

    def start(self, who=OPS, url=None):
        with mock.patch("reconciliation.tasks.run_business_date.delay") as delay, \
                self.captureOnCommitCallbacks(execute=True):
            r = self.post(self.as_(who), url or self.run_url)
        return r, delay

    def test_run_started_and_audited(self):
        r, delay = self.start()
        self.assertEqual(r.status_code, 202, r.content)
        body = r.json()
        self.assertEqual((body["status"], body["requested_by"], body["business_date"]),
                         ("QUEUED", self.ops.username, "2026-07-14"))
        delay.assert_called_once_with(body["id"])
        run = ReconciliationRun.objects.get()
        self.assertEqual((run.requested_by, run.source), (self.ops, "api"))
        log = OperationLog.objects.get(operation=OperationType.RUN_STARTED)
        self.assertEqual((log.user, log.run_id, log.business_date), (self.ops, run.pk, D))
        self.assertEqual(log.details["checklist"]["loaded"], 1)
        row = self.as_(OPS).get(DATES).json()["results"][0]
        self.assertEqual(row["last_run"]["id"], run.pk)
        self.assertEqual(row["run_blockers"][0]["code"], "RUN_IN_PROGRESS")
        # A second press while it is queued: 409.
        self.assertEqual(self.start()[0].status_code, 409)
        detail = self.as_(OPS).get(f"{DATES}2026-07-14/").json()
        self.assertEqual(([x["id"] for x in detail["runs"]], [h["operation"] for h in detail["history"]]),
                         ([run.pk], ["RUN_STARTED"]))

    def test_run_refusals(self):
        for role in ALL_ROLES:
            if role != OPS:
                with self.subTest(role=role):
                    self.assertEqual(self.start(role)[0].status_code, 403)
        self.assertEqual(self.start(None)[0].status_code, 401)
        r = self.start(url=f"{DATES}2026-08-01/run/")
        self.assertEqual(r[0].status_code, 400)  # nothing loaded
        self.assertEqual(self.start(url=f"{DATES}bad/run/")[0].status_code, 400)
        UploadedFile.objects.filter(pk=self.failed.pk).update(processing_status=ProcessingStatus.NORMALIZED)
        r, delay = self.start()
        self.assertEqual(r.status_code, 409)
        self.assertIn("still being processed", r.json()["detail"])
        delay.assert_not_called()
        self.assertFalse(ReconciliationRun.objects.exists())
        self.assertFalse(OperationLog.objects.exists())
        c, _ = csrf_client(self.ops)
        self.assertEqual(c.post(f"{DATES}2026-07-13/run/").status_code, 403)

    def test_queue_failure_fails_the_run(self):
        with mock.patch("reconciliation.tasks.run_business_date.delay", side_effect=ConnectionError("down")), \
                self.captureOnCommitCallbacks(execute=True):
            self.post(self.as_(OPS), self.run_url)
        run = ReconciliationRun.objects.get()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn("could not be queued", run.error)
        self.assertEqual(self.start()[0].status_code, 202)  # a new run may start

    def test_execute_runs_the_three_steps_in_order(self):
        r, _ = self.start()
        run = execute_run(r.json()["id"])
        self.assertEqual(run.status, RunStatus.SUCCEEDED, run.error)
        self.assertEqual([run.steps[s]["status"] for s in ("PRESENCE", "RULES", "SETTLEMENT")], ["DONE"] * 3)
        self.assertIn("total_match_groups", run.steps["PRESENCE"]["summary"])
        self.assertIn("cases", run.steps["RULES"]["summary"])
        self.assertEqual(run.steps["SETTLEMENT"]["summary"]["status"], "NOT_READY")  # no NTSL in this fixture
        ev = RuleEvaluationRun.objects.get(business_date=D)
        self.assertEqual(ev.triggered_by, f"run #{run.pk} ({self.ops.username})")
        self.assertEqual(execute_run(run.pk).status, RunStatus.SUCCEEDED)  # not run twice
        self.assertEqual(RuleEvaluationRun.objects.filter(business_date=D).count(), 1)
        row = self.as_(OPS).get(DATES).json()["results"][0]
        self.assertEqual((row["rule_evaluation"]["id"], row["settlement"]["status"], row["last_run"]["status"]),
                         (ev.pk, "NOT_READY", "SUCCEEDED"))
        self.assertEqual(self.start()[0].status_code, 202)  # re-run: same button

    def test_execute_failure_is_recorded(self):
        r, _ = self.start()
        with mock.patch("reconciliation.presence.build_presence_matrix", side_effect=RuntimeError("boom")):
            run = execute_run(r.json()["id"])
        self.assertEqual((run.status, run.steps["PRESENCE"]["status"], run.steps["RULES"]["status"]),
                         (RunStatus.FAILED, "FAILED", "PENDING"))
        self.assertEqual(run.error, "PRESENCE: boom")
        self.assertEqual(self.start()[0].status_code, 202)


class SummaryMissingTests(Slice2Base):
    def test_home_operations_counts_missing_files(self):
        self.make_file("DRC", None)
        ops = self.as_(OPS).get(f"{V1}/upi/summary/").json()["operations"]
        self.assertEqual((ops["latest_business_date"], ops["files_missing"]), ("2026-07-14", 65))


class AdminDateSelectionAuditTests(Slice2Base):
    def test_admin_action_uses_the_service_and_is_audited(self):
        from django.urls import reverse

        cbs = self.make_file("CBS_GL_EXTRACT", None, status=ProcessingStatus.AWAITING_DATE_SELECTION,
                             detected_dates=["2026-07-14"])
        admin = self.user(superuser=True)
        c = Client()
        c.force_login(admin)
        with mock.patch("file_processing.intake.chain"), self.captureOnCommitCallbacks(execute=True):
            r = c.post(reverse("admin:file_processing_uploadedfile_changelist"),
                       {"action": "select_business_date", "_selected_action": [str(cbs.pk)], "apply": "1",
                        "business_date": "2026-07-14"})
        self.assertEqual(r.status_code, 302)
        log = OperationLog.objects.get(operation=OperationType.BUSINESS_DATE_SELECTED)
        self.assertEqual((log.user, log.source), (admin, "admin"))
        # A staff user without the Operations User role is refused (P10b).
        cbs2 = self.make_file("CBS_GL_EXTRACT", None, status=ProcessingStatus.AWAITING_DATE_SELECTION,
                              detected_dates=["2026-07-15"], d=date(2026, 7, 15))
        from django.contrib.auth.models import Permission

        staff_admin = self.user(Role.ADMINISTRATOR)
        staff_admin.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="file_processing", codename__in=("view_uploadedfile", "change_uploadedfile")))
        c.force_login(staff_admin)
        with mock.patch("file_processing.intake.chain") as resume:
            r = c.post(reverse("admin:file_processing_uploadedfile_changelist"),
                       {"action": "select_business_date", "_selected_action": [str(cbs2.pk)], "apply": "1",
                        "business_date": "2026-07-15"}, follow=True)
        self.assertContains(r, "Only an Operations User")
        resume.assert_not_called()
        cbs2.refresh_from_db()
        self.assertEqual(cbs2.processing_status, ProcessingStatus.AWAITING_DATE_SELECTION)


class OperationLogAppendOnlyTests(Slice2Base):
    def test_rows_cannot_change(self):
        from core.append_only import AppendOnlyError
        from core.operations import log_operation

        e = log_operation(OperationType.RUN_STARTED, user=self.ops, payment_module=self.module)
        with self.assertRaises(AppendOnlyError):
            e.save()
        with self.assertRaises(AppendOnlyError):
            OperationLog.objects.all().delete()
        with self.assertRaises(AppendOnlyError):
            OperationLog.objects.update(username="x")
