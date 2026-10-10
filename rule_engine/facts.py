"""
Fact extraction for the rule evaluator (Phase 2 Step 4): the ONE place that
turns the presence matrix (reconciliation.TransactionMatchGroup +
RegisterPresence) into the fact vocabulary of rule_engine.conditions.FACTS.

Batched: one business date is read with a fixed number of queries (groups,
presences, Adjustment facts, staging rows, staging-history rows for the
archived ones) whatever the group count -- no per-group query.

Each fact follows its FACTS definition and the reference oracle
(docs/phase2_rule_table_v3_1_reference.sql):
  cbs.present / npci.present      the leg's RegisterPresence.present
  cbs.pair_missing                CBS presence's paired_transaction_record IS NULL
  npci.response_code              NPCI presence's response_code ('' when absent; never null)
  cbs_reversed, is_on_us,
  amount_match, direction_match   the group's columns
  <overlay>.present               a DRC / TIMEOUT / ADJUSTMENT presence row exists
  <overlay>.ssk_side              that row's ssk_side, null without a row
  adjustment.adjtype              that row's dispute_type, null without a row
  adjustment.age_days             Adjdate - Txndate in days. Adjdate = the
                                  Adjustment TransactionFact's business_date;
                                  Txndate = its staging row's txn_date_raw
                                  (DD-MM-YYYY), from the live staging table or,
                                  once archived, the staging history copy (as
                                  TransactionFact.staging_row() and the oracle's
                                  union do). Null when either is unknown.
Groups that are superseded are skipped (the oracle does the same).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from core.models import PaymentModule
from reconciliation.models import RegisterPresence, TransactionMatchGroup
from transactions.models import TransactionFact
from transactions.models.history import StagingAdjustmentHistory
from transactions.models.staging import StagingAdjustment

# Fact prefix -> RegisterPresence.leg (reconciliation.presence DEFAULT_PRESENCE_LEGS /
# DEFAULT_OVERLAY_LEGS).
CBS_LEG, NPCI_LEG = "CBS", "NPCI"
OVERLAY_LEGS = {"drc": "DRC", "timeout": "TIMEOUT", "adjustment": "ADJUSTMENT"}
TXN_DATE_FORMAT = "%d-%m-%Y"  # oracle: to_date(txn_date_raw, 'DD-MM-YYYY')
_BATCH = 5000


@dataclass
class GroupFacts:
    group_id: int
    rrn: str
    business_date: date
    facts: dict
    adjustment_date: date | None = None  # Adjdate, when an Adjustment overlay row exists
    missing_legs: tuple[str, ...] = ()  # CBS/NPCI presence rows absent (data anomaly, counted)


def parse_txn_date(raw: str | None) -> date | None:
    try:
        return datetime.strptime((raw or "").strip(), TXN_DATE_FORMAT).date()
    except ValueError:
        return None


def _txn_dates(staging_ids: list[int]) -> dict[int, date | None]:
    """{staging row id: Txndate} from live staging, falling back to history
    (latest archived copy) for rows already archived."""
    raw: dict[int, str | None] = {}
    for start in range(0, len(staging_ids), _BATCH):
        batch = staging_ids[start : start + _BATCH]
        raw.update(StagingAdjustment.objects.filter(id__in=batch).values_list("id", "txn_date_raw"))
        missing = [i for i in batch if i not in raw]
        if missing:
            for original_id, value in (
                StagingAdjustmentHistory.objects.filter(original_id__in=missing)
                .order_by("original_id", "archived_at")
                .values_list("original_id", "txn_date_raw")
            ):
                raw[original_id] = value  # ordered by archived_at: the latest copy wins
    return {i: parse_txn_date(v) for i, v in raw.items()}


def extract_facts(payment_module: PaymentModule, business_date: date) -> list[GroupFacts]:
    groups = list(
        TransactionMatchGroup.objects.filter(
            payment_module=payment_module, business_date=business_date, is_superseded=False
        )
        .order_by("id")
        .values_list("id", "rrn", "cbs_reversed", "is_on_us", "amount_match", "direction_match")
    )
    presences: dict[int, dict[str, tuple]] = {}
    for group_id, leg, present, record_id, paired_id, code, ssk_side, dispute_type in RegisterPresence.objects.filter(
        match_group__payment_module=payment_module,
        match_group__business_date=business_date,
        match_group__is_superseded=False,
    ).values_list(
        "match_group_id",
        "leg",
        "present",
        "transaction_record_id",
        "paired_transaction_record_id",
        "response_code",
        "ssk_side",
        "dispute_type",
    ):
        presences.setdefault(group_id, {})[leg] = (present, record_id, paired_id, code, ssk_side, dispute_type)

    adjustment_fact_ids = [
        legs[OVERLAY_LEGS["adjustment"]][1]
        for legs in presences.values()
        if OVERLAY_LEGS["adjustment"] in legs and legs[OVERLAY_LEGS["adjustment"]][1] is not None
    ]
    adj_facts = dict(
        (fact_id, (adj_date, staging_id))
        for fact_id, adj_date, staging_id in TransactionFact.objects.filter(id__in=adjustment_fact_ids).values_list(
            "id", "business_date", "staging_row_id"
        )
    )
    txn_dates = _txn_dates([s for _, s in adj_facts.values() if s is not None])

    out: list[GroupFacts] = []
    for group_id, rrn, cbs_reversed, is_on_us, amount_match, direction_match in groups:
        legs = presences.get(group_id, {})
        missing = tuple(leg for leg in (CBS_LEG, NPCI_LEG) if leg not in legs)
        cbs = legs.get(CBS_LEG, (False, None, None, "", None, ""))
        npci = legs.get(NPCI_LEG, (False, None, None, "", None, ""))
        facts = {
            "cbs.present": bool(cbs[0]),
            "cbs.pair_missing": cbs[2] is None,
            "npci.present": bool(npci[0]),
            "npci.response_code": npci[3] or "",
            "cbs_reversed": cbs_reversed,
            "is_on_us": is_on_us,
            "amount_match": amount_match,
            "direction_match": direction_match,
        }
        for prefix, leg in OVERLAY_LEGS.items():
            row = legs.get(leg)
            facts[f"{prefix}.present"] = row is not None
            facts[f"{prefix}.ssk_side"] = row[4] if row is not None else None
        adj = legs.get(OVERLAY_LEGS["adjustment"])
        facts["adjustment.adjtype"] = adj[5] if adj is not None else None
        adjustment_date = None
        age = None
        if adj is not None and adj[1] in adj_facts:
            adjustment_date, staging_id = adj_facts[adj[1]]
            txn_date = txn_dates.get(staging_id)
            if txn_date is not None:
                age = (adjustment_date - txn_date).days
        facts["adjustment.age_days"] = age
        out.append(GroupFacts(group_id, rrn, business_date, facts, adjustment_date, missing))
    return out
