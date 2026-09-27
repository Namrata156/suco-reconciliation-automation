"""
Transaction Presence Matrix engine -- Transaction Processing Engine,
first slice.

Builds/refreshes `reconciliation.TransactionMatchGroup` +
`reconciliation.RegisterPresence` rows for one `(payment_module,
business_date)` by comparing `transactions.TransactionFact` across a
configurable set of logical "legs".

Idempotent by design: writes are batched bulk upserts (`bulk_create(...,
update_conflicts=True)`) keyed on the same natural keys as each model's
unique constraint, so rerunning after a new file/cycle loads updates
existing rows in place rather than duplicating them -- consistent with
the existing reprocessing convention (`uploaded_file__is_current`). Batched
rather than a per-row `update_or_create` loop for performance: at CBS's
real scale (tens of thousands of distinct valid RRNs per business day),
a per-row loop was measured to take several minutes against the real
sample data.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from django.db import transaction as db_transaction

from core.models import PaymentModule, RegisterType
from reconciliation.models import ProcessingStatus, RegisterPresence, TransactionMatchGroup
from transactions.models import TransactionFact

# Confirmed model (doubts.md "Resolved" -- 2026-09-13): this bank only ever
# compares CBS (its own ledger) against the files NPCI provides. "NPCI"
# aggregates 3 real RegisterTypes into one logical leg, since NPCI's side is
# represented by Raw Data / Merchant Raw Data (+ BBP) -- plural sources, one
# logical comparison side. DRC/Timeout/Adjustment are dispute overlays, not
# presence-matrix legs (excluded entirely -- second-slice work). NTSL is out
# of scope for this component (separate settlement-total table, Phase 4).
DEFAULT_PRESENCE_LEGS: dict[str, list[str]] = {
    "CBS": [RegisterType.CBS_GL_EXTRACT],
    "NPCI": [
        RegisterType.RAW_DATA,
        RegisterType.MERCHANT_RAW_DATA,
        RegisterType.MERCHANT_RAW_DATA_BBP,
    ],
}

# Fixed priority order used to pick one record when more than one of a leg's
# constituent RegisterTypes matches the same (rrn, business_date) -- a
# data-quality edge case, not the expected common case. The collision itself
# is counted in the summary rather than silently dropped.
_NPCI_LEG_PRIORITY = [
    RegisterType.RAW_DATA,
    RegisterType.MERCHANT_RAW_DATA,
    RegisterType.MERCHANT_RAW_DATA_BBP,
]


@dataclass
class PresenceMatrixResult:
    """Human-readable + test-assertable summary of one build_presence_matrix run."""

    payment_module_code: str
    business_date: date
    legs: dict[str, list[str]]
    per_leg_row_counts: dict[str, int] = field(default_factory=dict)
    per_leg_duplicate_rrn_counts: dict[str, int] = field(default_factory=dict)
    leg_collision_counts: dict[str, int] = field(default_factory=dict)
    matched: int = 0
    pending_rule_evaluation: int = 0
    unmatched: int = 0
    one_sided_counts: dict[str, int] = field(default_factory=dict)
    total_match_groups: int = 0

    def as_dict(self) -> dict:
        return {
            "payment_module_code": self.payment_module_code,
            "business_date": self.business_date.isoformat(),
            "legs": self.legs,
            "per_leg_row_counts": self.per_leg_row_counts,
            "per_leg_duplicate_rrn_counts": self.per_leg_duplicate_rrn_counts,
            "leg_collision_counts": self.leg_collision_counts,
            "matched": self.matched,
            "pending_rule_evaluation": self.pending_rule_evaluation,
            "unmatched": self.unmatched,
            "one_sided_counts": self.one_sided_counts,
            "total_match_groups": self.total_match_groups,
        }


def _priority_pick(records: list[TransactionFact], priority: list[str]) -> TransactionFact:
    """Picks one record by fixed priority order of source_register_type when
    duplicates collide on the same (rrn, business_date) within one leg."""
    for register_type in priority:
        for record in records:
            if record.source_register_type == register_type:
                return record
    return records[0]


def _build_leg_index(
    payment_module: PaymentModule,
    business_date_: date,
    register_types: list[str],
) -> tuple[dict[str, TransactionFact], int, int]:
    """Returns ({rrn: TransactionFact}, duplicate_rrn_count, collision_count)
    for one leg.

    duplicate_rrn_count: how many distinct RRNs had more than one candidate
    row within this leg (whether from one register type appearing twice, or
    from >1 of the leg's constituent register types matching the same RRN).
    collision_count: specifically how many of those were cross-register-type
    collisions (i.e. would matter for _priority_pick), a subset of interest
    for a leg aggregating multiple RegisterTypes (e.g. "NPCI").
    """
    # No select_related("uploaded_file") here deliberately: this code never
    # dereferences record.uploaded_file itself (uploaded_file__is_current is
    # just a filter condition, resolved as a JOIN without needing the related
    # object materialized). Joining UploadedFile in would pull its
    # pending_pipeline_context JSONField along for every row -- before the
    # 2026-09-23 staging redesign that field held the entire parsed file's
    # row context for a RANGE_CONTAINS upload (e.g. CBS), and eagerly
    # joining it in for every one of CBS's 69,239 rows exhausted memory
    # (confirmed by direct reproduction). It now only holds a small
    # {"uploaded_file_id", "halted"} dict, but uploaded files created before
    # the redesign may still carry the old large payload, so the join stays
    # avoided.
    queryset = TransactionFact.objects.filter(
        payment_module=payment_module,
        business_date=business_date_,
        rrn_valid=True,
        source_register_type__in=register_types,
        uploaded_file__is_current=True,
    )

    by_rrn: dict[str, list[TransactionFact]] = {}
    for record in queryset:
        by_rrn.setdefault(record.rrn, []).append(record)

    index: dict[str, TransactionFact] = {}
    duplicate_rrn_count = 0
    collision_count = 0
    for rrn, records in by_rrn.items():
        if len(records) > 1:
            duplicate_rrn_count += 1
            distinct_register_types = {r.source_register_type for r in records}
            if len(distinct_register_types) > 1:
                collision_count += 1
        index[rrn] = _priority_pick(records, _NPCI_LEG_PRIORITY) if len(records) > 1 else records[0]

    return index, duplicate_rrn_count, collision_count


@db_transaction.atomic
def build_presence_matrix(
    payment_module: PaymentModule,
    business_date_: date,
    legs: dict[str, Iterable[str]] | None = None,
) -> PresenceMatrixResult:
    """
    Builds/refreshes the Transaction Presence Matrix for one
    (payment_module, business_date).

    `legs` defaults to `DEFAULT_PRESENCE_LEGS` (CBS vs NPCI). Passing a
    different/extended dict (e.g. adding a synthetic "SWITCH" leg) proves
    the engine's extensibility without any model/migration change -- see
    reconciliation/tests/test_presence.py's extensibility proof test.
    """
    resolved_legs: dict[str, list[str]] = {
        leg_name: list(register_types) for leg_name, register_types in (legs or DEFAULT_PRESENCE_LEGS).items()
    }

    leg_indexes: dict[str, dict[str, TransactionFact]] = {}
    result = PresenceMatrixResult(
        payment_module_code=payment_module.code,
        business_date=business_date_,
        legs=resolved_legs,
    )

    candidate_rrns: set[str] = set()
    for leg_name, register_types in resolved_legs.items():
        index, dup_count, collision_count = _build_leg_index(payment_module, business_date_, register_types)
        leg_indexes[leg_name] = index
        result.per_leg_row_counts[leg_name] = len(index)
        result.per_leg_duplicate_rrn_counts[leg_name] = dup_count
        result.leg_collision_counts[leg_name] = collision_count
        candidate_rrns |= index.keys()

    one_sided_counter: Counter[str] = Counter()

    # Classification pass is pure in-memory work (no per-RRN DB round trip
    # here) -- DB writes are batched below via bulk upserts. At CBS's real
    # scale (tens of thousands of distinct valid RRNs per business day) a
    # per-RRN update_or_create (2+ queries each) was measured to take
    # several minutes against the real sample; batching brings this down to
    # a handful of bulk statements total. Confirmed necessary by direct
    # reproduction against the real CBS + Raw Data/Merchant Raw Data/BBP
    # samples during this component's build -- see this backend engineer's
    # status report.
    match_group_rows: list[TransactionMatchGroup] = []
    # (rrn, leg_name) -> TransactionFact|None, built alongside so a second
    # pass can attach the right match_group_id once IDs are known.
    presence_plan: list[tuple[str, str, TransactionFact | None]] = []

    for rrn in sorted(candidate_rrns):
        present_records: dict[str, TransactionFact] = {
            leg_name: index[rrn] for leg_name, index in leg_indexes.items() if rrn in index
        }

        # Settlement cycle: derived (informational only, not part of the
        # matching key) from whichever present leg's record has one set.
        settlement_cycle = None
        for record in present_records.values():
            if record.settlement_cycle_id:
                settlement_cycle = record.settlement_cycle
                break

        present_leg_count = len(present_records)
        total_leg_count = len(resolved_legs)

        amount_match: bool | None = None
        if present_leg_count >= 2:
            distinct_amounts = {record.amount for record in present_records.values()}
            amount_match = len(distinct_amounts) == 1

        if present_leg_count == total_leg_count and amount_match is True:
            processing_status = ProcessingStatus.MATCHED
            result.matched += 1
        elif present_leg_count == total_leg_count and amount_match is False:
            processing_status = ProcessingStatus.PENDING_RULE_EVALUATION
            result.pending_rule_evaluation += 1
        else:
            processing_status = ProcessingStatus.UNMATCHED
            result.unmatched += 1
            if present_leg_count == 1:
                only_leg = next(iter(present_records))
                one_sided_counter[only_leg] += 1

        match_group_rows.append(
            TransactionMatchGroup(
                payment_module=payment_module,
                rrn=rrn,
                business_date=business_date_,
                settlement_cycle=settlement_cycle,
                amount_match=amount_match,
                status_match=None,  # no transaction-status field on TransactionFact yet
                processing_status=processing_status,
            )
        )
        for leg_name in resolved_legs:
            presence_plan.append((rrn, leg_name, present_records.get(leg_name)))

    # Bulk upsert #1: TransactionMatchGroup, keyed on the same fields as
    # unique_match_group_per_module_rrn_date -- a rerun after a new file/
    # cycle loads updates existing rows (same PK preserved) rather than
    # duplicating them, same idempotency guarantee as update_or_create.
    if match_group_rows:
        TransactionMatchGroup.objects.bulk_create(
            match_group_rows,
            update_conflicts=True,
            unique_fields=["payment_module", "rrn", "business_date"],
            update_fields=["settlement_cycle", "amount_match", "status_match", "processing_status"],
            batch_size=500,
        )

    # Refetch IDs (bulk_create's upserted-row PKs aren't reliably populated
    # back onto the passed-in instances across backends) to build the
    # rrn -> match_group_id map needed for RegisterPresence rows.
    match_group_ids: dict[str, int] = dict(
        TransactionMatchGroup.objects.filter(
            payment_module=payment_module, business_date=business_date_
        ).values_list("rrn", "id")
    )

    presence_rows = [
        RegisterPresence(
            match_group_id=match_group_ids[rrn],
            leg=leg_name,
            present=record is not None,
            transaction_record=record,
            amount=record.amount if record else None,
            direction=record.direction if record else "",
        )
        for rrn, leg_name, record in presence_plan
    ]

    # Bulk upsert #2: RegisterPresence, keyed on unique_presence_per_leg.
    if presence_rows:
        RegisterPresence.objects.bulk_create(
            presence_rows,
            update_conflicts=True,
            unique_fields=["match_group", "leg"],
            update_fields=["present", "transaction_record", "amount", "direction"],
            batch_size=500,
        )

    result.one_sided_counts = dict(one_sided_counter)
    result.total_match_groups = len(candidate_rrns)
    return result
