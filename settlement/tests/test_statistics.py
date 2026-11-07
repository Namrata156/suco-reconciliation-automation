"""reconcile_settlement_date ANALYZEs the freshly written tables first
(settlement.reconcile.refresh_planner_statistics; e2e 2026-11-07: with no
statistics on a fresh database the _npci_side query ran for hours)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from django.db import connection
from django.test.utils import CaptureQueriesContext

from settlement import reconcile
from settlement.models import ResultStatus
from settlement.tests.recon_base import SettlementTestBase


def _analyze_queries(ctx) -> list[str]:
    return [q["sql"] for q in ctx.captured_queries if q["sql"].lstrip().upper().startswith("ANALYZE")]


class RefreshStatisticsTests(SettlementTestBase):
    def setUp(self):
        super().setUp()
        self.build_day()

    def test_tables_are_analyzed_before_the_settlement_queries(self):
        with CaptureQueriesContext(connection) as ctx:
            r = self.reconcile().result
        self.assertEqual(r.status, ResultStatus.TALLIED, r.reasons)
        analyze = _analyze_queries(ctx)
        self.assertEqual(len(analyze), 1, analyze)
        for model in reconcile.STATISTICS_MODELS:
            self.assertIn(connection.ops.quote_name(model._meta.db_table), analyze[0])
        sqls = [q["sql"] for q in ctx.captured_queries]
        first_select = next(i for i, s in enumerate(sqls) if s.lstrip().upper().startswith("SELECT"))
        self.assertLess(sqls.index(analyze[0]), first_select)

    def test_the_freshly_written_tables_are_covered(self):
        names = {m._meta.label for m in reconcile.STATISTICS_MODELS}
        self.assertTrue({"transactions.TransactionFact", "reconciliation.TransactionMatchGroup",
                         "reconciliation.RegisterPresence", "rule_engine.RuleResult"} <= names, names)

    def test_skipped_on_other_databases(self):
        with mock.patch.object(connection, "vendor", "sqlite"), mock.patch.object(
                connection, "cursor", side_effect=AssertionError("no query expected")):
            reconcile.refresh_planner_statistics()

    def test_a_failing_analyze_is_logged_and_the_run_goes_on(self):
        missing = SimpleNamespace(_meta=SimpleNamespace(db_table="no_such_table_for_analyze"))
        with mock.patch.object(reconcile, "STATISTICS_MODELS", (missing,)), \
                self.assertLogs("settlement.reconcile", "WARNING") as logs:
            r = self.reconcile().result
        self.assertEqual(r.status, ResultStatus.TALLIED, r.reasons)  # the transaction was not broken
        self.assertIn("ANALYZE before settlement failed", logs.output[0])
