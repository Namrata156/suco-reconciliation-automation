"""
S2 NTSL self-check (Phase 4 design, approved 2026-10-11): before NTSL is
compared with anything, check that its own lines add up, per business date
and cycle, using the ACTIVE category mapping. This catches a bad file or a
bad mapping.

Arithmetic (worked out from the 36 real cycles, all of which pass):
  1. Sub Totals debit  = sum of debits of every DETAIL line
     Sub Totals credit = sum of credits of every DETAIL line
     (per column, not netted; fees, GST, rebate, mandate fees included)
  2. Settlement Amount (credit - debit) = Sub Totals (credit - debit)
  3. Final (credit - debit) = Settlement Amount (credit - debit)
                              + sum of POST lines (credit - debit)
Each step uses NTSL's own figure from the step before, so a failure points
at one step. Nets are credit - debit: negative means SSK pays.

The tolerance is Rs 0: this is NTSL's internal arithmetic, not the
settlement reconciliation (whose configurable tolerance, Q4, is Slice 2).

A cycle FAILS when: any line is unmapped (listed, and left out of the sums),
a total line is missing or repeated, the Final line disagrees with the
loader's is_final_settlement flag, lines come from more than one current
file, or any difference is not 0. An active cycle with no NTSL lines gets
a MISSING result.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.models import PaymentModule, SettlementCycle
from settlement.categories import SECTION_BY_CATEGORY, NtslCategory, Section
from settlement.mapping import active_classifier
from settlement.models import CheckStatus, NtslCycleCheck
from transactions.models import NtslSettlementLine

logger = logging.getLogger(__name__)

TOLERANCE = Decimal("0.00")
ZERO = Decimal("0.00")


@dataclass
class DateCheckSummary:
    business_date: object
    map_version: object
    checks: list[NtslCycleCheck] = field(default_factory=list)
    lines_without_cycle: int = 0

    @property
    def passed(self) -> bool:
        return self.lines_without_cycle == 0 and all(c.status == CheckStatus.PASS for c in self.checks)


def _money(v: Decimal) -> str:
    return f"{v:.2f}"


def _evaluate(lines, classifier) -> dict:
    """Pure arithmetic over one cycle's lines; returns the NtslCycleCheck field values."""
    totals: dict[str, dict] = defaultdict(lambda: {"lines": 0, "txn_count": 0, "debit": ZERO, "credit": ZERO})
    by_category: dict[str, list] = defaultdict(list)
    unmapped, failures = [], []
    files = set()
    for line in lines:
        files.add(line.uploaded_file_id)
        c = classifier.classify(line.description)
        if c is None:
            unmapped.append({
                "description": line.description, "row_reference": line.row_reference,
                "txn_count": line.txn_count, "debit": _money(line.debit), "credit": _money(line.credit),
            })
            continue
        t = totals[c.category]
        t["lines"] += 1
        t["txn_count"] += line.txn_count or 0
        t["debit"] += line.debit
        t["credit"] += line.credit
        by_category[c.category].append(line)

    if len(files) > 1:
        failures.append(f"Lines come from {len(files)} current NTSL files for this cycle (expected 1)")
    if unmapped:
        failures.append(
            f"{len(unmapped)} line(s) have a description the active mapping does not know: "
            + "; ".join(sorted({u['description'] for u in unmapped}))
        )

    def single(category: str, label: str):
        found = by_category.get(category, [])
        if len(found) != 1:
            failures.append(f"Expected exactly one '{label}' line, found {len(found)}")
            return None
        return found[0]

    sub = single(NtslCategory.SUBTOTAL, "Sub Totals")
    settle = single(NtslCategory.SETTLEMENT_AMOUNT, "Settlement Amount")
    final = single(NtslCategory.FINAL, "Final Settlement Amount")

    flagged = [ln for ln in lines if ln.is_final_settlement]
    if final is not None and (len(flagged) != 1 or flagged[0].pk != final.pk):
        failures.append("The line mapped to FINAL is not the line the loader flagged as Final Settlement Amount")

    def section_sum(section: str, col: str) -> Decimal:
        return sum(
            (t[col] for cat, t in totals.items() if SECTION_BY_CATEGORY[cat] == section), ZERO
        )

    out = {
        "computed_subtotal_debit": section_sum(Section.DETAIL, "debit"),
        "computed_subtotal_credit": section_sum(Section.DETAIL, "credit"),
        "ntsl_subtotal_debit": sub.debit if sub else None,
        "ntsl_subtotal_credit": sub.credit if sub else None,
        "ntsl_settlement_net": (settle.credit - settle.debit) if settle else None,
        "computed_settlement_net": (sub.credit - sub.debit) if sub else None,
        "ntsl_final_net": (final.credit - final.debit) if final else None,
        "computed_final_net": (
            (settle.credit - settle.debit) + section_sum(Section.POST, "credit") - section_sum(Section.POST, "debit")
            if settle else None
        ),
    }

    def check(label, computed, ntsl):
        if computed is not None and ntsl is not None and abs(computed - ntsl) > TOLERANCE:
            failures.append(f"{label}: computed {_money(computed)}, NTSL {_money(ntsl)}, difference {_money(computed - ntsl)}")

    check("Sub Totals debit", out["computed_subtotal_debit"], out["ntsl_subtotal_debit"])
    check("Sub Totals credit", out["computed_subtotal_credit"], out["ntsl_subtotal_credit"])
    check("Settlement Amount (credit - debit)", out["computed_settlement_net"], out["ntsl_settlement_net"])
    check("Final Settlement Amount (credit - debit)", out["computed_final_net"], out["ntsl_final_net"])

    out.update(
        status=CheckStatus.FAIL if failures else CheckStatus.PASS,
        failures=failures,
        line_count=len(lines),
        unmapped_count=len(unmapped),
        unmapped_lines=unmapped,
        category_totals={
            cat: {**t, "debit": _money(t["debit"]), "credit": _money(t["credit"])} for cat, t in sorted(totals.items())
        },
    )
    return out


