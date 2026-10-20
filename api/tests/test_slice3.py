"""
/api/v1/ Phase 5 Slice 3 (design docs/phase5_design_draft_v1.txt 4.12-4.16):
reconciliation results (summary, list, match group detail), settlement per
date and cycle with the NTSL self-check and the contributing transactions,
the settings screens (response codes, NTSL mapping, tolerance) with the
tolerance maker-checker, the pending-approvals list, and the two Slice 2
gaps (file statuses and per-side file types in /meta/).

For each endpoint: the five roles allowed and refused, 401 anonymous, CSRF on
writes, module isolation, 404s, filters and pagination, and the audit event
the service writes. The results and settlement data come from the real
presence matrix, rule evaluator and settlement run over the synthetic day of
settlement/tests/recon_base.py (made-up RRNs and amounts).
"""
from __future__ import annotations

from datetime import date
from io import StringIO
from unittest import mock

from django.core.management import call_command

from api import settings_views
from api.tests.test_slice2 import FILES, Slice2Base
from api.tests.test_v1 import ALL_ROLES, JSON, V1, V1TestBase, csrf_client
from core.models import PaymentModule
from exception_management import code_changes
from exception_management.models import CaseType, ExceptionCase, ResponseCodeChangeEvent
from exception_management.roles import Role
from exception_management.sync import sync_cases_for_date
from file_processing.models import ProcessingStatus
from reconciliation.models import TransactionMatchGroup
from rule_engine.models import ResponseCode, RuleEvaluationRun, RuleResult
from settlement import mapping_changes
from settlement.models import (
    MapStatus,
    NtslCategoryMapVersion,
    NtslMappingEvent,
    SettlementResult,
    SettlementTolerance,
    SettlementToleranceEvent,
)
from settlement.tests.recon_base import D, SettlementTestBase, ntsl_lines

UPI = f"{V1}/upi"
DATE = f"{UPI}/dates/2026-07-14"
SETTINGS = f"{UPI}/settings"
A, O, M, C, U = Role.ADMINISTRATOR, Role.OPERATIONS_USER, Role.MAKER, Role.CHECKER, Role.AUDITOR

# RRNs of recon_base.build_day
MATCHED_REM, MATCHED_BEN, ON_US, NPCI_OK, NPCI_DECLINED, CBS_ONLY = (f"10000000000{i}" for i in range(1, 7))
ADJ_CREDIT, ADJ_DRC = "100000000008", "100000000009"


class Slice3Base(V1TestBase, SettlementTestBase):
    """V1TestBase's users and clients + SettlementTestBase's real-day builders."""

    def setUp(self):
        V1TestBase.setUp(self)
        call_command("seed_settlement_cycles", stdout=StringIO())
        call_command("seed_ntsl_category_map", stdout=StringIO())
        self._files, self._row = {}, 0

    def ok(self, who, url, params=None):
        r = self.as_(who).get(url, params or {})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def group(self, rrn, d=D):
        return TransactionMatchGroup.objects.get(payment_module=self.module, rrn=rrn, business_date=d)


