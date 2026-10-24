"""
/api/v1/<module>/ NPCI file endpoints (docs/npci_file_design_draft_v1.txt Amendment 1, A7 API contract):
preview, generate, list, detail, download, the /me capabilities and the case
page link. Roles per owner Q2: all five roles read; Checker generates;
Maker, Checker and Operations User download; Administrator and Auditor get
no download. Synthetic data only.
"""
from __future__ import annotations

from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import OperationLog, OperationType, PaymentModule
from exception_management.roles import Role
from npci_files.models import NpciFile
from npci_files.tests.base import D, NpciTestBase
from api.tests.test_v1 import ALL_ROLES, JSON, V1, V1TestBase, csrf_client

A, O, M, C, U = Role.ADMINISTRATOR, Role.OPERATIONS_USER, Role.MAKER, Role.CHECKER, Role.AUDITOR
UPI = f"{V1}/upi"
PREVIEW = f"{UPI}/dates/2026-07-14/npci-file/preview/"
GENERATE = f"{UPI}/dates/2026-07-14/npci-file/"
FILES = f"{UPI}/npci-files/"


class NpciApiBase(V1TestBase, NpciTestBase):
    def setUp(self):
        V1TestBase.setUp(self)
        self.mk, self.chk = self.users[M], self.users[C]
        self._row = 0
        self.approved = self.drc_case("100000000001", "R01", "140.50", "'000000001111")
        self.approve(self.approved)
        self.open_case = self.timeout_case("100000000002", "R05")

    def ok(self, who, url, params=None):
        r = self.as_(who).get(url, params or {})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def generate(self, who=C, **body):
        return self.post(self.as_(who), GENERATE, body)


class PreviewTests(NpciApiBase):
    def test_read_matrix(self):
        self.assert_read_matrix(PREVIEW)
        self.assert_read_matrix(FILES)

    def test_preview_payload(self):
        body = self.ok(U, PREVIEW)
        self.assertEqual((body["file_date"], body["file_format"], body["next_file_name"], body["next_sequence"]),
                         ("2026-07-14", "SSKUPI", "SSKUPI14072026.csv", 1))
        self.assertEqual((body["row_count"], body["total_amount"], body["partial"]), (1, "140.50", True))
        row = body["rows"][0]
        self.assertEqual({k: row[k] for k in ("line", "rrn", "flag", "code", "shtdat", "amount", "account_masked",
                                              "text", "rule_code", "action_type")},
                         {"line": 1, "rrn": "100000000001", "flag": "DRC", "code": "102", "shtdat": "2026-07-14",
                          "amount": "140.5", "account_masked": "********1111", "text": "Online reverse",
                          "rule_code": "R01", "action_type": "DRC_102"})
        self.assertEqual(body["counts"], [{"flag": "DRC", "code": "102", "text": "Online reverse", "rows": 1,
                                           "amount": "140.50"}])
        self.assertEqual(body["not_included_counts"], {"OPEN": 1})
        excluded = body["not_included"][0]
        self.assertEqual((excluded["case_id"], excluded["flag"], excluded["code"], excluded["reason"]),
                         (self.open_case.pk, "RET", "115", "OPEN"))
        self.assertFalse(body["can_generate"])  # Auditor
        self.assertTrue(self.ok(C, PREVIEW)["can_generate"])
        self.assertNotIn("000000001111", str(body))
        self.assertFalse(NpciFile.objects.exists())

    def test_bad_date_and_unknown_module(self):
        self.assertEqual(self.as_(C).get(f"{UPI}/dates/14-07-2026/npci-file/preview/").status_code, 400)
        self.assertEqual(self.as_(C).get(f"{V1}/xyz/npci-files/").status_code, 404)
        PaymentModule.objects.create(code="IMPS", name="IMPS")
        r = self.as_(C).get(f"{V1}/imps/dates/2026-07-14/npci-file/preview/")
        self.assertEqual(r.status_code, 404)
        self.assertIn("No NPCI file format", r.json()["detail"])


