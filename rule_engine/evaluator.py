"""
Business Rule Engine evaluator (Phase 2 Step 4, approved by the project
owner 2026-10-10).

evaluate_business_date(module, D) applies the module's ACTIVE rule set to
every current TransactionMatchGroup of D and records one RuleResult per
group (first rule in evaluation_order whose condition is true wins; the
catch-all R99 guarantees exactly one). Re-running D supersedes D's current
results (kept, never deleted) and recreates them in one transaction.

Rules only RECOMMEND. Nothing here moves money, produces an NPCI file or
notifies anyone; maker-checker is Phase 3.

Conditions are compiled once per run into Python closures that follow
rule_engine.conditions' semantics exactly -- in particular a leaf whose
fact is null is FALSE whatever the operator (SQL's UNKNOWN, as the oracle
docs/phase2_rule_table_v3_1_reference.sql relies on).

Deadlines (Rule.tat_*; working days from core.working_days):
  T  = the group's business date, except for a rule whose condition reads
       the Adjustment overlay (adjustment.* facts) -- then T = the
       Adjustment's own date (Adjdate), since an overlay can sit on its
       original Txndate's group and the clock must not start before the
       adjustment arrived.
  DAYS WORKING n  -> the n-th working day after T (calendar_incomplete when
                     a year with no holidays loaded was crossed)
  DAYS CALENDAR n -> T + n days, not shifted (owner 2026-10-10)
  BY_ADJTYPE      -> the first RuleTat row whose condition matches; none ->
                     no deadline, tat_unresolved=True
  NEXT_BUSINESS_DATE_LOAD (R13) -> no fixed deadline; see the re-check below
  tat_cutoff_time (R05: 16:00) is stored next to the due date.

R13 next-day re-check (owner Q3) and its R12 mirror -- modelled as
RuleResult.recheck_state + cross-links, not as extra rules:
  "next business date" of D = D + 1 calendar day (UPI runs 24x7).
  D+1 NPCI "loaded" = a current UploadedFile in LOADED status, of an NPCI
  leg file type (Raw Data / Merchant Raw Data / BBP), with business_date
  D+1 -- not the mere presence of facts dated D+1.
  "Found" = D+1 has an NPCI row for the RRN whose response code is in
  RECHECK_CLEARING_CODES (00 / RB; owner 2026-10-10). A row with any other
  code does not count: the item is handled exactly as "not found". The
  codes seen are recorded on the result (next_day_npci_codes).
  For each current R13 result on D:
    found                         -> matched_next_day: Reconciled (late
        cycle), next_day_fact (the clearing row) / next_day_group recorded,
        linked to D+1's current R12 result if there is one.
    not found, D+1 NPCI loaded    -> escalated: Reversal Required, HIGH, due
        1 working day after the escalation date (first escalation, kept
        across re-runs).
    not found, not loaded         -> awaiting_next_day: R13's own defaults.
  Mirror: a current R12 result on D+1 whose RRN is a current R13 result on
  D that is matched_next_day -> matched_previous_day: Reconciled, linked to
  the D result (a late match, not an adjustment). R12 is NPCI code 00 by
  definition (R11 takes every other code), so the clearing-code rule never
  blocks the mirror; requiring the D side to be matched_next_day keeps the
  two sides symmetric anyway. An R12 that loses that link is reset to R12's
  own defaults.
  refresh_next_day_recheck(D) runs after evaluating D (D's R13s, D+1's R12s)
  and after evaluating D+1 (same pair), so whichever date is evaluated
  second updates the other date's current results in place. It never
  changes which rule fired.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import string
import time as time_module
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from datetime import time as dt_time
from typing import Callable

from django.db import connection, transaction
from django.db.models import JSONField
from django.db.models import Min
from django.utils import timezone

from core.models import PaymentModule, RegisterType
from core.working_days import WorkingDayCalendar
from file_processing.models import ProcessingStatus as FileProcessingStatus
from file_processing.models import UploadedFile
from reconciliation.models import TransactionMatchGroup
from reconciliation.presence import DEFAULT_PRESENCE_LEGS
from reconciliation.presence import _read_cells  # same cell reading/cleaning as the matrix
from rule_engine.conditions import facts_used
from rule_engine.facts import GroupFacts, extract_facts
from rule_engine.models import (
    REASON_TEMPLATE_PLACEHOLDERS,
    DayBasis,
    Priority,
    RecheckState,
    Rule,
    RuleEvaluationRun,
    RuleResult,
    RuleSet,
    RuleSetStatus,
    StatusCategory,
    TatType,
)
from transactions.models import TransactionFact

logger = logging.getLogger(__name__)

# Rule codes the re-check is about (UPI rule set v3.1: R13 "In CBS only",
# R12 "In NPCI only - successful"). If a future rule set drops either code,
# the re-check simply finds no results for it.
RECHECK_RULE_CODE = "R13"
MIRROR_RULE_CODE = "R12"
# A D+1 NPCI row clears a CBS-only (R13) item only with one of these codes
# (owner decision 2026-10-10): 00 = success, RB = deemed approved (Q4). Any
# other code -- declined, incl. BT, or a blank cell -- is NOT a match and is
# escalated exactly like "not found" (NPCI OC 93 FY19-20: a declined
# transaction where the customer was debited must be reversed by T+1).
RECHECK_CLEARING_CODES = frozenset({"00", "RB"})
NPCI_REGISTER_TYPES = list(DEFAULT_PRESENCE_LEGS["NPCI"])
_BULK_BATCH = 2000


class NoActiveRuleSet(Exception):
    pass


# ---------------------------------------------------------------------------
# Condition compilation
# ---------------------------------------------------------------------------
Predicate = Callable[[dict], bool]


def _leaf(fact: str, op: str, value) -> Predicate:
    if op in ("in", "not_in"):
        values = frozenset(value)
    if op == "icontains_any":
        needles = [v.lower() for v in value]

    def test(v) -> bool:
        if op == "eq":
            return v == value
        if op == "ne":
            return v != value
        if op == "in":
            return v in values
        if op == "not_in":
            return v not in values
        if op == "icontains_any":
            low = v.lower()
            return any(n in low for n in needles)
        if op == "gt":
            return v > value
        if op == "gte":
            return v >= value
        if op == "lt":
            return v < value
        if op == "lte":
            return v <= value
        raise ValueError(f"unknown operator {op!r}")

    def predicate(facts: dict) -> bool:
        v = facts[fact]
        return v is not None and test(v)  # null fact -> FALSE, whatever the operator

    return predicate


def compile_condition(node: dict) -> Predicate:
    """A validated condition (rule_engine.conditions format) -> predicate."""
    if "all" in node:
        children = [compile_condition(c) for c in node["all"]]
        return lambda facts: all(c(facts) for c in children)
    if "any" in node:
        children = [compile_condition(c) for c in node["any"]]
        return lambda facts: any(c(facts) for c in children)
    if node.get("always") is True:
        return lambda facts: True
    return _leaf(node["fact"], node["op"], node["value"])


@dataclass
class CompiledRule:
    rule: Rule
    predicate: Predicate
    tat_rows: list  # [(RuleTat, predicate)]
    clock_on_adjustment: bool  # condition reads adjustment.* facts -> T = Adjdate


def compile_rule_set(rule_set: RuleSet) -> list[CompiledRule]:
    rules = rule_set.rules.order_by("evaluation_order").select_related("response_code").prefetch_related("tat_rows")
    return [
        CompiledRule(
            rule=rule,
            predicate=compile_condition(rule.condition),
            tat_rows=[(row, compile_condition(row.condition)) for row in rule.tat_rows.order_by("evaluation_order")],
            clock_on_adjustment=any(f.startswith("adjustment.") for f in facts_used(rule.condition)),
        )
        for rule in rules
    ]


def first_match(compiled: list[CompiledRule], facts: dict) -> CompiledRule:
    for c in compiled:
        if c.predicate(facts):
            return c
    raise RuntimeError("No rule matched; the active rule set must end with a catch-all")


# ---------------------------------------------------------------------------
# Outcome (status / priority / deadline) of a rule
# ---------------------------------------------------------------------------
@dataclass
class Outcome:
    status_category: str
    priority: str
    due_date: date | None = None
    due_cutoff_time: object = None
    tat_label: str = ""
    tat_unresolved: bool = False
    calendar_incomplete: bool = False
    rbi_compensation: bool = False

    FIELDS = (
        "status_category",
        "priority",
        "due_date",
        "due_cutoff_time",
        "tat_label",
        "tat_unresolved",
        "calendar_incomplete",
        "rbi_compensation",
    )

    def apply(self, result: RuleResult) -> None:
        for name in self.FIELDS:
            setattr(result, name, getattr(self, name))

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.FIELDS}


def _add_days(t: date, days: int, basis: str, calendar: WorkingDayCalendar) -> tuple[date, bool, str]:
    if basis == DayBasis.WORKING:
        r = calendar.add_working_days(t, days)
        return r.date, r.calendar_incomplete, f"T+{days} working day{'s' if days != 1 else ''}"
    return t + timedelta(days=days), False, f"{days} calendar days"


def rule_outcome(compiled: CompiledRule, tat_start: date, facts: dict, calendar: WorkingDayCalendar) -> Outcome:
    """The fired rule's own outcome (no re-check override)."""
    rule = compiled.rule
    out = Outcome(
        status_category=rule.status_category,
        priority=rule.priority,
        rbi_compensation=rule.rbi_compensation_after_tat,
    )
    if rule.tat_type == TatType.DAYS:
        out.due_date, out.calendar_incomplete, out.tat_label = _add_days(
            tat_start, rule.tat_days, rule.tat_day_basis, calendar
        )
    elif rule.tat_type == TatType.BY_ADJTYPE:
        row = next((r for r, pred in compiled.tat_rows if pred(facts)), None)
        if row is None:
            out.tat_unresolved = True
            out.tat_label = "No TAT row matches this Adjtype"
        else:
            out.due_date, out.calendar_incomplete, label = _add_days(tat_start, row.tat_days, row.tat_day_basis, calendar)
            out.tat_label = f"{row.label}: {label}"
    elif rule.tat_type == TatType.NEXT_BUSINESS_DATE_LOAD:
        out.tat_label = "Next business date's NPCI load"
    if out.due_date is not None and rule.tat_cutoff_time is not None:
        out.due_cutoff_time = rule.tat_cutoff_time
        out.tat_label += f", by {rule.tat_cutoff_time:%H:%M}"
    return out