# ---------------------------------------------------------------------------
# reconciliation results (4.12, 4.13)
# ---------------------------------------------------------------------------
class ResultTests(Slice3Base):
    def setUp(self):
        super().setUp()
        self.build_day()
        sync_cases_for_date(self.module, D, source="test")

    def rrns(self, params=None):
        return [r["rrn"] for r in self.ok(U, f"{DATE}/results/", params)["results"]]

    def test_read_matrix(self):
        gid = self.group(MATCHED_REM).pk
        for url in (f"{DATE}/results/", f"{DATE}/results/summary/", f"{DATE}/groups/{gid}/"):
            self.assert_read_matrix(url)

    def test_summary_counts_from_the_rule_run(self):
        body = self.ok(M, f"{DATE}/results/summary/")
        run = RuleEvaluationRun.objects.get(business_date=D)
        groups = TransactionMatchGroup.objects.filter(business_date=D, is_superseded=False).count()
        self.assertEqual(body["rule_evaluation"]["id"], run.pk)
        self.assertEqual(body["rule_evaluation"]["rule_set"], "v3.2")
        self.assertEqual(body["results_total"], groups)
        self.assertEqual({r["rule_code"]: r["count"] for r in body["rules"]}, run.rule_counts)
        self.assertEqual(sum(x["count"] for x in body["by_status_category"]), groups)
        self.assertEqual(sum(x["count"] for x in body["by_priority"]), groups)
        self.assertEqual(body["match_groups"]["total"], groups)
        self.assertEqual(sum(body["match_groups"]["by_processing_status"].values()), groups)
        self.assertEqual(body["match_groups"]["on_us"], 1)
        self.assertEqual(body["cases"]["total"], ExceptionCase.objects.filter(business_date=D).count())

    def test_list_rows_and_pagination(self):
        body = self.ok(O, f"{DATE}/results/", {"page_size": 2})
        groups = TransactionMatchGroup.objects.filter(business_date=D, is_superseded=False).count()
        self.assertEqual((body["count"], len(body["results"])), (groups, 2))
        self.assertIsNotNone(body["next"])
        row = next(r for r in self.ok(O, f"{DATE}/results/")["results"] if r["rrn"] == MATCHED_REM)
        self.assertEqual(row["cbs"], {"present": True, "amount": "1000.00", "direction": "CREDIT",
                                      "response_code": None})
        self.assertEqual((row["npci"]["present"], row["npci"]["amount"], row["npci"]["response_code"]),
                         (True, "1000.00", "00"))
        self.assertEqual(row["settlement_cycle"], "1C")
        current = RuleResult.objects.get(match_group__rrn=MATCHED_REM, is_superseded=False)
        self.assertEqual((row["rule_result"]["id"], row["rule_result"]["rule_code"]), (current.pk, current.rule_code))
        self.assertIsNone(row["case"])  # R09 reconciled: no case
        cased = ExceptionCase.objects.filter(business_date=D, case_type=CaseType.TRANSACTION).first()
        self.assertIsNotNone(cased)
        row = self.ok(O, f"{DATE}/results/", {"rrn": cased.rrn})["results"][0]
        self.assertEqual(row["case"]["id"], cased.pk)

    def test_filters(self):
        self.assertEqual(self.rrns({"presence": "CBS_ONLY"}), [CBS_ONLY])
        self.assertEqual(self.rrns({"presence": "NPCI_ONLY"}), [NPCI_OK, NPCI_DECLINED])
        self.assertEqual(self.rrns({"presence": "CBS_AND_NPCI"}), [MATCHED_REM, MATCHED_BEN, ON_US])
        self.assertEqual(self.rrns({"presence": "NEITHER"}), [ADJ_CREDIT, ADJ_DRC])
        self.assertEqual(self.rrns({"on_us": "true"}), [ON_US])
        self.assertEqual(self.rrns({"rrn": NPCI_OK}), [NPCI_OK])
        r12 = RuleResult.objects.get(match_group__rrn=NPCI_OK, is_superseded=False)
        self.assertIn(NPCI_OK, self.rrns({"rule_code": r12.rule_code}))
        self.assertTrue(all(x == r12.rule_code for x in
                            [r["rule_result"]["rule_code"] for r in self.ok(U, f"{DATE}/results/",
                                                                            {"rule_code": r12.rule_code})["results"]]))
        rows = self.ok(U, f"{DATE}/results/", {"status_category": "RECONCILED"})["results"]
        self.assertTrue(rows and all(r["rule_result"]["status_category"] == "RECONCILED" for r in rows))
        rows = self.ok(U, f"{DATE}/results/", {"priority": "HIGH,MEDIUM,LOW"})["results"]
        self.assertTrue(all(r["rule_result"]["priority"] in ("HIGH", "MEDIUM", "LOW") for r in rows))
        self.assertEqual(self.rrns({"evaluated": "false"}), [])
        self.assertEqual(self.rrns({"superseded": "true"}), [])
        self.assertEqual(self.rrns({"ordering": "-rrn", "presence": "CBS_AND_NPCI"}), [ON_US, MATCHED_BEN, MATCHED_REM])
        ranked = [r["rule_result"]["priority"] for r in self.ok(U, f"{DATE}/results/", {"ordering": "priority"})
                  ["results"]]
        order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        self.assertEqual(ranked, sorted(ranked, key=lambda p: order.get(p, 3)))
        for bad in ({"presence": "X"}, {"priority": "URGENT"}, {"status_category": "X"}, {"ordering": "amount"},
                    {"on_us": "yes"}, {"processing_status": "X"}):
            with self.subTest(bad):
                r = self.as_(U).get(f"{DATE}/results/", bad)
                self.assertEqual(r.status_code, 400)
                self.assertIn(next(iter(bad)), r.json()["errors"])

    def test_superseded_groups_are_kept_and_filterable(self):
        g = self.group(NPCI_DECLINED)
        TransactionMatchGroup.objects.filter(pk=g.pk).update(is_superseded=True)
        self.assertNotIn(NPCI_DECLINED, self.rrns())
        self.assertEqual(self.rrns({"superseded": "true"}), [NPCI_DECLINED])

    def test_group_detail(self):
        g = self.group(MATCHED_REM)
        body = self.ok(C, f"{DATE}/groups/{g.pk}/")
        self.assertEqual((body["rrn"], body["processing_status"], body["flags"]["amount_match"]),
                         (MATCHED_REM, g.processing_status, True))
        legs = {x["leg"]: x for x in body["legs"]}
        self.assertEqual(legs["CBS"]["fact"]["register_type"], "CBS_GL_EXTRACT")
        self.assertEqual((legs["NPCI"]["response_code"], legs["NPCI"]["fact"]["settlement_cycle"]), ("00", "1C"))
        self.assertEqual(body["rule_result"]["rule_set"], "v3.2")
        self.assertEqual(body["earlier_rule_results"], [])
        self.assertIsNone(body["case"])
        # A re-run supersedes, never deletes: the earlier result is listed.
        self.evaluate()
        body = self.ok(C, f"{DATE}/groups/{g.pk}/")
        self.assertEqual(len(body["earlier_rule_results"]), 1)
        self.assertTrue(body["earlier_rule_results"][0]["is_superseded"])
        self.assertFalse(body["rule_result"]["is_superseded"])

    def test_group_detail_overlay_rows_and_case(self):
        g = self.group(ADJ_CREDIT)
        body = self.ok(M, f"{DATE}/groups/{g.pk}/")
        self.assertEqual([(x["leg"], x["present"]) for x in body["legs"]],
                         [("CBS", False), ("NPCI", False), ("ADJUSTMENT", True)])
        self.assertEqual([(r["leg"], r["dispute_type"]) for r in body["overlay_rows"]],
                         [("ADJUSTMENT", "Credit Adjustment")])
        case = ExceptionCase.objects.filter(business_date=D, case_type=CaseType.TRANSACTION).first()
        body = self.ok(M, f"{DATE}/groups/{case.match_group_id or self.group(case.rrn).pk}/")
        self.assertEqual(body["case"]["id"], case.pk)

    def test_404s_and_module_isolation(self):
        g = self.group(MATCHED_REM)
        self.assertEqual(self.as_(U).get(f"{UPI}/dates/2026-07-15/results/").status_code, 404)
        self.assertEqual(self.as_(U).get(f"{UPI}/dates/2026-07-15/results/summary/").status_code, 404)
        self.assertEqual(self.as_(U).get(f"{UPI}/dates/2026-07-15/groups/{g.pk}/").status_code, 404)  # other date
        self.assertEqual(self.as_(U).get(f"{DATE}/groups/999999/").status_code, 404)
        self.assertEqual(self.as_(U).get(f"{UPI}/dates/14-07-2026/results/").status_code, 400)
        PaymentModule.objects.create(code="IMPS", name="IMPS")
        self.assertEqual(self.as_(U).get(f"{V1}/imps/dates/2026-07-14/results/").status_code, 404)
        self.assertEqual(self.as_(U).get(f"{V1}/imps/dates/2026-07-14/groups/{g.pk}/").status_code, 404)
        self.assertEqual(self.as_(U).get(f"{V1}/nope/dates/2026-07-14/results/").status_code, 404)
        self.assertEqual(self.as_(None).get(f"{V1}/nope/dates/2026-07-14/results/").status_code, 401)


