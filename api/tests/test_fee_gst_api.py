"""Fee GST check (owner 2026-11-06) on GET /api/v1/<module>/dates/<date>/settlement/ -> self_check:
warning only, never part of status / all_pass."""
from __future__ import annotations

from api.tests.test_slice3 import DATE, O, Slice3Base


class SelfCheckGstApiTests(Slice3Base):
    def setUp(self):
        super().setUp()
        self.build_day()  # recon_base.ntsl_lines: 1C has a switching fee and no GST line
        self.outcome = self.reconcile()

    def test_gst_fields_on_each_cycle(self):
        sc = self.ok(O, f"{DATE}/settlement/")["self_check"]
        self.assertTrue(sc["all_pass"])  # the GST warning does not change all_pass
        self.assertEqual(sc["gst_warnings"], sum(1 for c in sc["results"] if c["gst_warning"]))
        one = next(c for c in sc["results"] if c["cycle"] == "1C")
        self.assertEqual((one["status"], one["failures"], one["gst_warning"]), ("PASS", [], True))
        g = one["gst_check"]
        self.assertEqual(set(g), {"rate", "tolerance", "rows"})
        self.assertEqual((g["rate"], g["tolerance"]), ("0.18", "1.00"))
        self.assertEqual(len(g["rows"]), 12)
        sw = next(r for r in g["rows"] if r["fee_category"] == "SWITCHING_FEE" and r["side"] == "DEBIT")
        self.assertEqual(sw, {"fee_category": "SWITCHING_FEE", "gst_category": "SWITCHING_FEE_GST", "side": "DEBIT",
                              "fee": "0.30", "fee_txn_count": 3, "expected_gst": "0.05", "ntsl_gst": "0.00",
                              "difference": "0.05", "tolerance": "1.00", "within": False,
                              "note": "FEE_WITHOUT_GST"})
        quiet = next(c for c in sc["results"] if c["cycle"] == "DC1")
        self.assertFalse(quiet["gst_warning"])

    def test_result_stays_tallied(self):
        self.assertEqual(self.ok(O, f"{DATE}/settlement/")["result"]["status"], "TALLIED")