def render_reason(template: str, facts: dict) -> str:
    if not template:
        return ""
    values = {}
    for _, name, _, _ in string.Formatter().parse(template):
        if name:
            v = facts.get(REASON_TEMPLATE_PLACEHOLDERS[name])
            values[name] = "unknown" if v is None else v
    return template.format(**values)


# ---------------------------------------------------------------------------
# Evaluation of one business date
# ---------------------------------------------------------------------------
@dataclass
class EvaluationSummary:
    payment_module_code: str
    business_date: date
    rule_set_version: str
    run_id: int
    group_count: int = 0
    superseded_count: int = 0
    rule_counts: dict = field(default_factory=dict)
    recheck_counts: dict = field(default_factory=dict)
    previous_date_recheck_counts: dict = field(default_factory=dict)
    calendar_incomplete_count: int = 0
    tat_unresolved_count: int = 0
    groups_missing_cbs_or_npci_presence: int = 0
    duration_seconds: float = 0.0

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["business_date"] = self.business_date.isoformat()
        return d


def active_rule_set(payment_module: PaymentModule) -> RuleSet:
    rule_set = RuleSet.objects.filter(payment_module=payment_module, status=RuleSetStatus.ACTIVE).first()
    if rule_set is None:
        raise NoActiveRuleSet(f"No ACTIVE rule set for {payment_module.code}")
    return rule_set