# ---------------------------------------------------------------------------
# settlement (4.14, 4.15)
# ---------------------------------------------------------------------------
class SettlementTests(Slice3Base):
    def setUp(self):
        super().setUp()
        self.build_day()
        self.outcome = self.reconcile()

    def test_read_matrix(self):
        for url in (f"{DATE}/settlement/", f"{DATE}/settlement/transactions/"):
            self.assert_read_matrix(url)

    def test_tallied_date_with_self_check(self):
        body = self.ok(O, f"{DATE}/settlement/")
        r = body["result"]
        self.assertEqual((r["result_id"], r["status"], r["result_is_current"]), (self.outcome.result.pk, "TALLIED", True))
        self.assertEqual(r["sop"]["unexplained"], "0.00")
        self.assertEqual(r["unexplained_amount"], "0.00")
        self.assertEqual(r["tolerance"], {"amount": "0.00", "txn_count": 0})
        self.assertEqual(r["tolerance_version"], 1)
        self.assertTrue(r["cycles"])
        self.assertTrue(all(c["within_tolerance"] for c in r["cycles"]))
        sc = body["self_check"]
        self.assertEqual((sc["cycles"], sc["pass"], sc["fail"], sc["missing"], sc["all_pass"]), (12, 12, 0, 0, True))
        one = next(c for c in sc["results"] if c["cycle"] == "1C")
        self.assertEqual((one["status"], one["unmapped_count"], one["final_net"]["difference"]), ("PASS", 0, "0.00"))
        self.assertIsNone(body["case"])  # TALLIED: no settlement case
        self.assertEqual(body["earlier_results"], [])
        self.assertEqual(r["transaction_counts"], {k: self.ok(O, f"{DATE}/settlement/transactions/", {"kind": k})
                                                   ["count"] for k in ("npci", "sop", "adjustments", "z")})

    def test_contributing_transactions(self):
        url = f"{DATE}/settlement/transactions/"
        npci = self.ok(M, url)
        self.assertEqual(npci["kind"], "npci")
        self.assertIn("g_rule", npci["fields"])
        self.assertIn(NPCI_OK, [x["rrn"] for x in npci["results"]])  # R12 NPCI only: G05
        self.assertEqual({x["g_rule"] for x in self.ok(M, url, {"g_rule": "G05"})["results"]}, {"G05"})
        sop = self.ok(M, url, {"kind": "sop"})
        self.assertEqual(sop["fields"], ["rrn", "rule", "g_rule", "amount"])
        self.assertEqual({x["rrn"] for x in sop["results"]}, {NPCI_OK, CBS_ONLY})
        self.assertEqual(self.ok(M, url, {"kind": "sop", "rrn": CBS_ONLY})["results"][0]["amount"], "5.00")
        adj = self.ok(M, url, {"kind": "adjustments"})
        self.assertEqual([(x["rrn"], x["cycle"], x["signed"]) for x in adj["results"]], [(ADJ_CREDIT, "DC1", "35.00")])
        z = self.ok(M, url, {"kind": "z", "page_size": 1})
        self.assertLessEqual(len(z["results"]), 1)
        self.assertEqual(z["counts"], npci["counts"])
        for bad in ({"kind": "all"}, {"side": "LEFT"}):
            self.assertEqual(self.as_(M).get(url, bad).status_code, 400)

    def test_unexplained_date_case_and_case_transactions(self):
        self.load_ntsl("1C", ntsl_lines("1070", 3, "750", 2))
        outcome = self.reconcile()
        body = self.ok(C, f"{DATE}/settlement/")
        self.assertEqual(body["result"]["status"], "UNEXPLAINED")
        self.assertTrue(body["result"]["reasons"])
        self.assertEqual(body["case"]["id"], outcome.case_id)
        self.assertEqual([e["id"] for e in body["earlier_results"]], [self.outcome.result.pk])
        url = f"{UPI}/cases/{outcome.case_id}/settlement/transactions/"
        self.assert_read_matrix(url)
        tx = self.ok(M, url, {"kind": "sop"})
        self.assertEqual(tx["result_id"], outcome.result.pk)
        self.assertEqual({x["rrn"] for x in tx["results"]}, {NPCI_OK, CBS_ONLY})
        # The case detail's settlement block is unchanged by the refactor.
        detail = self.ok(M, f"{UPI}/cases/{outcome.case_id}/")["settlement"]
        self.assertEqual((detail["result_id"], detail["first_result_id"], detail["status"]),
                         (outcome.result.pk, outcome.result.pk, "UNEXPLAINED"))
        # A transaction case has no settlement transactions.
        sync_cases_for_date(self.module, D, source="test")
        tx_case = ExceptionCase.objects.filter(case_type=CaseType.TRANSACTION).first()
        self.assertEqual(self.as_(M).get(f"{UPI}/cases/{tx_case.pk}/settlement/transactions/").status_code, 404)

    def test_404s_and_module_isolation(self):
        self.assertEqual(self.as_(U).get(f"{UPI}/dates/2026-07-15/settlement/").status_code, 404)
        self.assertEqual(self.as_(U).get(f"{UPI}/dates/2026-07-15/settlement/transactions/").status_code, 404)
        self.assertEqual(self.as_(U).get(f"{UPI}/dates/x/settlement/").status_code, 400)
        self.assertEqual(self.as_(U).get(f"{UPI}/cases/999999/settlement/transactions/").status_code, 404)
        PaymentModule.objects.create(code="IMPS", name="IMPS")
        self.assertEqual(self.as_(U).get(f"{V1}/imps/dates/2026-07-14/settlement/").status_code, 404)


class SettlementNotReadyTests(Slice3Base):
    def test_not_ready_result_has_reasons_and_no_transactions(self):
        self.load_all_ntsl()
        self.reconcile()  # no presence matrix / rule results yet
        body = self.ok(U, f"{DATE}/settlement/")
        self.assertEqual(body["result"]["status"], "NOT_READY")
        self.assertTrue(any("presence matrix" in x for x in body["result"]["reasons"]))
        self.assertEqual(body["self_check"]["pass"], 12)
        tx = self.ok(U, f"{DATE}/settlement/transactions/")
        self.assertEqual((tx["count"], tx["result_status"]), (0, "NOT_READY"))


