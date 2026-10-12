"""
Settlement reconciliation (Phase 4 design S3/S4, Slice 2) for one payment
module + business date, applying the owner-approved gap rules G01-G10 / Z
(settlement.gap_rules; docs/phase4_gap_rules_draft_v1.txt).

    reconcile_settlement_date(module, date, source=...) -> ReconcileOutcome

Steps, all in one transaction:
  1. Readiness. The S2 self-check is run first (settlement.selfcheck, incl.
     the O4 switching-fee count); every active cycle must PASS. Every live
     match group of the date must have a current rule result, evaluated
     after the matrix was last built. Otherwise the result is NOT_READY
     (stored, with the reasons) and nothing is compared.
  2. NTSL side, per cycle, from the categorised NtslSettlementLine rows
     (active mapping): remitter / beneficiary transaction amounts and
     counts, Net Adjusted Amount, fee / tax lines (G10), and any non-zero
     line no G rule covers.
  3. System side and attribution, per cycle x side, using each NPCI row's
     own settlement cycle. Only NPCI rows NTSL settles (code 00 / RB) are
     compared; each gets a G rule from its match group's current rule result
     (gap_rules.attribute_npci_row). DC1 / DC2 Net Adjusted Amount is
     compared with the signed money-moving Adjustment rows (G09).
       unexplained = NTSL - matched (G01-G03) - explained (G04-G06 / G09)
  4. CBS-side SOP check (C1): (CBS deposits - withdrawals, valid-RRN rows)
     - (NTSL TXN debit - credit) = explained items (G04-G08), per RRN:
     the RRN's CBS net minus its NTSL-settled NPCI net.
  5. Status: TALLIED when every cycle x side row and the SOP check are
     within the active tolerance (Q4); otherwise UNEXPLAINED. The previous
     current result is superseded (never deleted). The full calculation,
     with the contributing transactions, is the result's snapshot.
  6. The date's settlement case is synced (settlement.cases): raised when
     UNEXPLAINED, flagged for review when a later run tallies.

Read volume: one date is read with a fixed number of queries; the ~70K
matched (G01) rows are only summed, never listed.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Exists, F, Func, Max, OuterRef, Subquery, Sum, TextField, Value
from django.utils import timezone

from core.models import Direction, PaymentModule, RegisterType, SettlementCycle
from reconciliation.models import RegisterPresence, TransactionMatchGroup
from reconciliation.presence import DEFAULT_PRESENCE_LEGS
from rule_engine.models import RuleEvaluationRun, RuleResult
from settlement import cases
from settlement import gap_rules as g
from settlement.categories import NtslCategory
from settlement.mapping import NoActiveMapping, active_classifier
from settlement.models import (
    CheckStatus,
    ResultStatus,
    SettlementCycleResult,
    SettlementResult,
    Side,
)
from settlement.selfcheck import check_ntsl_date
from settlement.tolerance import active_tolerance
from transactions.models import NtslSettlementLine, TransactionFact
from transactions.models.history import HISTORY_MODEL_BY_STAGING_MODEL
from transactions.models.staging import STAGING_MODEL_BY_REGISTER_TYPE

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")
NPCI_REGISTER_TYPES = DEFAULT_PRESENCE_LEGS["NPCI"]
CBS_REGISTER_TYPE = RegisterType.CBS_GL_EXTRACT
CBS_LEG, NPCI_LEG = "CBS", "NPCI"
_BATCH = 5000
# Raw cells (1-based, as FileTypeConfig.column_mapping / reconciliation.presence).
NPCI_CODE_CELL = "5"
ADJ_UID_CELL, ADJ_TYPE_CELL, ADJ_REMITTER_CELL, ADJ_BENEFICIARY_CELL = "2", "4", "5", "6"
SSK = "SSK"


def _m(v) -> str:
    return f"{(v or ZERO):.2f}"


def _clean(v) -> str:
    return str(v or "").strip().removeprefix("'").strip()


@dataclass
class Bucket:
    count: int = 0
    amount: Decimal = ZERO

    def add(self, amount, n: int = 1) -> None:
        self.count += n
        self.amount += amount

    def as_dict(self) -> dict:
        return {"count": self.count, "amount": _m(self.amount)}


@dataclass
class ReconcileOutcome:
    result: SettlementResult
    case_action: str = ""  # settlement.cases: created / updated / flagged / unchanged / none
    case_id: int | None = None


# ---------------------------------------------------------------------------
# readiness
# ---------------------------------------------------------------------------
def _readiness(payment_module, business_date) -> tuple[list[str], dict, RuleEvaluationRun | None]:
    """(reasons it is NOT ready, self-check summary for the snapshot, latest run)."""
    reasons: list[str] = []
    selfcheck: dict = {}
    if not NtslSettlementLine.objects.filter(
        payment_module=payment_module, business_date=business_date, uploaded_file__is_current=True
    ).exists():
        reasons.append("No NTSL lines are loaded for this date")
    else:
        summary = check_ntsl_date(payment_module, business_date, source="settlement.reconcile")
        selfcheck = {c.settlement_cycle.cycle_code: c.status for c in summary.checks}
        for c in summary.checks:
            if c.status != CheckStatus.PASS:
                reasons.append(f"NTSL self-check {c.settlement_cycle.cycle_code}: {c.status} - "
                               + "; ".join(c.failures))
        if summary.lines_without_cycle:
            reasons.append(f"{summary.lines_without_cycle} NTSL line(s) have no settlement cycle")

    groups = TransactionMatchGroup.objects.filter(
        payment_module=payment_module, business_date=business_date, is_superseded=False
    )
    group_count = groups.count()
    result_count = RuleResult.objects.current().filter(
        payment_module=payment_module, business_date=business_date, match_group__is_superseded=False
    ).count()
    run = RuleEvaluationRun.objects.filter(payment_module=payment_module, business_date=business_date).order_by(
        "-started_at"
    ).first()
    if group_count == 0:
        reasons.append("No presence matrix (match groups) for this date")
    elif result_count < group_count:
        reasons.append(f"Rule results missing: {group_count - result_count} of {group_count} match groups have no "
                       "current rule result (run evaluate_rules)")
    elif run is not None:
        last_built = groups.aggregate(m=Max("updated_at"))["m"]
        if last_built is not None and last_built > run.started_at:
            reasons.append(f"The presence matrix changed after the last rule evaluation ({last_built:%Y-%m-%d %H:%M} "
                           f"> {run.started_at:%Y-%m-%d %H:%M}); run evaluate_rules")
    return reasons, selfcheck, run


# ---------------------------------------------------------------------------
# NTSL side
# ---------------------------------------------------------------------------
@dataclass
class NtslCycle:
    remitter: Bucket = field(default_factory=Bucket)
    remitter_rb: Bucket = field(default_factory=Bucket)
    beneficiary: Bucket = field(default_factory=Bucket)
    beneficiary_rb: Bucket = field(default_factory=Bucket)
    adjustment_net: Decimal = ZERO
    adjustment_count: int | None = None
    fees: dict = field(default_factory=lambda: defaultdict(lambda: {"count": 0, "debit": ZERO, "credit": ZERO}))
    uncovered: list = field(default_factory=list)
    txn_debit: Decimal = ZERO
    txn_credit: Decimal = ZERO

    @property
    def fees_net(self) -> Decimal:
        return sum((f["credit"] - f["debit"] for f in self.fees.values()), ZERO)

    @property
    def uncovered_net(self) -> Decimal:
        return sum((Decimal(u["credit"]) - Decimal(u["debit"]) for u in self.uncovered), ZERO)


def _ntsl_side(payment_module, business_date, classifier) -> dict[str, NtslCycle]:
    C = NtslCategory
    out: dict[str, NtslCycle] = defaultdict(NtslCycle)
    lines = NtslSettlementLine.objects.filter(
        payment_module=payment_module, business_date=business_date, uploaded_file__is_current=True
    ).values_list("settlement_cycle__cycle_code", "description", "txn_count", "debit", "credit")
    for cycle_code, description, n, debit, credit in lines:
        cls = classifier.classify(description)
        if cls is None or cycle_code is None:  # cannot happen after a PASSing self-check
            continue
        cat, cyc = cls.category, out[cycle_code]
        if cat in g.TXN_CATEGORIES:
            cyc.txn_debit += debit
            cyc.txn_credit += credit
        if cat in g.REMITTER_CATEGORIES:
            (cyc.remitter_rb if cat == C.TXN_REMITTER_RB else cyc.remitter).add(debit - credit, n or 0)
        elif cat in g.BENEFICIARY_CATEGORIES:
            (cyc.beneficiary_rb if cat == C.TXN_BENEFICIARY_RB else cyc.beneficiary).add(credit - debit, n or 0)
        elif cat in g.ADJUSTMENT_CATEGORIES:
            cyc.adjustment_net += credit - debit
            if n is not None:
                cyc.adjustment_count = (cyc.adjustment_count or 0) + n
        elif cat in g.FEE_CATEGORIES:
            f = cyc.fees[cat]
            f["count"] += n or 0
            f["debit"] += debit
            f["credit"] += credit
        elif cat in g.UNCOVERED_CATEGORIES and (debit or credit):
            cyc.uncovered.append({"category": cat, "description": description, "debit": _m(debit),
                                  "credit": _m(credit)})
    return out


# ---------------------------------------------------------------------------
# NPCI side (G01-G06)
# ---------------------------------------------------------------------------
def _staging_cells(by_register_type: dict[str, list[int]], cells: tuple[str, ...]) -> dict[tuple[str, int], tuple]:
    """{(register type, staging id): (cell values...)} from the live staging
    table or, once archived, its history copy. Reads only the named cells.

    jsonb_extract_path_text, not raw_cells__5: Django reads a numeric key
    transform as an ARRAY index on PostgreSQL, which is NULL on our objects."""
    keys = {f"c{c}": Func(F("raw_cells"), Value(c), function="jsonb_extract_path_text", output_field=TextField())
            for c in cells}
    names = list(keys)
    out: dict[tuple[str, int], tuple] = {}
    for register_type, ids in by_register_type.items():
        live = STAGING_MODEL_BY_REGISTER_TYPE[register_type]
        history = HISTORY_MODEL_BY_STAGING_MODEL[live]
        for start in range(0, len(ids), _BATCH):
            batch = ids[start: start + _BATCH]
            found = {row[0]: row[1:] for row in
                     live.objects.filter(id__in=batch).annotate(**keys).values_list("id", *names)}
            missing = [i for i in batch if i not in found]
            if missing:
                for row in history.objects.filter(original_id__in=missing).order_by("original_id", "archived_at") \
                        .annotate(**keys).values_list("original_id", *names):
                    found[row[0]] = row[1:]  # latest archived copy wins
            for i, v in found.items():
                out[(register_type, i)] = tuple(_clean(x) for x in v)
    return out


@dataclass
class NpciSide:
    # (cycle code or None, side) -> {G rule: Bucket}
    buckets: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(Bucket)))
    rb: dict = field(default_factory=lambda: defaultdict(Bucket))  # (cycle, side) -> RB rows
    contributing: list = field(default_factory=list)  # every settled row except G01
    not_placed: list = field(default_factory=list)  # settled rows with no side (direction unknown)
    not_settled: Counter = field(default_factory=Counter)  # code -> rows NTSL does not settle
    ntsl_net_by_rrn: dict = field(default_factory=lambda: defaultdict(lambda: ZERO))  # debit - credit
    settled_rows: int = 0


def _npci_side(payment_module, business_date, cycle_codes: dict[int, str]) -> NpciSide:
    live_groups = dict(match_group__payment_module=payment_module, match_group__business_date=business_date,
                       match_group__is_superseded=False)
    npci = RegisterPresence.objects.filter(leg=NPCI_LEG, **live_groups)
    primary = npci.filter(transaction_record_id=OuterRef("pk"))
    paired = npci.filter(paired_transaction_record_id=OuterRef("pk"))
    current = RuleResult.objects.filter(is_superseded=False)
    facts = (
        TransactionFact.objects.filter(
            payment_module=payment_module, business_date=business_date,
            source_register_type__in=NPCI_REGISTER_TYPES, uploaded_file__is_current=True,
        )
        .annotate(
            p_group=Subquery(primary.values("match_group_id")[:1]),
            p_code=Subquery(primary.values("response_code")[:1]),
            q_group=Subquery(paired.values("match_group_id")[:1]),
        )
        .annotate(
            p_rule=Subquery(current.filter(match_group_id=OuterRef("p_group")).values("rule_code")[:1]),
            q_rule=Subquery(current.filter(match_group_id=OuterRef("q_group")).values("rule_code")[:1]),
            q_on_us=Subquery(TransactionMatchGroup.objects.filter(pk=OuterRef("q_group")).values("is_on_us")[:1]),
            p_cbs=Exists(RegisterPresence.objects.filter(match_group_id=OuterRef("p_group"), leg=CBS_LEG,
                                                         present=True)),
        )
        .order_by("id")
        .values_list("id", "rrn", "direction", "amount", "settlement_cycle_id", "source_register_type",
                     "staging_row_id", "p_group", "p_code", "q_group", "p_rule", "q_rule", "q_on_us", "p_cbs")
    )
    out = NpciSide()
    # One streaming pass; rows whose code is not on a presence row (paired
    # rows, rows no group holds -- hundreds) wait for one staging lookup.
    deferred = []
    for row in facts.iterator(chunk_size=_BATCH):
        if row[8]:
            _attribute(out, row, row[8], cycle_codes)
        else:
            deferred.append(row)
    need: dict[str, list[int]] = defaultdict(list)
    for row in deferred:
        if row[6] is not None:
            need[row[5]].append(row[6])
    looked_up = _staging_cells(need, (NPCI_CODE_CELL,))
    for row in deferred:
        _attribute(out, row, (looked_up.get((row[5], row[6])) or ("",))[0], cycle_codes)
    return out


def _attribute(out: NpciSide, row: tuple, code: str, cycle_codes: dict) -> None:
    (_fid, rrn, direction, amount, cycle_id, _rtype, _staging_id, p_group, _p_code, q_group, p_rule, q_rule,
     q_on_us, p_cbs) = row
    if code not in g.SETTLED_CODES:
        out.not_settled[code or "<blank>"] += 1
        return
    out.settled_rows += 1
    cycle = cycle_codes.get(cycle_id)
    side = g.side_of_npci_direction(direction)
    role = g.PRIMARY if p_group else (g.PAIRED if q_group else None)
    rule = p_rule if p_group else q_rule
    rule_g = g.attribute_npci_row(role=role, rule_code=rule, is_on_us=bool(q_on_us), cbs_present=bool(p_cbs))
    if rule in g.SOP_ITEM_RULES:  # the SOP check needs these RRNs' NTSL net (debit - credit)
        out.ntsl_net_by_rrn[rrn] += amount if direction == Direction.DEBIT else -amount
    listed = [rrn, rule_g, rule or "", cycle, side, _m(amount), code, role or ""]
    if side is None:
        out.not_placed.append(listed)
        return
    out.buckets[(cycle, side)][rule_g].add(amount)
    if code == g.DEEMED_APPROVED_CODE:
        out.rb[(cycle, side)].add(amount)
    if rule_g != g.G01:
        out.contributing.append(listed)


# ---------------------------------------------------------------------------
# G09: Adjustment rows -> DC1 / DC2
# ---------------------------------------------------------------------------
@dataclass
class DisputeSide:
    explained: dict = field(default_factory=lambda: defaultdict(Bucket))  # DC1/DC2 -> money rows
    rows: list = field(default_factory=list)  # money-moving rows, signed
    no_money: Counter = field(default_factory=Counter)  # adjtype -> rows (Z)
    not_in_sign_table: list = field(default_factory=list)
    unassigned: list = field(default_factory=list)  # money rows whose Uid time is unknown / another date


def _dispute_side(payment_module, business_date) -> DisputeSide:
    facts = list(
        TransactionFact.objects.filter(
            payment_module=payment_module, business_date=business_date, source_register_type=RegisterType.ADJUSTMENT,
            uploaded_file__is_current=True,
        ).order_by("id").values_list("rrn", "amount", "staging_row_id")
    )
    cells = _staging_cells(
        {RegisterType.ADJUSTMENT: [s for _, _, s in facts if s is not None]},
        (ADJ_UID_CELL, ADJ_TYPE_CELL, ADJ_REMITTER_CELL, ADJ_BENEFICIARY_CELL),
    )
    out = DisputeSide()
    for rrn, amount, staging_id in facts:
        uid, adjtype, remitter, beneficiary = cells.get((RegisterType.ADJUSTMENT, staging_id), ("", "", "", ""))
        sign = g.adjustment_sign(adjtype)
        if sign == g.NO_MONEY:
            out.no_money[adjtype] += 1
            continue
        raised = g.uid_time(uid)
        row = [rrn, adjtype, remitter, beneficiary, _m(amount), raised.isoformat() if raised else None]
        ssk_rem, ssk_ben = remitter == SSK, beneficiary == SSK
        if sign is None or ssk_rem == ssk_ben:  # unknown type, or SSK on both / neither side
            out.not_in_sign_table.append(row)
            continue
        signed = g.signed_adjustment(adjtype, ssk_rem, amount)
        cycle = g.dispute_cycle_for(raised, business_date)
        if cycle is None:
            out.unassigned.append(row + [_m(signed)])
            continue
        out.explained[cycle].add(signed)
        out.rows.append(row + [cycle, _m(signed)])
    return out


# ---------------------------------------------------------------------------
# rule results: SOP items and the Z list
# ---------------------------------------------------------------------------
def _rule_items(payment_module, business_date, cycle_codes):
    """(rule counts, SOP items [rrn, rule, G], Z items [rrn, rule, cycle, side, amount, legs],
    results whose rule no G / Z covers {rule: count})."""
    current = RuleResult.objects.current().filter(payment_module=payment_module, business_date=business_date,
                                                  match_group__is_superseded=False)
    counts = dict(current.values_list("rule_code").annotate(n=Count("id")).order_by("rule_code"))
    covered = {g.MATCHED_RULE, *g.G03_RULES, *g.SOP_ITEM_RULES, *g.Z_RULES}
    not_covered = {r: n for r, n in counts.items() if r not in covered}
    wanted = list(g.SOP_ITEM_RULES) + list(g.Z_RULES) + list(not_covered)
    results = list(current.filter(rule_code__in=wanted).values_list("match_group_id", "rrn", "rule_code"))
    legs: dict[int, dict] = defaultdict(dict)
    group_ids = [r[0] for r in results]
    for start in range(0, len(group_ids), _BATCH):
        for gid, leg, present, amount, direction, cycle_id in RegisterPresence.objects.filter(
            match_group_id__in=group_ids[start: start + _BATCH], leg__in=(CBS_LEG, NPCI_LEG)
        ).values_list("match_group_id", "leg", "present", "amount", "direction",
                      "transaction_record__settlement_cycle_id"):
            if present:
                legs[gid][leg] = (amount, direction, cycle_id)
    sop_items, z_items, other_items = [], [], []
    for gid, rrn, rule in results:
        if rule in g.SOP_ITEM_RULES:
            sop_items.append((rrn, rule, g.SOP_ITEM_RULES[rule]))
            continue
        npci, cbs = legs[gid].get(NPCI_LEG), legs[gid].get(CBS_LEG)
        src = npci or cbs
        item = [rrn, rule, cycle_codes.get(npci[2]) if npci else None,
                g.side_of_npci_direction(npci[1]) if npci else None, _m(src[0]) if src else None,
                "+".join(k for k in (CBS_LEG, NPCI_LEG) if legs[gid].get(k)) or "-"]
        (z_items if rule in g.Z_RULES else other_items).append(item)
    return counts, sop_items, z_items, other_items, not_covered


def _cbs_side(payment_module, business_date, item_rrns):
    base = TransactionFact.objects.filter(payment_module=payment_module, business_date=business_date,
                                          source_register_type=CBS_REGISTER_TYPE, uploaded_file__is_current=True)
    valid = base.filter(rrn_valid=True)
    totals = {d: (n, s or ZERO) for d, n, s in valid.values_list("direction").annotate(n=Count("id"), s=Sum("amount"))}
    by_rrn: dict[str, Decimal] = defaultdict(lambda: ZERO)
    rrns = sorted(item_rrns)
    for start in range(0, len(rrns), _BATCH):
        for rrn, direction, amount in valid.filter(rrn__in=rrns[start: start + _BATCH]).values_list(
                "rrn", "direction", "amount"):
            if direction == Direction.CREDIT:
                by_rrn[rrn] += amount
            elif direction == Direction.DEBIT:
                by_rrn[rrn] -= amount
    invalid = base.filter(rrn_valid=False).aggregate(n=Count("id"), s=Sum("amount"))
    return totals, by_rrn, invalid


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------
def _within(amount: Decimal, count: int, tolerance) -> bool:
    return abs(amount) <= tolerance.amount and abs(count) <= tolerance.txn_count


def _supersede(payment_module, business_date, now) -> None:
    SettlementResult.objects.filter(payment_module=payment_module, business_date=business_date,
                                    is_current=True).update(is_current=False, superseded_at=now)


@transaction.atomic
def reconcile_settlement_date(payment_module: PaymentModule, business_date: date, *, source: str = "",
                              sync_case: bool = True) -> ReconcileOutcome:
    now = timezone.now()
    tolerance = active_tolerance(payment_module)
    try:
        map_version, classifier = active_classifier(payment_module)
    except NoActiveMapping as exc:
        map_version, classifier = None, None
        not_ready = [str(exc)]
        selfcheck, run = {}, None
    else:
        not_ready, selfcheck, run = _readiness(payment_module, business_date)

    common = dict(payment_module=payment_module, business_date=business_date, map_version=map_version,
                  tolerance=tolerance, tolerance_amount=tolerance.amount, tolerance_count=tolerance.txn_count,
                  rule_run=run, computed_at=now, source=source)
    if not_ready:
        _supersede(payment_module, business_date, now)
        result = SettlementResult.objects.create(
            status=ResultStatus.NOT_READY, reasons=not_ready,
            snapshot={"rules": RULES_REF, "self_check": selfcheck, "not_ready": not_ready}, **common,
        )
        logger.warning("Settlement %s %s NOT_READY: %s", payment_module.code, business_date, not_ready)
        outcome = ReconcileOutcome(result)
        if sync_case:
            outcome.case_action, outcome.case_id = cases.sync_settlement_case(result, source=source)
        return outcome

    cycles = {c.pk: c for c in SettlementCycle.objects.filter(payment_module=payment_module)}
    cycle_codes = {pk: c.cycle_code for pk, c in cycles.items()}
    cycle_by_code = {c.cycle_code: c for c in cycles.values()}

    ntsl = _ntsl_side(payment_module, business_date, classifier)
    npci = _npci_side(payment_module, business_date, cycle_codes)
    disputes = _dispute_side(payment_module, business_date)
    rule_counts, sop_items, z_items, other_items, not_covered = _rule_items(payment_module, business_date,
                                                                            cycle_codes)
    cbs_totals, cbs_by_rrn, cbs_invalid = _cbs_side(payment_module, business_date, {r for r, _, _ in sop_items})

    # -- cycle x side comparisons ------------------------------------------------
    rows: list[dict] = []
    order = [c.cycle_code for c in sorted(cycles.values(), key=lambda c: (c.cycle_code in g.DISPUTE_CYCLES,
                                                                          len(c.cycle_code), c.cycle_code))]
    keys = set(ntsl) | {cyc for cyc, _ in npci.buckets} | set(disputes.explained)
    for code in [c for c in order if c in keys] + sorted((k for k in keys if k not in order), key=str):
        n = ntsl.get(code, NtslCycle())
        is_dc = code in g.DISPUTE_CYCLES
        for side, ntsl_main, ntsl_rb in ((g.REMITTER, n.remitter, n.remitter_rb),
                                         (g.BENEFICIARY, n.beneficiary, n.beneficiary_rb)):
            by_g = npci.buckets.get((code, side), {})
            ntsl_amount, ntsl_count = ntsl_main.amount + ntsl_rb.amount, ntsl_main.count + ntsl_rb.count
            if is_dc and not by_g and not ntsl_amount and not ntsl_count:
                continue
            matched = sum((by_g[x].amount for x in g.SETTLED_G if x in by_g), ZERO)
            matched_n = sum(by_g[x].count for x in g.SETTLED_G if x in by_g)
            explained = sum((by_g[x].amount for x in g.EXPLAINED_G if x in by_g), ZERO)
            explained_n = sum(by_g[x].count for x in g.EXPLAINED_G if x in by_g)
            rows.append(dict(
                cycle=code, side=side, ntsl_amount=ntsl_amount, ntsl_count=ntsl_count,
                matched_amount=matched, matched_count=matched_n, explained_amount=explained,
                explained_count=explained_n,
                unexplained_amount=ntsl_amount - matched - explained,
                unexplained_count=ntsl_count - matched_n - explained_n,
                detail={
                    "by_rule": {x: b.as_dict() for x, b in sorted(by_g.items())},
                    "ntsl_rb": ntsl_rb.as_dict(), "npci_rb_rows": npci.rb.get((code, side), Bucket()).as_dict(),
                },
            ))
        if is_dc or n.adjustment_net or code in disputes.explained:
            ex = disputes.explained.get(code, Bucket())
            count_compared = n.adjustment_count is not None
            rows.append(dict(
                cycle=code, side=Side.ADJUSTMENT, ntsl_amount=n.adjustment_net, ntsl_count=n.adjustment_count,
                matched_amount=ZERO, matched_count=0, explained_amount=ex.amount, explained_count=ex.count,
                unexplained_amount=n.adjustment_net - ex.amount,
                unexplained_count=(n.adjustment_count - ex.count) if count_compared else 0,
                detail={"by_rule": {g.G09: ex.as_dict()},
                        "note": "" if count_compared else "NTSL gives no count for Net Adjusted Amount; count not "
                                                          "compared"},
            ))
        if n.uncovered:
            rows.append(dict(
                cycle=code, side=Side.UNCOVERED, ntsl_amount=n.uncovered_net, ntsl_count=None, matched_amount=ZERO,
                matched_count=0, explained_amount=ZERO, explained_count=0, unexplained_amount=n.uncovered_net,
                unexplained_count=0, detail={"lines": n.uncovered, "note": "No G rule covers these NTSL lines"},
            ))

    # -- CBS-side SOP check (C1) ----------------------------------------------------
    deposits = cbs_totals.get(Direction.CREDIT, (0, ZERO))
    withdrawals = cbs_totals.get(Direction.DEBIT, (0, ZERO))
    cbs_unknown = cbs_totals.get(Direction.UNKNOWN, (0, ZERO))
    cbs_net = deposits[1] - withdrawals[1]
    ntsl_debit = sum((c.txn_debit for c in ntsl.values()), ZERO)
    ntsl_credit = sum((c.txn_credit for c in ntsl.values()), ZERO)
    ntsl_net = ntsl_debit - ntsl_credit
    sop_difference = cbs_net - ntsl_net
    sop_by_g: dict[str, Bucket] = defaultdict(Bucket)
    sop_rows = []
    for rrn, rule, rule_g in sorted(sop_items):
        contribution = cbs_by_rrn.get(rrn, ZERO) - npci.ntsl_net_by_rrn.get(rrn, ZERO)
        sop_by_g[rule_g].add(contribution)
        sop_rows.append([rrn, rule, rule_g, _m(contribution)])
    sop_explained = sum((b.amount for b in sop_by_g.values()), ZERO)
    sop_unexplained = sop_difference - sop_explained

    # -- status ----------------------------------------------------------------------------
    reasons: list[str] = []
    for r in rows:
        r["within_tolerance"] = _within(r["unexplained_amount"], r["unexplained_count"], tolerance)
        if not r["within_tolerance"]:
            reasons.append(f"{r['cycle'] or 'no cycle'} {r['side']}: unexplained Rs {_m(r['unexplained_amount'])}, "
                           f"{r['unexplained_count']} txn")
    if abs(sop_unexplained) > tolerance.amount:
        reasons.append(f"SOP check (CBS vs NTSL): unexplained Rs {_m(sop_unexplained)}")
    if npci.not_placed:
        reasons.append(f"{len(npci.not_placed)} NTSL-settled NPCI row(s) have no debit/credit direction")
    if cbs_unknown[0]:
        reasons.append(f"{cbs_unknown[0]} valid-RRN CBS row(s) have no debit/credit direction (Rs {_m(cbs_unknown[1])})")
    status = ResultStatus.UNEXPLAINED if reasons else ResultStatus.TALLIED

    fees_by_cycle = {code: {cat: {"count": f["count"], "debit": _m(f["debit"]), "credit": _m(f["credit"])}
                            for cat, f in sorted(c.fees.items())} for code, c in ntsl.items()}
    fees_net = sum((c.fees_net for c in ntsl.values()), ZERO)
    snapshot = {
        "rules": RULES_REF,
        "map_version": map_version.version,
        "tolerance": {"version": tolerance.version, "amount": _m(tolerance.amount), "txn_count": tolerance.txn_count},
        "rule_run_id": run.pk if run else None,
        "rule_counts": rule_counts,
        "self_check": selfcheck,
        "cycles": [{**{k: (_m(v) if isinstance(v, Decimal) else v) for k, v in r.items()}} for r in rows],
        "sop": {
            "formula": "(CBS deposits - withdrawals, valid-RRN rows) - (NTSL TXN debit - credit) = explained (G04-G08)",
            "cbs_deposits": {"count": deposits[0], "amount": _m(deposits[1])},
            "cbs_withdrawals": {"count": withdrawals[0], "amount": _m(withdrawals[1])},
            "cbs_net": _m(cbs_net), "ntsl_txn_debit": _m(ntsl_debit), "ntsl_txn_credit": _m(ntsl_credit),
            "ntsl_net": _m(ntsl_net), "difference": _m(sop_difference),
            "explained_by_rule": {k: b.as_dict() for k, b in sorted(sop_by_g.items())},
            "explained": _m(sop_explained), "unexplained": _m(sop_unexplained),
            "items": sop_rows,
            "cbs_rows_without_valid_rrn": {"count": cbs_invalid["n"], "amount": _m(cbs_invalid["s"]),
                                           "note": "outside RRN matching and the SOP check (ops P4)"},
        },
        "g09": {
            "cutoff": "DC1 before 16:00 IST, DC2 after (Uid time; ops to confirm, P2)",
            "rows": disputes.rows, "no_money_rows": dict(disputes.no_money),
            "not_in_sign_table": disputes.not_in_sign_table, "unassigned": disputes.unassigned,
            "row_format": ["rrn", "adjtype", "remitter", "beneficiary", "amount", "raised_at", "cycle", "signed"],
        },
        "g10": {"note": "NTSL-internal only until ops answers Q3", "net": _m(fees_net), "by_cycle": fees_by_cycle},
        "contributing": npci.contributing,
        "contributing_format": ["rrn", "g_rule", "rule", "cycle", "side", "amount", "npci_code", "role"],
        "not_placed": npci.not_placed,
        "npci_not_settled_by_code": dict(npci.not_settled),
        "z": {"rules": list(g.Z_RULES), "items": z_items,
              "item_format": ["rrn", "rule", "cycle", "side", "amount", "legs"],
              "adjustment_rows_no_money": dict(disputes.no_money)},
        "rules_not_covered": {"counts": not_covered, "items": other_items},
    }

    _supersede(payment_module, business_date, now)
    result = SettlementResult.objects.create(
        status=status, reasons=reasons,
        sop_cbs_net=cbs_net, sop_ntsl_net=ntsl_net, sop_difference=sop_difference, sop_explained=sop_explained,
        sop_unexplained=sop_unexplained,
        unexplained_amount=sum((abs(r["unexplained_amount"]) for r in rows), ZERO),
        unexplained_count=sum(abs(r["unexplained_count"]) for r in rows),
        fees_net=fees_net, snapshot=snapshot, **common,
    )
    SettlementCycleResult.objects.bulk_create([
        SettlementCycleResult(
            result=result, settlement_cycle=cycle_by_code.get(r["cycle"]), side=r["side"],
            ntsl_amount=r["ntsl_amount"], ntsl_count=r["ntsl_count"], matched_amount=r["matched_amount"],
            matched_count=r["matched_count"], explained_amount=r["explained_amount"],
            explained_count=r["explained_count"], unexplained_amount=r["unexplained_amount"],
            unexplained_count=r["unexplained_count"], within_tolerance=r["within_tolerance"], detail=r["detail"],
        )
        for r in rows
    ])
    outcome = ReconcileOutcome(result)
    if sync_case:
        outcome.case_action, outcome.case_id = cases.sync_settlement_case(result, source=source)
    return outcome


RULES_REF = "G01-G10 / Z, owner-approved 2026-10-11 (docs/phase4_gap_rules_draft_v1.txt)"
