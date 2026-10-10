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
  NEXT_BUSINESS_DATE_LOAD -> no due date (only retired v3.1's R13 used it)
  tat_cutoff_time (R05: 16:00) is stored next to the due date.

One business date at a time (owner, v3.2 amendment 2026-10-10: "we only
work on that particular day"): evaluating D reads only D's matrix and
writes only D's results. There is no cross-day check in either direction --
the v3.1-era R13 next-day re-check / R12 mirror was removed. A payment NPCI
records on D and CBS posts on D+1 shows up as R12 on D and R13 on D+1, two
separate items that ops resolves by hand (R13 is a T+1 review item in v3.2).
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
from django.utils import timezone

from core.models import PaymentModule
from core.working_days import WorkingDayCalendar
from reconciliation.models import TransactionMatchGroup
from rule_engine.conditions import facts_used
from rule_engine.facts import GroupFacts, extract_facts
from rule_engine.models import (
    REASON_TEMPLATE_PLACEHOLDERS,
    DayBasis,
    Priority,
    Rule,
    RuleEvaluationRun,
    RuleResult,
    RuleSet,
    RuleSetStatus,
    TatType,
)

logger = logging.getLogger(__name__)

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
    triggered_by: str = "",
    calendar: WorkingDayCalendar | None = None,
) -> EvaluationSummary:
    started = time_module.perf_counter()
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

        order = {c.rule.code: i for i, c in enumerate(compiled)}
        counts = Counter(r["rule_code"] for r in rows)
        summary = EvaluationSummary(
            payment_module_code=payment_module.code,
            business_date=business_date,
            rule_set_version=rule_set.version_label,
            run_id=run.pk,
            group_count=len(rows),
            superseded_count=superseded,
            rule_counts={code: counts.get(code, 0) for code in sorted(order, key=order.get)},
            calendar_incomplete_count=sum(r["calendar_incomplete"] for r in rows),
            tat_unresolved_count=sum(r["tat_unresolved"] for r in rows),
            groups_missing_cbs_or_npci_presence=sum(1 for gf in group_facts if gf.missing_legs),
        )
        summary.duration_seconds = round(time_module.perf_counter() - started, 2)
        run.finished_at = timezone.now()
        run.duration_seconds = summary.duration_seconds
        run.group_count = summary.group_count
        run.rule_counts = summary.rule_counts
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