# ---------------------------------------------------------------------------
# settings: response codes and NTSL mapping (read), pending approvals
# ---------------------------------------------------------------------------
class SettingsReadTests(Slice3Base):
    def test_read_matrix(self):
        v1 = NtslCategoryMapVersion.objects.get(payment_module=self.module, version=1)
        for url in (f"{SETTINGS}/response-codes/", f"{SETTINGS}/response-codes/requests/", f"{SETTINGS}/ntsl-mapping/",
                    f"{SETTINGS}/ntsl-mapping/versions/", f"{SETTINGS}/ntsl-mapping/versions/{v1.pk}/",
                    f"{SETTINGS}/ntsl-mapping/versions/{v1.pk}/entries/", f"{SETTINGS}/tolerance/",
                    f"{SETTINGS}/tolerance/versions/", f"{SETTINGS}/pending/"):
            self.assert_read_matrix(url)

    def test_response_codes(self):
        body = self.ok(U, f"{SETTINGS}/response-codes/", {"page_size": 500})
        self.assertEqual(body["count"], ResponseCode.objects.filter(payment_module=self.module).count())
        self.assertEqual(body["rule_set"], "v3.2")
        drc102 = next(c for c in body["results"] if c["flag"] == "DRC" and c["code"] == "102")
        self.assertTrue(drc102["locked"])
        self.assertIn("R01", drc102["used_by"])
        self.assertEqual({c["flag"] for c in self.ok(U, f"{SETTINGS}/response-codes/", {"flag": "RET"})["results"]},
                         {"RET"})
        unknown = self.ok(U, f"{SETTINGS}/response-codes/", {"known": "false"})["results"]
        self.assertTrue(unknown and all(c["code"] is None for c in unknown))
        self.assertEqual(self.as_(U).get(f"{SETTINGS}/response-codes/", {"flag": "XYZ"}).status_code, 400)

    def test_code_request_history_and_pending(self):
        rc = ResponseCode.objects.filter(payment_module=self.module, flag="RET", code__isnull=True).first()
        maker = self.users[M]
        req = code_changes.propose_code_change(rc, maker, new_code="116", reason="NPCI circular (synthetic)")
        row = next(c for c in self.ok(C, f"{SETTINGS}/response-codes/", {"flag": "RET", "page_size": 500})["results"]
                   if c["id"] == rc.pk)
        self.assertEqual(row["pending_request_id"], req.pk)
        body = self.ok(M, f"{SETTINGS}/response-codes/requests/", {"status": "PENDING"})
        r = body["results"][0]
        self.assertEqual((r["id"], r["changes"], r["requested_by"], r["self_approval"]),
                         (req.pk, {"code": [None, "116"]}, maker.username, True))
        self.assertEqual([e["event_type"] for e in r["events"]], ["PROPOSED"])
        self.assertEqual(self.ok(C, f"{SETTINGS}/response-codes/requests/")["results"][0]["self_approval"], False)
        self.assertEqual(self.ok(C, f"{SETTINGS}/response-codes/requests/", {"status": "APPROVED"})["count"], 0)
        self.assertEqual(self.as_(C).get(f"{SETTINGS}/response-codes/requests/", {"status": "X"}).status_code, 400)
        self.assertEqual(ResponseCodeChangeEvent.objects.filter(change_request=req).count(), 1)

    def test_ntsl_mapping(self):
        body = self.ok(U, f"{SETTINGS}/ntsl-mapping/")
        v1 = NtslCategoryMapVersion.objects.get(payment_module=self.module, version=1)
        self.assertEqual((body["active"]["id"], body["active"]["status"], body["pending"]),
                         (v1.pk, "ACTIVE", None))
        self.assertEqual(body["active"]["entry_count"], v1.entries.count())
        self.assertTrue(any(c["value"] == "TXN_REMITTER" for c in body["categories"]))
        entries = self.ok(U, f"{SETTINGS}/ntsl-mapping/versions/{v1.pk}/entries/", {"category": "FINAL"})
        self.assertTrue(entries["count"] >= 1 and all(e["category"] == "FINAL" for e in entries["results"]))
        self.assertEqual(self.as_(U).get(f"{SETTINGS}/ntsl-mapping/versions/{v1.pk}/entries/",
                                         {"category": "X"}).status_code, 400)
        maker = self.users[M]
        v2 = mapping_changes.propose_mapping_change(
            self.module, maker, reason="synthetic", add=[{"match_type": "EXACT", "pattern": "Synthetic line",
                                                          "category": "FEE"}])
        body = self.ok(M, f"{SETTINGS}/ntsl-mapping/")
        self.assertEqual((body["pending"]["id"], body["pending"]["change_counts"]["added"],
                          body["pending"]["self_approval"]), (v2.pk, 1, True))
        self.assertEqual([v["version"] for v in self.ok(U, f"{SETTINGS}/ntsl-mapping/versions/")["results"]], [2, 1])
        detail = self.ok(U, f"{SETTINGS}/ntsl-mapping/versions/{v2.pk}/")
        self.assertEqual(detail["change_summary"]["added"][0]["pattern"], "Synthetic line")
        self.assertEqual([e["event_type"] for e in detail["events"]], ["PROPOSED"])
        self.assertEqual(NtslMappingEvent.objects.filter(version=v2).count(), 1)
        self.assertEqual(self.as_(U).get(f"{SETTINGS}/ntsl-mapping/versions/999999/").status_code, 404)
        imps = PaymentModule.objects.create(code="IMPS", name="IMPS")
        self.assertEqual(self.as_(U).get(f"{V1}/imps/settings/ntsl-mapping/versions/{v2.pk}/").status_code, 404)
        self.assertIsNone(self.ok(U, f"{V1}/imps/settings/ntsl-mapping/")["active"])
        self.assertIsNotNone(imps)

    def test_pending_approvals_across_settings(self):
        maker, admin = self.users[M], self.users[A]
        rc = ResponseCode.objects.filter(payment_module=self.module, flag="RET", code__isnull=True).first()
        req = code_changes.propose_code_change(rc, maker, new_code="116", reason="synthetic")
        v2 = mapping_changes.propose_mapping_change(
            self.module, maker, reason="synthetic", add=[{"match_type": "EXACT", "pattern": "X", "category": "FEE"}])
        r = self.post(self.as_(admin), f"{SETTINGS}/tolerance/", {"amount": "1.00", "txn_count": 0, "reason": "r"})
        self.assertEqual(r.status_code, 201)
        body = self.ok(C, f"{SETTINGS}/pending/")
        self.assertEqual([(i["kind"], i["id"]) for i in body["results"]],
                         [("response_code", req.pk), ("ntsl_mapping", v2.pk), ("tolerance", r.json()["id"])])
        self.assertTrue(body["can_decide"])
        self.assertEqual(body["cases_awaiting_approval"]["count"], 0)
        self.assertEqual([i["self_approval"] for i in body["results"]], [False, False, False])
        self.assertEqual([i["self_approval"] for i in self.ok(M, f"{SETTINGS}/pending/")["results"]],
                         [True, True, False])
        self.assertFalse(self.ok(M, f"{SETTINGS}/pending/")["can_decide"])
        self.assertEqual([i["kind"] for i in self.ok(U, f"{SETTINGS}/pending/", {"kind": "tolerance"})["results"]],
                         ["tolerance"])
        self.assertEqual(self.ok(U, f"{SETTINGS}/pending/", {"page_size": 2})["count"], 3)
        self.assertEqual(self.as_(U).get(f"{SETTINGS}/pending/", {"kind": "cases"}).status_code, 400)
        # Another module's pending list is its own.
        PaymentModule.objects.create(code="IMPS", name="IMPS")
        self.assertEqual(self.ok(U, f"{V1}/imps/settings/pending/")["count"], 0)