class GenerateTests(NpciApiBase):
    def test_roles_csrf_and_partial_confirmation(self):
        for role in (A, O, M, U):
            with self.subTest(role=role):
                self.assertEqual(self.generate(role, confirm_partial=True).status_code, 403)
        self.assertEqual(self.generate(self.norole, confirm_partial=True).status_code, 403)
        self.assertEqual(self.generate(None, confirm_partial=True).status_code, 401)
        c, token = csrf_client(self.chk)
        self.assertEqual(c.post(GENERATE, {"confirm_partial": True}, content_type=JSON).status_code, 403)
        r = c.post(GENERATE, {}, content_type=JSON, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 409)
        self.assertIn("confirm_partial", r.json()["errors"])
        self.assertEqual(self.generate(confirm_partial="yes").status_code, 400)
        r = c.post(GENERATE, {"confirm_partial": True, "remarks": "RET due 4 pm"}, content_type=JSON,
                   HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 201, r.content)
        b = r.json()
        self.assertEqual((b["file_name"], b["sequence"], b["row_count"], b["partial"], b["remarks"],
                          b["generated_by"], b["not_included_count"]),
                         ("SSKUPI14072026.csv", 1, 1, True, "RET due 4 pm", self.chk.username, 1))
        self.assertTrue(b["download_url"].endswith(f"/npci-files/{b['id']}/download/"))
        self.assertEqual([h["operation"] for h in b["history"]], ["NPCI_FILE_GENERATED"])
        self.assertEqual(b["rows"][0]["account_masked"], "********1111")
        r = self.generate(confirm_partial=True)
        self.assertEqual(r.status_code, 409)  # nothing new to include
        self.assertIn("rows", r.json()["errors"])
        self.assertNotIn("sent", str(r.json()))  # the app never sends anything to NPCI (owner 2026-10-23)

    def test_second_file_and_case_link(self):
        self.generate(confirm_partial=True)
        self.approve(self.open_case)
        preview = self.ok(M, PREVIEW)
        self.assertEqual((preview["next_file_name"], preview["row_count"], preview["already_sent_count"],
                          preview["already_sent_files"], preview["partial"]),
                         ("SSKUPI14072026_2.csv", 1, 1, ["SSKUPI14072026.csv"], False))
        self.assertEqual([f["file_name"] for f in preview["files"]], ["SSKUPI14072026.csv"])
        r = self.generate()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual([x["rrn"] for x in r.json()["rows"]], ["100000000002"])
        case = self.ok(M, f"{UPI}/cases/{self.open_case.pk}/")
        self.assertEqual((case["npci_file"]["file_name"], case["npci_file"]["line"]), ("SSKUPI14072026_2.csv", 1))
        self.assertIsNone(self.ok(M, f"{UPI}/cases/{self.make_case('100000000099').pk}/")["npci_file"])


