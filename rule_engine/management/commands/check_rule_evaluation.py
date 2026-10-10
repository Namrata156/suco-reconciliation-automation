"""
Acceptance check for the rule evaluator (Phase 2 Step 4), read-only.

1. Runs the acceptance oracle docs/phase2_rule_table_v3_1_reference.sql
   (its CASE expression unchanged; only the final aggregate SELECT is
   swapped for a per-group one) in a READ ONLY transaction and compares its
   rule per match group with the current RuleResult of every group.
2. Compares the current per-rule counts with
   rule_engine.seed_data.v3_2.EXPECTED_COUNTS (identical to v3.1's: v3.2
   changes only R13's outcome, not which groups it matches) and reports
   which rule set version the current results reference.
The oracle SQL encodes v3.1's CASE order and conditions, which v3.2 keeps
unchanged, so it remains the per-group oracle.

The project's tests load only single-cycle samples, so the full-date counts
can't be a unit test; this command is the acceptance check instead.

    manage.py check_rule_evaluation
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from core.models import PaymentModule
from rule_engine.models import RuleResult
from rule_engine.seed_data import v3_2 as expected

ORACLE = Path(settings.BASE_DIR) / "docs" / "phase2_rule_table_v3_1_reference.sql"
_CASE_HEAD = "r as (select business_date, case"
_FINAL_SELECT = "select rule,\n  count(*)"


def oracle_per_group_sql() -> str:
    sql = ORACLE.read_text(encoding="utf-8")
    if sql.count(_CASE_HEAD) != 1 or sql.count(_FINAL_SELECT) != 1:
        raise CommandError(f"{ORACLE.name} no longer has the expected shape; update this check")
    sql = sql.replace(_CASE_HEAD, "r as (select id, business_date, case")
    return sql[: sql.index(_FINAL_SELECT)] + "select id, business_date, rule from r"


def oracle_rules() -> dict[int, tuple]:
    """{match group id: (business date, oracle rule)}. Read-only: the query
    is a SELECT, and in a fresh transaction it is also run READ ONLY (inside
    an already-open transaction, e.g. a test, the mode can't be switched)."""
    outer = connection.in_atomic_block
    with transaction.atomic(), connection.cursor() as cursor:
        if not outer:
            cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(oracle_per_group_sql())
        return {gid: (d, rule) for gid, d, rule in cursor.fetchall()}


class Command(BaseCommand):
    help = "Read-only: compares current rule results with the v3.1 oracle SQL and EXPECTED_COUNTS."

    def add_arguments(self, parser):
        parser.add_argument("--payment-module", default="UPI")

    def handle(self, *args, **options):
        module = PaymentModule.objects.get(code=options["payment_module"])
        oracle = oracle_rules()
        ours = dict(
            (gid, (d, code))
            for gid, d, code in RuleResult.objects.filter(payment_module=module, is_superseded=False).values_list(
                "match_group_id", "business_date", "rule_code"
            )
        )
        versions = Counter(
            RuleResult.objects.filter(payment_module=module, is_superseded=False).values_list(
                "rule_set__version_label", flat=True
            )
        )
        self.stdout.write(f"Current results by rule set version: {dict(versions)}")
        failures = 0
        only_oracle = oracle.keys() - ours.keys()
        only_ours = ours.keys() - oracle.keys()
        diff = [(gid, oracle[gid], ours[gid]) for gid in oracle.keys() & ours.keys() if oracle[gid] != ours[gid]]
        self.stdout.write(
            f"Per-group: oracle {len(oracle)} groups, evaluator {len(ours)} current results; "
            f"{len(diff)} differ, {len(only_oracle)} only in oracle, {len(only_ours)} only in evaluator"
        )
        for gid, o, e in diff[:20]:
            self.stdout.write(self.style.ERROR(f"  group {gid}: oracle {o} vs evaluator {e}"))
        failures += len(diff) + len(only_oracle) + len(only_ours)

        counts = Counter((d, code) for d, code in ours.values())
        for i, d in enumerate(expected.EXPECTED_COUNT_DATES):
            got_total = sum(n for (dd, _), n in counts.items() if dd == d)
            bad = {
                code: (want[i], counts.get((d, code), 0))
                for code, want in expected.EXPECTED_COUNTS.items()
                if counts.get((d, code), 0) != want[i]
            }
            ok = not bad and got_total == expected.EXPECTED_TOTALS[i]
            line = f"{d}: total {got_total} (expected {expected.EXPECTED_TOTALS[i]})" + (
                "  OK" if ok else f"  MISMATCH {bad}"
            )
            self.stdout.write(self.style.SUCCESS(line) if ok else self.style.ERROR(line))
            failures += 0 if ok else 1
        if failures:
            raise CommandError(f"Acceptance check failed ({failures} problems)")
        self.stdout.write(self.style.SUCCESS("Acceptance check passed"))