# ---------------------------------------------------------------------------
# tolerance maker-checker (4.16 c)
# ---------------------------------------------------------------------------
class ToleranceTests(Slice3Base):
    url = f"{SETTINGS}/tolerance/"

    def propose(self, who=A, **data):
        body = {"amount": "1.00", "txn_count": 1, "reason": "Ops asked for Rs 1 rounding (synthetic)", **data}
        return self.post(self.as_(who), self.url, body)

    def decide(self, who, pk, decision="approve", **data):
        return self.post(self.as_(who), f"{self.url}{pk}/{decision}/", data)

    def test_read_does_not_seed(self):
        body = self.ok(U, self.url)
        self.assertEqual((body["active"], body["pending"], body["default_if_none"]),
                         (None, None, {"amount": "0.00", "txn_count": 0}))
        self.assertFalse(SettlementTolerance.objects.exists())
        self.assertEqual((body["can_propose"], body["can_decide"]), (False, False))
        self.assertTrue(self.ok(A, self.url)["can_propose"])
        self.assertTrue(self.ok(C, self.url)["can_decide"])

    def test_propose_roles_csrf_and_audit(self):
        for role in ALL_ROLES:
            if role != A:
                with self.subTest(role=role):
                    self.assertEqual(self.propose(role).status_code, 403)
        self.assertEqual(self.propose(self.norole).status_code, 403)
        self.assertEqual(self.propose(None).status_code, 401)
        c, token = csrf_client(self.users[A])
        body = {"amount": "1.00", "txn_count": 1, "reason": "x"}
        self.assertEqual(c.post(self.url, body, content_type=JSON).status_code, 403)
        self.assertFalse(SettlementTolerance.objects.filter(status=MapStatus.PENDING).exists())
        r = c.post(self.url, body, content_type=JSON, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 201, r.content)
        t = SettlementTolerance.objects.get(pk=r.json()["id"])
        self.assertEqual((t.status, t.version, str(t.amount), t.txn_count, t.proposed_by),
                         (MapStatus.PENDING, 2, "1.00", 1, self.users[A]))
        self.assertEqual([e["event_type"] for e in r.json()["events"]], ["PROPOSED"])
        ev = SettlementToleranceEvent.objects.get(tolerance=t)
        self.assertEqual((ev.actor, ev.changes), (self.users[A], {"amount": ["0.00", "1.00"], "txn_count": [0, 1]}))
        body = self.ok(U, self.url)
        self.assertEqual((body["active"]["version"], body["pending"]["id"]), (1, t.pk))

    def test_propose_refusals(self):
        self.assertEqual(self.propose(amount="0.00", txn_count=0).status_code, 400)  # changes nothing
        self.assertEqual(self.propose(amount="-1").status_code, 400)
        self.assertEqual(self.propose(amount="abc").status_code, 400)
        self.assertEqual(self.propose(txn_count="1.5").status_code, 400)
        self.assertEqual(self.propose(reason="").status_code, 400)
        self.assertEqual(self.propose(reason=5).status_code, 400)
        self.assertEqual(self.propose().status_code, 201)
        r = self.propose(amount="2.00")
        self.assertEqual(r.status_code, 409)  # one pending at a time
        self.assertIn("pending", r.json()["detail"])

    def test_checker_approves_and_it_is_audited(self):
        pk = self.propose().json()["id"]
        for role in (A, O, M, U):
            with self.subTest(role=role):
                self.assertEqual(self.decide(role, pk).status_code, 403)
        self.assertEqual(self.decide(None, pk).status_code, 401)
        c, _ = csrf_client(self.users[C])
        self.assertEqual(c.post(f"{self.url}{pk}/approve/", {}, content_type=JSON).status_code, 403)
        r = self.decide(C, pk, remarks="ok")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body["status"], body["decided_by"], body["self_approved"], body["decision_remarks"]),
                         ("ACTIVE", self.users[C].username, False, "ok"))
        self.assertEqual([e["event_type"] for e in body["events"]], ["PROPOSED", "APPROVED"])
        old = SettlementTolerance.objects.get(version=1)
        self.assertEqual(old.status, MapStatus.RETIRED)
        self.assertEqual(self.ok(U, self.url)["active"]["amount"], "1.00")
        self.assertEqual(self.decide(C, pk).status_code, 409)  # already decided
        self.assertEqual([v["status"] for v in self.ok(U, f"{self.url}versions/")["results"]], ["ACTIVE", "RETIRED"])
        self.assertEqual(self.ok(U, f"{self.url}versions/", {"status": "RETIRED"})["count"], 1)

    def test_self_approval_needs_confirmation(self):
        both = self.user(A, C)
        pk = self.propose(both).json()["id"]
        self.assertTrue(self.ok(both, self.url)["pending"]["self_approval"])
        r = self.decide(both, pk)
        self.assertEqual(r.status_code, 400)
        self.assertIn("confirm_self_approval", r.json()["errors"])
        self.assertEqual(SettlementTolerance.objects.get(pk=pk).status, MapStatus.PENDING)
        self.assertFalse(SettlementToleranceEvent.objects.filter(tolerance_id=pk, event_type="APPROVED").exists())
        r = self.decide(both, pk, confirm_self_approval=True)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["self_approved"])
        self.assertTrue(SettlementToleranceEvent.objects.get(tolerance_id=pk, event_type="APPROVED").self_approved)

    def test_reject(self):
        pk = self.propose().json()["id"]
        self.assertEqual(self.decide(M, pk, "reject", reason="no").status_code, 403)
        self.assertEqual(self.decide(C, pk, "reject").status_code, 400)  # reason required
        r = self.decide(C, pk, "reject", reason="Rs 0 stays (synthetic)")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["status"], r.json()["decision_remarks"]), ("REJECTED", "Rs 0 stays (synthetic)"))
        self.assertEqual(self.decide(C, pk).status_code, 409)
        self.assertEqual(self.ok(U, self.url)["active"]["version"], 1)

    def test_404s_and_module_isolation(self):
        pk = self.propose().json()["id"]
        self.assertEqual(self.decide(C, pk, "delete").status_code, 404)
        self.assertEqual(self.decide(C, 999999).status_code, 404)
        self.assertEqual(self.as_(U).get(f"{self.url}versions/999999/").status_code, 404)
        self.assertEqual(self.ok(U, f"{self.url}versions/{pk}/")["status"], "PENDING")
        PaymentModule.objects.create(code="IMPS", name="IMPS")
        self.assertEqual(self.post(self.as_(C), f"{V1}/imps/settings/tolerance/{pk}/approve/").status_code, 404)
        self.assertEqual(self.as_(U).get(f"{V1}/imps/settings/tolerance/versions/{pk}/").status_code, 404)
        self.assertEqual(SettlementTolerance.objects.get(pk=pk).status, MapStatus.PENDING)