def _build_row(gf: GroupFacts, compiled: CompiledRule, rule_set: RuleSet, run_id: int, now, calendar) -> dict:
    """One RuleResult as a {column attname: value} dict (written by COPY)."""
    rule = compiled.rule
    tat_start = gf.adjustment_date if (compiled.clock_on_adjustment and gf.adjustment_date) else gf.business_date
    row = dict(
        payment_module_id=rule_set.payment_module_id,
        match_group_id=gf.group_id,
        business_date=gf.business_date,
        rrn=gf.rrn,
        run_id=run_id,
        rule_set_id=rule_set.pk,
        rule_id=rule.pk,
        rule_code=rule.code,
        action=rule.action,
        response_code_id=rule.response_code_id,
        reason=render_reason(rule.reason_template, gf.facts),
        tat_start_date=tat_start,
        facts=gf.facts,
        evaluated_at=now,
        recheck_state=RecheckState.AWAITING_NEXT_DAY if rule.code == RECHECK_RULE_CODE else RecheckState.NONE,
    )
    row.update(rule_outcome(compiled, tat_start, gf.facts, calendar).as_dict())
    return row


def _csv_value(field, value):
    if value is None:
        return None  # unquoted empty -> NULL in COPY csv
    if isinstance(field, JSONField):
        return json.dumps(value)
    if isinstance(value, (date, dt_time)):  # date, datetime and time
        return value.isoformat()
    if isinstance(value, str):
        return str(value)  # TextChoices members -> plain str
    return value


