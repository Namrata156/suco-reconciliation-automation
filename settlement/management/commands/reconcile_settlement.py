"""
Settlement reconciliation (Phase 4 S3/S4) for one or more business dates:

    manage.py reconcile_settlement --date 2026-07-14 [--date ...] [--module UPI]

Runs the NTSL self-check first (settlement.selfcheck, incl. O4), then the
gap rules G01-G10 (settlement.reconcile), stores a SettlementResult per date
(the previous one is superseded, never deleted), syncs the date's settlement
case (raised when UNEXPLAINED; flagged for review when a re-run tallies) and
prints a per-cycle table.

Where it fits: after the rules. The daily order is load files ->
build the presence matrix -> evaluate_rules (which syncs the Phase 3 cases)
-> reconcile_settlement. A date whose rule results are missing or older
than its matrix comes out NOT_READY, so running it too early is harmless.

Exit status is 0 whatever the status (it is stored); non-zero only when
the command cannot run (unknown module, bad date).
"""
from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from core.models import PaymentModule
from settlement.models import ResultStatus
from settlement.reconcile import reconcile_settlement_date


def _d(v):
    return "-" if v is None else f"{v:,.2f}"


class Command(BaseCommand):
    help = "Settlement reconciliation (NTSL vs system, gap rules G01-G10) for the given business date(s)."

    def add_arguments(self, parser):
        parser.add_argument("--date", action="append", required=True, help="Business date YYYY-MM-DD (repeatable)")
        parser.add_argument("--module", default="UPI")

    def handle(self, *args, **options):
        module = PaymentModule.objects.filter(code=options["module"]).first()
        if module is None:
            raise CommandError(f"No payment module {options['module']!r}")
        try:
            dates = [date.fromisoformat(d) for d in options["date"]]
        except ValueError as exc:
            raise CommandError(f"Bad --date: {exc}") from exc
        for business_date in dates:
            outcome = reconcile_settlement_date(module, business_date, source="manage.py reconcile_settlement")
            self._print(module, outcome)

    def _print(self, module, outcome):
        r, out = outcome.result, self.stdout
        style = {ResultStatus.TALLIED: self.style.SUCCESS, ResultStatus.UNEXPLAINED: self.style.ERROR}.get(
            r.status, self.style.WARNING)
        out.write(f"\nSettlement {module.code} {r.business_date}: " + style(r.status)
                  + f"  (result {r.pk}, tolerance Rs {r.tolerance_amount:.2f} / {r.tolerance_count} txn)")
        if r.status == ResultStatus.NOT_READY:
            for reason in r.reasons:
                out.write(f"  - {reason}")
            return
        out.write(f"{'Cycle':<6} {'Side':<12} {'NTSL Rs':>16} {'NTSL n':>7} {'Matched Rs':>16} {'Match n':>7} "
                  f"{'Explained Rs':>13} {'Expl n':>6} {'Unexpl Rs':>11} {'Unexpl n':>8}")
        for c in r.cycle_rows.select_related("settlement_cycle").order_by("pk"):
            code = c.settlement_cycle.cycle_code if c.settlement_cycle_id else "-"
            out.write(
                f"{code:<6} {c.side:<12} {_d(c.ntsl_amount):>16} {'-' if c.ntsl_count is None else c.ntsl_count:>7} "
                f"{_d(c.matched_amount):>16} {c.matched_count:>7} {_d(c.explained_amount):>13} {c.explained_count:>6} "
                f"{_d(c.unexplained_amount):>11} {c.unexplained_count:>8}" + ("" if c.within_tolerance else "  <--")
            )
        s = r.snapshot.get("sop", {})
        out.write(f"SOP check: CBS net {_d(r.sop_cbs_net)} - NTSL net {_d(r.sop_ntsl_net)} = difference "
                  f"{_d(r.sop_difference)}; explained {_d(r.sop_explained)} "
                  f"({', '.join(f'{k} {v['count']} / {v['amount']}' for k, v in s.get('explained_by_rule', {}).items())}); "
                  f"unexplained {_d(r.sop_unexplained)}")
        out.write(f"G10 fees / taxes (NTSL-internal, all cycles, credit - debit): {_d(r.fees_net)}")
        for reason in r.reasons:
            out.write(self.style.WARNING(f"  - {reason}"))
        if outcome.case_action not in ("none", ""):
            out.write(f"Settlement case: {outcome.case_action} (case {outcome.case_id})")