class ToleranceStaleDatesTests(Slice3Base):
    """Q4 (owner 2026-10-20): no automatic re-run after a tolerance approval;
    the approve response and the read list the dates whose current settlement
    result used an older tolerance version."""

    url = f"{SETTINGS}/tolerance/"

    def setUp(self):
        super().setUp()
        self.build_day()
        self.first = self.reconcile().result  # computed with v1 (Rs 0 / 0)

    def approve_v2(self):
        pk = self.post(self.as_(A), self.url, {"amount": "2.00", "txn_count": 1, "reason": "synthetic"}).json()["id"]
        r = self.post(self.as_(C), f"{self.url}{pk}/approve/", {})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_listed_after_approval_and_cleared_by_a_run(self):
        self.assertEqual(self.ok(U, self.url)["dates_with_older_tolerance"]["count"], 0)
        block = self.approve_v2()["dates_with_older_tolerance"]
        self.assertEqual(block["count"], 1)
        row = block["dates"][0]
        self.assertEqual((row["business_date"], row["result_id"], row["settlement_status"], row["tolerance_version"],
                          row["tolerance"]), ("2026-07-14", self.first.pk, "TALLIED", 1,
                                              {"amount": "0.00", "txn_count": 0}))
        self.assertEqual(self.ok(M, self.url)["dates_with_older_tolerance"]["dates"], block["dates"])
        # Nothing was re-run: the date's current result is still the v1 one.
        self.assertEqual(SettlementResult.objects.get(business_date=D, is_current=True).pk, self.first.pk)
        self.reconcile()  # the user runs the date again
        self.assertEqual(self.ok(U, self.url)["dates_with_older_tolerance"]["count"], 0)

    def test_reject_has_no_list_and_archived_dates_are_left_out(self):
        pk = self.post(self.as_(A), self.url, {"amount": "2.00", "txn_count": 1, "reason": "synthetic"}).json()["id"]
        r = self.post(self.as_(C), f"{self.url}{pk}/reject/", {"reason": "no"})
        self.assertNotIn("dates_with_older_tolerance", r.json())
        self.assertEqual(self.ok(U, self.url)["dates_with_older_tolerance"]["count"], 0)  # v1 still active
        self.assertEqual(settings_views._archived_dates(self.module, [D]), set())
        with mock.patch.object(settings_views, "_archived_dates", return_value={D}):
            self.assertEqual(self.approve_v2()["dates_with_older_tolerance"]["count"], 0)