@transaction.atomic
def check_ntsl_date(payment_module: PaymentModule, business_date, *, source: str = "") -> DateCheckSummary:
    """Checks every active cycle of the module for the date; supersedes
    earlier current results for the same cycles."""
    version, classifier = active_classifier(payment_module)
    now = timezone.now()
    lines_by_cycle: dict = defaultdict(list)
    lines = (
        NtslSettlementLine.objects.filter(
            payment_module=payment_module, business_date=business_date, uploaded_file__is_current=True
        ).order_by("uploaded_file_id", "id")
    )
    for line in lines:
        lines_by_cycle[line.settlement_cycle_id].append(line)

    summary = DateCheckSummary(business_date=business_date, map_version=version)
    summary.lines_without_cycle = len(lines_by_cycle.pop(None, []))
    if summary.lines_without_cycle:
        logger.warning("NTSL %s %s: %d line(s) have no settlement cycle", payment_module, business_date,
                       summary.lines_without_cycle)

    cycles = list(SettlementCycle.objects.filter(payment_module=payment_module, is_active=True).order_by("id"))
    cycles += list(SettlementCycle.objects.filter(pk__in=set(lines_by_cycle) - {c.pk for c in cycles}))
    for cycle in cycles:
        cycle_lines = lines_by_cycle.get(cycle.pk, [])
        if cycle_lines:
            values = _evaluate(cycle_lines, classifier)
        else:
            values = {"status": CheckStatus.MISSING, "failures": ["No NTSL lines loaded for this cycle"]}
        NtslCycleCheck.objects.filter(
            payment_module=payment_module, business_date=business_date, settlement_cycle=cycle, is_current=True
        ).update(is_current=False, superseded_at=now)
        check = NtslCycleCheck.objects.create(
            payment_module=payment_module, business_date=business_date, settlement_cycle=cycle,
            map_version=version, checked_at=now, source=source, **values,
        )
        if check.unmapped_count:
            logger.warning("NTSL %s %s %s: %d unmapped line(s): %s", payment_module, business_date,
                           cycle.cycle_code, check.unmapped_count,
                           sorted({u["description"] for u in check.unmapped_lines}))
        summary.checks.append(check)
    return summary