class FileReadAndDownloadTests(NpciApiBase):
    def setUp(self):
        super().setUp()
        self.file_id = self.generate(confirm_partial=True).json()["id"]
        self.detail = f"{FILES}{self.file_id}/"
        self.download = f"{FILES}{self.file_id}/download/"

    def test_list_and_detail(self):
        self.assert_read_matrix(self.detail)
        body = self.ok(U, FILES)
        self.assertEqual((body["count"], body["results"][0]["file_name"], body["can_download"]),
                         (1, "SSKUPI14072026.csv", False))
        self.assertIsNone(body["results"][0]["download_url"])  # Auditor
        self.assertIsNone(self.ok(A, self.detail)["download_url"])
        self.assertIsNotNone(self.ok(O, self.detail)["download_url"])
        self.assertEqual(self.ok(M, FILES, {"file_date": "2026-07-15"})["count"], 0)
        self.assertEqual(self.ok(M, FILES, {"date_from": "2026-07-14", "date_to": "2026-07-14"})["count"], 1)
        self.assertEqual(self.as_(M).get(FILES, {"file_date": "x"}).status_code, 400)
        self.assertEqual(self.as_(M).get(f"{FILES}999999/").status_code, 404)

    def test_download_roles_bytes_and_audit(self):
        f = NpciFile.objects.get(pk=self.file_id)
        for role in (M, C, O):
            with self.subTest(role=role):
                r = self.as_(role).get(self.download)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.content, f.content.encode("ascii"))
                self.assertEqual(r["Content-Type"], "text/csv")
                self.assertEqual(r["Content-Disposition"], 'attachment; filename="SSKUPI14072026.csv"')
                self.assertEqual(r["X-Content-SHA256"], f.sha256)
                self.assertTrue(r.content.endswith(b"\r\n"))
        for who in (A, U, self.norole):
            with self.subTest(who=who):
                self.assertEqual(self.as_(who).get(self.download).status_code, 403)
        self.assertEqual(self.as_(None).get(self.download).status_code, 401)
        logs = OperationLog.objects.filter(operation=OperationType.NPCI_FILE_DOWNLOADED)
        self.assertEqual(sorted(logs.values_list("username", flat=True)),
                         sorted(self.users[r].username for r in (M, C, O)))
        history = self.ok(U, self.detail)["history"]
        self.assertEqual([h["operation"] for h in history], ["NPCI_FILE_GENERATED"] + ["NPCI_FILE_DOWNLOADED"] * 3)
        audit = self.ok(U, f"{V1}/audit/operations/", {"event_type": "NPCI_FILE_DOWNLOADED"})
        self.assertEqual(audit["count"], 3)

    def test_download_history_on_each_file(self):
        """Owner 2026-10-23: who downloaded each file and when, newest first (no approval step)."""
        self.assertEqual((self.ok(U, FILES)["results"][0]["download_count"], self.ok(U, self.detail)["downloads"]),
                         (0, []))
        self.as_(M).get(self.download)
        self.as_(C).get(self.download)
        self.as_(U).get(self.download)  # refused (403): not a download, not listed
        row = self.ok(U, FILES)["results"][0]  # the Auditor sees the history, without a download link
        self.assertEqual(row["download_count"], 2)
        self.assertEqual([d["by"] for d in row["downloads"]], [self.users[C].username, self.users[M].username])
        self.assertGreaterEqual(row["downloads"][0]["at"], row["downloads"][1]["at"])
        self.assertEqual(self.ok(A, self.detail)["downloads"], row["downloads"])
        # A second file: its own (empty) history; the list reads all histories in one query.
        self.approve(self.open_case)
        second = self.generate().json()
        self.assertEqual((second["download_count"], second["downloads"]), (0, []))
        client = self.as_(O)
        with CaptureQueriesContext(connection) as queries:
            body = client.get(FILES).json()
        log_queries = [q["sql"] for q in queries.captured_queries if "core_operationlog" in q["sql"]]
        self.assertEqual(len(log_queries), 1, log_queries)  # one batched query for both files
        rows = {r["file_name"]: r for r in body["results"]}
        self.assertEqual((rows["SSKUPI14072026.csv"]["download_count"], rows["SSKUPI14072026_2.csv"]["download_count"]),
                         (2, 0))

    def test_module_isolation(self):
        PaymentModule.objects.create(code="IMPS", name="IMPS")
        self.assertEqual(self.as_(M).get(f"{V1}/imps/npci-files/{self.file_id}/").status_code, 404)
        self.assertEqual(self.as_(M).get(f"{V1}/imps/npci-files/{self.file_id}/download/").status_code, 404)


class CapabilityTests(NpciApiBase):
    def test_me_capabilities(self):
        for role in ALL_ROLES:
            with self.subTest(role=role):
                perms = self.ok(role, f"{V1}/me/")["permissions"]
                self.assertEqual((perms["npci_files.view"], perms["npci_files.generate"],
                                  perms["npci_files.download"]), (True, role == C, role in (M, C, O)))

    def test_response_code_editable_fields(self):
        rows = {r["label"]: r for r in self.ok(M, f"{UPI}/settings/response-codes/", {"page_size": 100})["results"]}
        self.assertEqual(rows["DRC 102 - Online reverse"]["editable_fields"], ["description"])
        self.assertEqual(rows["RET (unknown) - Account closed"]["editable_fields"], ["code", "description"])