# ---------------------------------------------------------------------------
# response-code maker-checker (4.16 a; owner 2026-10-20 Q1/Q2)
# ---------------------------------------------------------------------------
class ResponseCodeWriteTests(Slice3Base):
    url = f"{SETTINGS}/response-codes/requests/"

    def setUp(self):
        super().setUp()
        self.rc = ResponseCode.objects.filter(payment_module=self.module, flag="RET", code__isnull=True).first()

    def edit(self, who=M, **data):
        body = {"kind": "EDIT", "response_code": self.rc.pk, "new_code": "116", "reason": "NPCI circular (synthetic)",
                **data}
        return self.post(self.as_(who), self.url, body)

    def create(self, who=M, **data):
        body = {"kind": "CREATE", "flag": "RET", "new_description": "Synthetic reason", "new_code": "999",
                "new_source": "synthetic circular", "reason": "synthetic", **data}
        return self.post(self.as_(who), self.url, body)

    def decide(self, who, pk, decision="approve", **data):
        return self.post(self.as_(who), f"{self.url}{pk}/{decision}/", data)

    def test_propose_roles_csrf_and_audit(self):
        for role in (A, O, C, U):
            with self.subTest(role=role):
                self.assertEqual(self.edit(role).status_code, 403)
        self.assertEqual(self.edit(self.norole).status_code, 403)
        self.assertEqual(self.edit(None).status_code, 401)
        c, token = csrf_client(self.users[M])
        body = {"kind": "EDIT", "response_code": self.rc.pk, "new_code": "116", "reason": "x"}
        self.assertEqual(c.post(self.url, body, content_type=JSON).status_code, 403)
        r = c.post(self.url, body, content_type=JSON, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 201, r.content)
        b = r.json()
        self.assertEqual((b["kind"], b["status"], b["changes"], b["requested_by"], b["self_approval"]),
                         ("EDIT", "PENDING", {"code": [None, "116"]}, self.users[M].username, True))
        self.assertEqual([e["event_type"] for e in b["events"]], ["PROPOSED"])
        self.assertEqual(ResponseCodeChangeEvent.objects.get(change_request_id=b["id"]).actor, self.users[M])
        self.rc.refresh_from_db()
        self.assertIsNone(self.rc.code)  # nothing applied before approval

    def test_propose_refusals(self):
        self.assertEqual(self.edit(kind="DELETE").status_code, 400)
        self.assertEqual(self.edit(response_code="x").status_code, 400)
        self.assertEqual(self.edit(response_code=999999).status_code, 400)
        self.assertIn("flag", self.edit(flag="RET").json()["errors"])
        self.assertIn("response_code", self.create(response_code=self.rc.pk).json()["errors"])
        self.assertEqual(self.edit(new_code=116).status_code, 400)  # not text
        self.assertEqual(self.edit(reason="").status_code, 400)  # service: reason required
        self.assertEqual(self.edit(new_code=None).status_code, 400)  # service: changes nothing
        locked = ResponseCode.objects.filter(payment_module=self.module, flag="DRC", code="102").get()
        r = self.edit(response_code=locked.pk, new_code=None, new_description="Renamed")
        self.assertEqual(r.status_code, 400)  # locked by the active rule set
        self.assertIn("ACTIVE/RETIRED", r.json()["detail"])
        self.assertEqual(self.create(flag="XYZ").status_code, 400)
        self.assertEqual(self.create(new_source="").status_code, 400)
        self.assertEqual(self.edit().status_code, 201)
        self.assertEqual(self.edit(new_code="117").status_code, 409)  # one pending per code
        imps = PaymentModule.objects.create(code="IMPS", name="IMPS")
        r = self.post(self.as_(M), f"{V1}/imps/settings/response-codes/requests/",
                      {"kind": "EDIT", "response_code": self.rc.pk, "new_code": "118", "reason": "x"})
        self.assertEqual(r.status_code, 400)  # another module's code
        self.assertIsNotNone(imps)

    def test_checker_approves_edit_and_it_is_applied(self):
        pk = self.edit().json()["id"]
        for role in (A, O, M, U):
            with self.subTest(role=role):
                self.assertEqual(self.decide(role, pk).status_code, 403)
        self.assertEqual(self.decide(None, pk).status_code, 401)
        c, _ = csrf_client(self.users[C])
        self.assertEqual(c.post(f"{self.url}{pk}/approve/", {}, content_type=JSON).status_code, 403)
        r = self.decide(C, pk, remarks="ok")
        self.assertEqual(r.status_code, 200, r.content)
        b = r.json()
        self.assertEqual((b["status"], b["decided_by"], b["self_approved"], b["decision_remarks"]),
                         ("APPROVED", self.users[C].username, False, "ok"))
        self.assertEqual([e["event_type"] for e in b["events"]], ["PROPOSED", "APPROVED"])
        self.rc.refresh_from_db()
        self.assertEqual(self.rc.code, "116")
        self.assertEqual(self.decide(C, pk).status_code, 409)

    def test_create_approved_creates_the_code(self):
        pk = self.create().json()["id"]
        self.assertEqual(self.create().status_code, 409)  # same code already pending
        r = self.decide(C, pk)
        self.assertEqual(r.status_code, 200, r.content)
        rc = ResponseCode.objects.get(payment_module=self.module, flag="RET", code="999")
        self.assertEqual((rc.description, rc.source, r.json()["response_code_id"]),
                         ("Synthetic reason", "synthetic circular", rc.pk))

    def test_self_approval_needs_confirmation(self):
        both = self.user(M, C)
        pk = self.edit(both).json()["id"]
        r = self.decide(both, pk)
        self.assertEqual(r.status_code, 400)
        self.assertIn("confirm_self_approval", r.json()["errors"])
        self.assertFalse(ResponseCodeChangeEvent.objects.filter(change_request_id=pk, event_type="APPROVED").exists())
        r = self.decide(both, pk, confirm_self_approval=True)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["self_approved"])
        self.assertTrue(ResponseCodeChangeEvent.objects.get(change_request_id=pk, event_type="APPROVED").self_approved)

    def test_reject_and_404s(self):
        pk = self.edit().json()["id"]
        self.assertEqual(self.decide(C, pk, "reject").status_code, 400)  # reason required
        self.assertEqual(self.decide(C, pk, "delete").status_code, 404)
        self.assertEqual(self.decide(C, 999999).status_code, 404)
        PaymentModule.objects.create(code="IMPS", name="IMPS")
        self.assertEqual(self.post(self.as_(C), f"{V1}/imps/settings/response-codes/requests/{pk}/approve/")
                         .status_code, 404)
        r = self.decide(C, pk, "reject", reason="not in the circular (synthetic)")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["status"], r.json()["decision_remarks"]),
                         ("REJECTED", "not in the circular (synthetic)"))
        self.rc.refresh_from_db()
        self.assertIsNone(self.rc.code)
        self.assertEqual(self.ok(U, f"{SETTINGS}/pending/", {"kind": "response_code"})["count"], 0)


