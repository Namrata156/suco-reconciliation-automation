"""
S2 NTSL self-check for one or more business dates:

    manage.py check_ntsl --date 2026-07-14 [--date ...] [--module UPI]

For every active settlement cycle, checks that NTSL's categorised lines
reproduce its own Sub Totals, Settlement Amount and Final Settlement Amount
(see settlement/selfcheck.py), stores one NtslCycleCheck per cycle
(earlier results for the same cycle are superseded, never deleted) and
prints a table. Unmapped descriptions are listed. Exit status is 0 even
when cycles fail (the results are stored); it is non-zero only when the
check cannot run (no NTSL lines for the date, no active mapping).
"""
from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from core.models import PaymentModule
from settlement.mapping import NoActiveMapping
from settlement.models import CheckStatus
from settlement.selfcheck import check_ntsl_date
from transactions.models import NtslSettlementLine


def _d(v):
    return "-" if v is None else f"{v:,.2f}"


class Command(BaseCommand):
    help = "NTSL self-check per cycle (Sub Totals / Settlement Amount / Final) for the given business date(s)."

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
            if not NtslSettlementLine.objects.filter(
                payment_module=module, business_date=business_date, uploaded_file__is_current=True
            ).exists():
                raise CommandError(f"No NTSL lines loaded for {module.code} {business_date}")
            try:
                summary = check_ntsl_date(module, business_date, source="manage.py check_ntsl")
            except NoActiveMapping as exc:
                raise CommandError(str(exc)) from exc
            self._print(module, summary)

    def _print(self, module, summary):
        out = self.stdout
        out.write(f"\nNTSL self-check {module.code} {summary.business_date} (mapping v{summary.map_version.version})")
        out.write(f"{'Cycle':<6} {'Status':<8} {'Lines':>5} {'Unmap':>5} {'SubTot Dr diff':>15} {'SubTot Cr diff':>15} "
                  f"{'Settle diff':>12} {'Final diff':>12} {'Final net':>16}")
        for c in summary.checks:
            out.write(
                f"{c.settlement_cycle.cycle_code:<6} {c.status:<8} {c.line_count:>5} {c.unmapped_count:>5} "
                f"{_d(c.subtotal_debit_diff):>15} {_d(c.subtotal_credit_diff):>15} {_d(c.settlement_diff):>12} "
                f"{_d(c.final_diff):>12} {_d(c.ntsl_final_net):>16}"
            )
            for reason in c.failures:
                out.write(f"       - {reason}")
        counts = {s: sum(1 for c in summary.checks if c.status == s) for s in CheckStatus.values}
        if summary.lines_without_cycle:
            out.write(self.style.WARNING(f"{summary.lines_without_cycle} NTSL line(s) have no settlement cycle"))
        line = f"{len(summary.checks)} cycles: " + ", ".join(f"{n} {s}" for s, n in counts.items() if n)
        out.write(self.style.SUCCESS(line) if summary.passed else self.style.WARNING(line))