def insert_results(rows: list[dict]) -> None:
    """Writes RuleResult rows with PostgreSQL COPY. bulk_create spent ~45 s
    per date on Django's per-field preparation (2.4M prepare_value calls for
    ~79K rows, measured 2026-10-10); COPY takes a few seconds. Bypasses
    Model.save() -- RuleResult has no custom save -- but not the DB
    constraints (FKs, the one-current-result-per-group unique index).
    Other databases fall back to bulk_create."""
    if not rows:
        return
    fields = [f for f in RuleResult._meta.concrete_fields if not f.primary_key]
    if connection.vendor != "postgresql":
        RuleResult.objects.bulk_create([RuleResult(**row) for row in rows], batch_size=_BULK_BATCH)
        return
    defaults = {f.attname: f.get_default() for f in fields}
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_STRINGS, lineterminator=chr(10))
    for row in rows:
        writer.writerow([_csv_value(f, row.get(f.attname, defaults[f.attname])) for f in fields])
    buf.seek(0)
    columns = ", ".join(connection.ops.quote_name(f.column) for f in fields)
    table = connection.ops.quote_name(RuleResult._meta.db_table)
    with connection.cursor() as cursor:
        cursor.cursor.copy_expert(f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv)", buf)


def evaluate_business_date(
    payment_module: PaymentModule,
    business_date: date,
    *,
    today: date | None = None,
    triggered_by: str = "",
    calendar: WorkingDayCalendar | None = None,
) -> EvaluationSummary:
    started = time_module.perf_counter()
    today = today or timezone.localdate()
    calendar = calendar or WorkingDayCalendar()
    with transaction.atomic():
        rule_set = active_rule_set(payment_module)
        compiled = compile_rule_set(rule_set)
        now = timezone.now()
        group_facts = extract_facts(payment_module, business_date)

        superseded = RuleResult.objects.filter(
            payment_module=payment_module, business_date=business_date, is_superseded=False
        ).update(is_superseded=True, superseded_at=now)

        run = RuleEvaluationRun.objects.create(
            payment_module=payment_module,
            business_date=business_date,
            rule_set=rule_set,
            started_at=now,
            superseded_count=superseded,
            triggered_by=triggered_by,
        )
        rows = [_build_row(gf, first_match(compiled, gf.facts), rule_set, run.pk, now, calendar) for gf in group_facts]
        insert_results(rows)

        # D's R13s against D+1, then D-1's R13s against D (D's R12 mirror).
        refresh_next_day_recheck(payment_module, business_date, today=today, calendar=calendar)
        previous = refresh_next_day_recheck(
            payment_module, business_date - timedelta(days=1), today=today, calendar=calendar
        )

        order = {c.rule.code: i for i, c in enumerate(compiled)}
        counts = Counter(r["rule_code"] for r in rows)
        current = RuleResult.objects.filter(
            payment_module=payment_module, business_date=business_date, is_superseded=False
        )
        recheck_counts = Counter(current.exclude(recheck_state="").values_list("recheck_state", flat=True))
        summary = EvaluationSummary(
            payment_module_code=payment_module.code,
            business_date=business_date,
            rule_set_version=rule_set.version_label,
            run_id=run.pk,
            group_count=len(rows),
            superseded_count=superseded,
            rule_counts={code: counts.get(code, 0) for code in sorted(order, key=order.get)},
            recheck_counts=dict(recheck_counts),
            previous_date_recheck_counts=previous,
            calendar_incomplete_count=current.filter(calendar_incomplete=True).count(),
            tat_unresolved_count=sum(r["tat_unresolved"] for r in rows),
            groups_missing_cbs_or_npci_presence=sum(1 for gf in group_facts if gf.missing_legs),
        )
        summary.duration_seconds = round(time_module.perf_counter() - started, 2)
        run.finished_at = timezone.now()
        run.duration_seconds = summary.duration_seconds
        run.group_count = summary.group_count
        run.rule_counts = summary.rule_counts
        run.recheck_counts = summary.recheck_counts
        run.calendar_incomplete_count = summary.calendar_incomplete_count
        run.save()
    if summary.groups_missing_cbs_or_npci_presence:
        logger.warning(
            "%s %s: %d groups lack a CBS or NPCI presence row (evaluated as absent)",
            payment_module.code,
            business_date,
            summary.groups_missing_cbs_or_npci_presence,
        )
    return summary