# ---------------------------------------------------------------------------
# NTSL mapping maker-checker (4.16 b; owner 2026-10-20 Q1/Q2)
# ---------------------------------------------------------------------------
class NtslMappingWriteTests(Slice3Base):
    url = f"{SETTINGS}/ntsl-mapping/"
    ENTRY = {"match_type": "EXACT", "pattern": "Synthetic fee line", "category": "FEE"}

    def propose(self, who=M, **data):
        return self.post(self.as_(who), self.url, {"add": [self.ENTRY], "reason": "synthetic", **data})

    def decide(self, who, pk, decision="approve", **data):
        return self.post(self.as_(who), f"{self.url}{pk}/{decision}/", data)

    def v1(self):
        return NtslCategoryMapVersion.objects.get(payment_module=self.module, version=1)

    def test_propose_roles_csrf_and_audit(self):
        for role in (A, O, C, U):
            with self.subTest(role=role):
                self.assertEqual(self.propose(role).status_code, 403)
        self.assertEqual(self.propose(None).status_code, 401)
        c, token = csrf_client(self.users[M])
        body = {"add": [self.ENTRY], "reason": "x"}
        self.assertEqual(c.post(self.url, body, content_type=JSON).status_code, 403)
        r = c.post(self.url, body, content_type=JSON, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 201, r.content)
        b = r.json()
        self.assertEqual((b["version"], b["status"], b["change_counts"]["added"], b["self_approval"]),
                         (2, "PENDING", 1, True))
        self.assertEqual(b["change_summary"]["added"][0]["pattern"], "Synthetic fee line")
        self.assertEqual([e["event_type"] for e in b["events"]], ["PROPOSED"])
        self.assertEqual(NtslMappingEvent.objects.get(version_id=b["id"]).actor, self.users[M])
        self.assertEqual(self.v1().status, MapStatus.ACTIVE)
        self.assertTrue(self.ok(M, self.url)["can_propose"])
        self.assertFalse(self.ok(M, self.url)["can_decide"])

    def test_propose_refusals(self):
        self.assertIn("add", self.propose(add="x").json()["errors"])
        self.assertIn("add", self.propose(add=["x"]).json()["errors"])
        self.assertIn("add[0]", self.propose(add=[{**self.ENTRY, "priority": "high"}]).json()["errors"])
        self.assertIn("add[0]", self.propose(add=[{**self.ENTRY, "colour": "red"}]).json()["errors"])
        self.assertIn("add[0]", self.propose(add=[{**self.ENTRY, "pattern": 5}]).json()["errors"])
        self.assertIn("remove_entry_ids", self.propose(remove_entry_ids=["1"]).json()["errors"])
        self.assertEqual(self.propose(add=[]).status_code, 400)  # service: changes nothing
        self.assertEqual(self.propose(add=[{**self.ENTRY, "category": "X"}]).status_code, 400)
        self.assertEqual(self.propose(add=[{"match_type": "REGEX", "pattern": "(", "category": "FEE"}]).status_code, 400)
        self.assertEqual(self.propose(remove_entry_ids=[999999]).status_code, 400)
        self.assertEqual(self.propose(reason="").status_code, 400)
        # Removing every entry leaves the loaded NTSL descriptions unmapped.
        self.load_all_ntsl()
        r = self.propose(remove_entry_ids=list(self.v1().entries.values_list("pk", flat=True)))
        self.assertEqual(r.status_code, 400)
        self.assertIn("unmapped", r.json()["detail"])
        self.assertEqual(self.propose().status_code, 201)
        self.assertEqual(self.propose(add=[{**self.ENTRY, "pattern": "Other"}]).status_code, 409)  # one pending

    def test_checker_approves_and_it_becomes_active(self):
        entry = self.v1().entries.order_by("id").first()
        pk = self.propose(remove_entry_ids=[entry.pk], add=[self.ENTRY, {**entry.as_dict(), "notes": "re-added"}]
                          ).json()["id"]
        for role in (A, O, M, U):
            with self.subTest(role=role):
                self.assertEqual(self.decide(role, pk).status_code, 403)
        r = self.decide(C, pk, remarks="ok")
        self.assertEqual(r.status_code, 200, r.content)
        b = r.json()
        self.assertEqual((b["status"], b["decided_by"], b["self_approved"]), ("ACTIVE", self.users[C].username, False))
        self.assertEqual([e["event_type"] for e in b["events"]], ["PROPOSED", "APPROVED"])
        self.assertEqual(self.v1().status, MapStatus.RETIRED)
        self.assertEqual(self.ok(U, self.url)["active"]["id"], pk)
        self.assertEqual(self.decide(C, pk).status_code, 409)

    def test_self_approval_needs_confirmation(self):
        both = self.user(M, C)
        pk = self.propose(both).json()["id"]
        r = self.decide(both, pk)
        self.assertEqual(r.status_code, 400)
        self.assertIn("confirm_self_approval", r.json()["errors"])
        self.assertEqual(NtslCategoryMapVersion.objects.get(pk=pk).status, MapStatus.PENDING)
        r = self.decide(both, pk, confirm_self_approval=True)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["self_approved"])
        self.assertTrue(NtslMappingEvent.objects.get(version_id=pk, event_type="APPROVED").self_approved)

    def test_reject_and_404s(self):
        pk = self.propose().json()["id"]
        self.assertEqual(self.decide(C, pk, "reject").status_code, 400)
        self.assertEqual(self.decide(C, pk, "delete").status_code, 404)
        self.assertEqual(self.decide(C, 999999).status_code, 404)
        self.assertEqual(self.decide(C, self.v1().pk).status_code, 409)  # the active version is not pending
        PaymentModule.objects.create(code="IMPS", name="IMPS")
        self.assertEqual(self.post(self.as_(C), f"{V1}/imps/settings/ntsl-mapping/{pk}/approve/").status_code, 404)
        r = self.decide(C, pk, "reject", reason="no (synthetic)")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "REJECTED"))
        self.assertEqual(self.v1().status, MapStatus.ACTIVE)


# ---------------------------------------------------------------------------
# Slice 2 gaps moved here: /meta/ file statuses and per-side file types
# ---------------------------------------------------------------------------
class MetaFileGapTests(Slice2Base):
    def test_meta_lists_file_statuses_and_every_file_type_config(self):
        body = self.as_(Role.OPERATIONS_USER).get(f"{UPI}/meta/").json()
        statuses = {s["value"]: s for s in body["processing_statuses"]}
        self.assertEqual(set(statuses), set(ProcessingStatus.values))
        self.assertEqual(statuses["AWAITING_DATE_SELECTION"]["state"], "AWAITING_DATE_SELECTION")
        self.assertEqual(statuses["VALIDATION_FAILED"]["state"], "FAILED")
        self.assertEqual(statuses["STAGED"]["state"], "PROCESSING")
        labels = {c["label"]: c["id"] for c in body["file_type_configs"]}
        self.assertEqual(len(body["file_type_configs"]), 10)
        self.assertEqual((labels["Raw Data ACQ"], labels["Raw Data ISS"]),
                         (self.cfg["RAW_DATA_ACQ"].pk, self.cfg["RAW_DATA_ISS"].pk))
        # Unchanged for existing callers: one entry per type code.
        self.assertEqual(len(body["file_types"]), len({c["file_type_code"] for c in body["file_type_configs"]}))
        for key in ("match_group_statuses", "presence_patterns", "result_orderings", "settlement_statuses",
                    "self_check_statuses", "settlement_transaction_kinds", "ntsl_categories", "settings_kinds"):
            self.assertTrue(body[key], key)

    def test_files_list_filters_one_side_with_the_meta_id(self):
        acq = self.make_file("RAW_DATA_ACQ", "1C", filename="acq.csv")
        iss = self.make_file("RAW_DATA_ISS", "1C", filename="iss.csv")
        labels = {c["label"]: c["id"] for c in self.as_(Role.MAKER).get(f"{UPI}/meta/").json()["file_type_configs"]}
        ids = lambda p: [r["id"] for r in self.as_(Role.MAKER).get(FILES, p).json()["results"]]  # noqa: E731
        self.assertEqual(ids({"file_type_config": str(labels["Raw Data ACQ"])}), [acq.pk])
        self.assertEqual(ids({"file_type_config": str(labels["Raw Data ISS"])}), [iss.pk])
        self.assertEqual(set(ids({"file_type": "RAW_DATA"})), {acq.pk, iss.pk})  # type code still works
        self.assertEqual(set(ids({"file_type_config": f"{labels['Raw Data ACQ']},{labels['Raw Data ISS']}"})),
                         {acq.pk, iss.pk})


assert date(2026, 7, 14) == D  # both bases use the same business date