# ---------------------------------------------------------------------------
# R13 next-day re-check and R12 mirror
# ---------------------------------------------------------------------------
def next_business_date(d: date) -> date:
    """UPI runs 24x7: NPCI's business dates are calendar days."""
    return d + timedelta(days=1)


def npci_loaded(payment_module: PaymentModule, d: date) -> bool:
    return UploadedFile.objects.filter(
        payment_module=payment_module,
        business_date=d,
        file_type_config__register_type__in=NPCI_REGISTER_TYPES,
        is_current=True,
        processing_status=FileProcessingStatus.LOADED,
    ).exists()


@dataclass
class NextDayNpci:
    """D+1's NPCI-leg rows for one RRN."""

    clearing_fact_id: int | None  # first row whose code is in RECHECK_CLEARING_CODES, else None
    codes: list[str]  # every response code seen, in pick order ('' = blank cell)


def _next_day_npci_by_rrn(payment_module: PaymentModule, d: date, rrns: list[str]) -> dict[str, NextDayNpci]:
    """{rrn: NextDayNpci} for D's NPCI-leg rows (current files, valid RRNs).
    Rows are ordered by the presence engine's register-type priority, then
    id; the response code is read from the source row's raw cells exactly as
    the presence engine reads it (reconciliation.presence._read_cells, cell
    NPCI_RESPONSE_CODE_CELL)."""
    priority = {rt: i for i, rt in enumerate(NPCI_REGISTER_TYPES)}
    facts: list[TransactionFact] = []
    for start in range(0, len(rrns), 5000):
        facts += TransactionFact.objects.filter(
            payment_module=payment_module,
            business_date=d,
            rrn__in=rrns[start : start + 5000],
            rrn_valid=True,
            source_register_type__in=NPCI_REGISTER_TYPES,
            uploaded_file__is_current=True,
        ).only("id", "rrn", "source_register_type", "staging_row_id")
    cells = _read_cells(facts)
    out: dict[str, NextDayNpci] = {}
    for fact in sorted(facts, key=lambda f: (priority[f.source_register_type], f.pk)):
        code = cells.get(fact.pk, {}).get("response_code", "")
        entry = out.setdefault(fact.rrn, NextDayNpci(None, []))
        entry.codes.append(code)
        if entry.clearing_fact_id is None and code in RECHECK_CLEARING_CODES:
            entry.clearing_fact_id = fact.pk
    return out


_RECHECK_FIELDS = Outcome.FIELDS + (
    "recheck_state",
    "recheck_checked_at",
    "escalated_on",
    "next_day_fact",
    "next_day_group",
    "next_day_npci_codes",
    "linked_result",
)


def _defaults(result: RuleResult, compiled_by_rule: dict, calendar) -> Outcome:
    return rule_outcome(compiled_by_rule[result.rule_id], result.tat_start_date, result.facts, calendar)


def refresh_next_day_recheck(
    payment_module: PaymentModule, d: date, *, today: date | None = None, calendar: WorkingDayCalendar | None = None
) -> dict:
    """Updates D's current R13 results and D+1's current R12 results in
    place (see module docstring). Returns {state: count} for D's R13s."""
    today = today or timezone.localdate()
    d1 = next_business_date(d)
    current = RuleResult.objects.filter(payment_module=payment_module, is_superseded=False)
    r13s = list(current.filter(business_date=d, rule_code=RECHECK_RULE_CODE).select_related("rule"))
    r12s = list(current.filter(business_date=d1, rule_code=MIRROR_RULE_CODE).select_related("rule"))
    if not r13s and not any(r.recheck_state for r in r12s):
        return {}
    calendar = calendar or WorkingDayCalendar()
    rule_sets = {r.rule_set_id for r in (*r13s, *r12s)}
    compiled_by_rule = {
        c.rule.pk: c for rs in RuleSet.objects.filter(pk__in=rule_sets) for c in compile_rule_set(rs)
    }
    now = timezone.now()
    loaded = npci_loaded(payment_module, d1)
    rrns = sorted({r.rrn for r in r13s})
    next_day = _next_day_npci_by_rrn(payment_module, d1, rrns) if rrns else {}
    found_facts = {rrn: e.clearing_fact_id for rrn, e in next_day.items() if e.clearing_fact_id is not None}
    next_groups = dict(
        TransactionMatchGroup.objects.filter(
            payment_module=payment_module, business_date=d1, rrn__in=list(found_facts), is_superseded=False
        ).values_list("rrn", "id")
    )
    r12_by_rrn = {r.rrn: r for r in r12s}
    r13_by_rrn = {r.rrn: r for r in r13s}
    first_escalation = dict(
        RuleResult.objects.filter(
            match_group_id__in=[r.match_group_id for r in r13s], escalated_on__isnull=False
        )
        .values("match_group_id")
        .annotate(first=Min("escalated_on"))
        .values_list("match_group_id", "first")
    )

    reconciled = Outcome(status_category=StatusCategory.RECONCILED, priority=Priority.NONE)
    states = Counter()
    for r in r13s:
        r.recheck_checked_at = now
        r.next_day_fact_id = r.next_day_group_id = r.linked_result_id = None
        r.next_day_npci_codes = ",".join(next_day[r.rrn].codes)[:50] if r.rrn in next_day else ""
        if r.rrn in found_facts:
            reconciled.apply(r)
            r.recheck_state = RecheckState.MATCHED_NEXT_DAY
            r.next_day_fact_id = found_facts[r.rrn]
            r.next_day_group_id = next_groups.get(r.rrn)
            mirror = r12_by_rrn.get(r.rrn)
            r.linked_result_id = mirror.pk if mirror else None
        elif loaded:
            r.escalated_on = first_escalation.get(r.match_group_id) or r.escalated_on or today
            due = calendar.add_working_days(r.escalated_on, 1)
            Outcome(
                status_category=StatusCategory.REVERSAL_REQUIRED,
                priority=Priority.HIGH,
                due_date=due.date,
                tat_label="T+1 working day from escalation",
                calendar_incomplete=due.calendar_incomplete,
            ).apply(r)
            r.recheck_state = RecheckState.ESCALATED
        else:
            _defaults(r, compiled_by_rule, calendar).apply(r)
            r.recheck_state = RecheckState.AWAITING_NEXT_DAY
        states[str(r.recheck_state)] += 1

    changed_r12 = []
    for r in r12s:
        partner = r13_by_rrn.get(r.rrn)
        if partner is not None and partner.recheck_state == RecheckState.MATCHED_NEXT_DAY:
            reconciled.apply(r)
            r.recheck_state = RecheckState.MATCHED_PREVIOUS_DAY
            r.linked_result_id = partner.pk
            r.recheck_checked_at = now
            changed_r12.append(r)
        elif r.recheck_state:
            _defaults(r, compiled_by_rule, calendar).apply(r)
            r.recheck_state = RecheckState.NONE
            r.linked_result_id = None
            r.recheck_checked_at = now
            changed_r12.append(r)

    RuleResult.objects.bulk_update(r13s + changed_r12, _RECHECK_FIELDS, batch_size=_BULK_BATCH)
    return dict(states)


def loaded_business_dates(payment_module: PaymentModule) -> list[date]:
    """Business dates with current (non-superseded) match groups."""
    # order_by() is required: the model's default ordering includes rrn,
    # which would otherwise make DISTINCT return one row per group.
    return list(
        TransactionMatchGroup.objects.filter(payment_module=payment_module, is_superseded=False)
        .order_by("business_date")
        .values_list("business_date", flat=True)
        .distinct()
    )
