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

2026-09-30 (Phase 2 Step 1): four corrections found by profiling the real
data for 2026-07-14 / 09-25 / 09-26. All of them live here, in the
reconciliation layer -- the loaded TransactionFact rows are never modified
(project owner decision 2026-09-30):
  - Leading zeros: CBS drops them from ~7K RRNs/day (8-11 digit values);
    zero-padded to 12 at match time, they match NPCI for 99%+ of rows.
  - Direction: CBS and NPCI legs are always labelled with OPPOSITE
    directions (100% of ~61K pairs/day) -> `direction_match`.
  - Reversals: a CBS CREDIT + DEBIT of the same amount on one RRN is a
    reversal -> `cbs_reversed`; the CBS presence points at the original leg.
  - Superseded: groups no longer backed by any current file are marked, not
    deleted.
Plus the NPCI response code (raw cell "5"; "00" = success) is recorded per
NPCI presence, so declined transactions can be told apart from real
exceptions by the Business Rule Engine.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from django.db import transaction as db_transaction
from django.db.models import Q
from django.utils import timezone

from core.models import Direction, PaymentModule, RegisterType
from reconciliation.models import ProcessingStatus, RegisterPresence, TransactionMatchGroup
from transactions.models import TransactionFact
from transactions.models.history import HISTORY_MODEL_BY_STAGING_MODEL
from transactions.models.staging import STAGING_MODEL_BY_REGISTER_TYPE

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

# Leading-zero recovery (see module docstring). Only CBS stores RRNs as
# numbers; every other source keeps them as text.
RRN_DIGITS = 12
_ZERO_PAD_MIN_DIGITS = 8
_ZERO_PAD_REGISTER_TYPES = {RegisterType.CBS_GL_EXTRACT}

# The two legs direction_match / cbs_reversed are defined between.
_CBS_LEG = "CBS"
_NPCI_LEG = "NPCI"

# NPCI response code: raw cell "5" of Raw Data / Merchant Raw Data / BBP TX
# rows (same 1-based keys as FileTypeConfig.column_mapping, where RRN is
# "4"). Verified 2026-09-30: "00" on 99.8% of RRNs found in CBS; the RRNs
# absent from CBS carry decline codes (01, Z9, ZM, Z6, YE, K1, ...).
NPCI_RESPONSE_CODE_CELL = "5"
NPCI_SUCCESS_RESPONSE_CODE = "00"
_RESPONSE_CODE_REGISTER_TYPES = set(DEFAULT_PRESENCE_LEGS[_NPCI_LEG])

_LOOKUP_BATCH = 5000


@dataclass
class PresenceMatrixResult:
    """Human-readable + test-assertable summary of one build_presence_matrix run."""

    payment_module_code: str
    business_date: date
    legs: dict[str, list[str]]
    per_leg_row_counts: dict[str, int] = field(default_factory=dict)
    per_leg_duplicate_rrn_counts: dict[str, int] = field(default_factory=dict)
    leg_collision_counts: dict[str, int] = field(default_factory=dict)
    per_leg_zero_padded_counts: dict[str, int] = field(default_factory=dict)
    matched: int = 0
    pending_rule_evaluation: int = 0
    unmatched: int = 0
    one_sided_counts: dict[str, int] = field(default_factory=dict)
    # NPCI-only groups, by the NPCI row's response code ("00" = success).
    npci_only_response_codes: dict[str, int] = field(default_factory=dict)
    cbs_reversed: int = 0
    direction_mismatches: int = 0
    superseded: int = 0
    total_match_groups: int = 0

    def as_dict(self) -> dict:
        return {
            "payment_module_code": self.payment_module_code,
            "business_date": self.business_date.isoformat(),
            "legs": self.legs,
            "per_leg_row_counts": self.per_leg_row_counts,
            "per_leg_duplicate_rrn_counts": self.per_leg_duplicate_rrn_counts,
            "leg_collision_counts": self.leg_collision_counts,
            "per_leg_zero_padded_counts": self.per_leg_zero_padded_counts,
            "matched": self.matched,
            "pending_rule_evaluation": self.pending_rule_evaluation,
            "unmatched": self.unmatched,
            "one_sided_counts": self.one_sided_counts,
            "npci_only_response_codes": self.npci_only_response_codes,
            "cbs_reversed": self.cbs_reversed,
            "direction_mismatches": self.direction_mismatches,
            "superseded": self.superseded,
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


def match_key(record: TransactionFact) -> str | None:
    """The RRN this record matches on, or None if it can't be matched.
    Valid RRNs are used as-is; a CBS RRN that lost its leading zeros (all
    digits, 8-11 long) is zero-padded back to 12."""
    if record.rrn_valid:
        return record.rrn
    rrn = record.rrn or ""
    if (
        record.source_register_type in _ZERO_PAD_REGISTER_TYPES
        and rrn.isdigit()
        and _ZERO_PAD_MIN_DIGITS <= len(rrn) < RRN_DIGITS
    ):
        return rrn.zfill(RRN_DIGITS)
    return None


def _is_reversal_pair(records: list[TransactionFact]) -> bool:
    """Exactly one CREDIT and one DEBIT of the same amount -- the only
    duplicate shape CBS shows on real data (913/917/788 RRNs a day)."""
    return (
        len(records) == 2
        and {r.direction for r in records} == {Direction.CREDIT, Direction.DEBIT}
        and records[0].amount == records[1].amount
    )


def _original_of_reversal(records: list[TransactionFact], counterpart: TransactionFact | None) -> TransactionFact:
    """The CBS row that pairs with NPCI (opposite direction) is the original;
    the other one is the reversal. Without an NPCI row, the first-loaded
    row is taken as the original."""
    if counterpart is not None:
        for record in records:
            if record.direction != counterpart.direction:
                return record
    return min(records, key=lambda r: r.pk)


def _response_codes(records: Iterable[TransactionFact]) -> dict[int, str]:
    """{TransactionFact.pk: NPCI response code}, read in bulk from each row's
    source staging row -- or its staging History copy once archived -- via
    staging_row_id (same lineage as TransactionFact.staging_row())."""
    by_register_type: dict[str, list[TransactionFact]] = {}
    for record in records:
        if record.source_register_type in _RESPONSE_CODE_REGISTER_TYPES and record.staging_row_id:
            by_register_type.setdefault(record.source_register_type, []).append(record)

    codes: dict[int, str] = {}
    for register_type, facts in by_register_type.items():
        staging_model = STAGING_MODEL_BY_REGISTER_TYPE[register_type]
        history_model = HISTORY_MODEL_BY_STAGING_MODEL[staging_model]
        staging_ids = [f.staging_row_id for f in facts]
        cells_by_staging_id: dict[int, dict] = {}
        for start in range(0, len(staging_ids), _LOOKUP_BATCH):
            batch = staging_ids[start : start + _LOOKUP_BATCH]
            cells_by_staging_id.update(staging_model.objects.filter(id__in=batch).values_list("id", "raw_cells"))
            missing = [i for i in batch if i not in cells_by_staging_id]
            if missing:
                cells_by_staging_id.update(
                    history_model.objects.filter(original_id__in=missing).values_list("original_id", "raw_cells")
                )
        for fact in facts:
            cells = cells_by_staging_id.get(fact.staging_row_id) or {}
            codes[fact.pk] = str(cells.get(NPCI_RESPONSE_CODE_CELL) or "").strip()
    return codes


def _build_leg_index(
    payment_module: PaymentModule,
    business_date_: date,
    register_types: list[str],
) -> tuple[dict[str, list[TransactionFact]], int, int, int]:
    """Returns ({match key: [TransactionFact, ...]}, duplicate_rrn_count,
    collision_count, zero_padded_count) for one leg. Which candidate is
    used is decided by the caller (reversal handling needs the other leg).

    duplicate_rrn_count: how many distinct RRNs had more than one candidate
    row within this leg (whether from one register type appearing twice, or
    from >1 of the leg's constituent register types matching the same RRN).
    collision_count: specifically how many of those were cross-register-type
    collisions (i.e. would matter for _priority_pick), a subset of interest
    for a leg aggregating multiple RegisterTypes (e.g. "NPCI").
    zero_padded_count: rows matched only thanks to leading-zero recovery.
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
    # rrn_valid=False rows are only fetched for register types where
    # leading-zero recovery applies; match_key() decides which are usable.
    queryset = TransactionFact.objects.filter(
        Q(rrn_valid=True) | Q(source_register_type__in=_ZERO_PAD_REGISTER_TYPES),
        payment_module=payment_module,
        business_date=business_date_,
        source_register_type__in=register_types,
        uploaded_file__is_current=True,
    )

    by_rrn: dict[str, list[TransactionFact]] = {}
    zero_padded_count = 0
    for record in queryset:
        key = match_key(record)
        if key is None:
            continue
        if not record.rrn_valid:
            zero_padded_count += 1
        by_rrn.setdefault(key, []).append(record)

    duplicate_rrn_count = 0
    collision_count = 0
    for records in by_rrn.values():
        if len(records) > 1:
            duplicate_rrn_count += 1
            distinct_register_types = {r.source_register_type for r in records}
            if len(distinct_register_types) > 1:
                collision_count += 1

    return by_rrn, duplicate_rrn_count, collision_count, zero_padded_count


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

    leg_candidates: dict[str, dict[str, list[TransactionFact]]] = {}
    result = PresenceMatrixResult(
        payment_module_code=payment_module.code,
        business_date=business_date_,
        legs=resolved_legs,
    )

    candidate_rrns: set[str] = set()
    for leg_name, register_types in resolved_legs.items():
        by_rrn, dup_count, collision_count, zero_padded = _build_leg_index(
            payment_module, business_date_, register_types
        )
        leg_candidates[leg_name] = by_rrn
        result.per_leg_row_counts[leg_name] = len(by_rrn)
        result.per_leg_duplicate_rrn_counts[leg_name] = dup_count
        result.leg_collision_counts[leg_name] = collision_count
        result.per_leg_zero_padded_counts[leg_name] = zero_padded
        candidate_rrns |= by_rrn.keys()

    response_codes = _response_codes(
        record
        for by_rrn in leg_candidates.values()
        for records in by_rrn.values()
        for record in records
    )

    one_sided_counter: Counter[str] = Counter()
    npci_only_codes: Counter[str] = Counter()

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
            leg_name: records[0] if len(records) == 1 else _priority_pick(records, _NPCI_LEG_PRIORITY)
            for leg_name, by_rrn in leg_candidates.items()
            if (records := by_rrn.get(rrn))
        }

        # Reversal: keep CBS's original leg (the one pairing with NPCI).
        cbs_candidates = leg_candidates.get(_CBS_LEG, {}).get(rrn, [])
        cbs_reversed = _is_reversal_pair(cbs_candidates)
        if cbs_reversed:
            present_records[_CBS_LEG] = _original_of_reversal(cbs_candidates, present_records.get(_NPCI_LEG))
            result.cbs_reversed += 1

        # Settlement cycle: derived (informational only, not part of the
        # matching key) from whichever present leg's record has one set.
        settlement_cycle_id = next(
            (record.settlement_cycle_id for record in present_records.values() if record.settlement_cycle_id),
            None,
        )

        present_leg_count = len(present_records)
        total_leg_count = len(resolved_legs)

        amount_match: bool | None = None
        if present_leg_count >= 2:
            distinct_amounts = {record.amount for record in present_records.values()}
            amount_match = len(distinct_amounts) == 1

        direction_match: bool | None = None
        cbs_record = present_records.get(_CBS_LEG)
        npci_record = present_records.get(_NPCI_LEG)
        if cbs_record and npci_record and Direction.UNKNOWN not in (cbs_record.direction, npci_record.direction):
            direction_match = cbs_record.direction != npci_record.direction
            if not direction_match:
                result.direction_mismatches += 1

        npci_code = response_codes.get(npci_record.pk, "") if npci_record else ""
        # MATCHED only when nothing needs a rule's judgement; anything present
        # everywhere but irregular goes to the Business Rule Engine.
        irregular = (
            amount_match is False
            or direction_match is False
            or cbs_reversed
            or npci_code not in ("", NPCI_SUCCESS_RESPONSE_CODE)
        )

        if present_leg_count == total_leg_count and amount_match is True and not irregular:
            processing_status = ProcessingStatus.MATCHED
            result.matched += 1
        elif present_leg_count == total_leg_count:
            processing_status = ProcessingStatus.PENDING_RULE_EVALUATION
            result.pending_rule_evaluation += 1
        else:
            processing_status = ProcessingStatus.UNMATCHED
            result.unmatched += 1
            if present_leg_count == 1:
                only_leg = next(iter(present_records))
                one_sided_counter[only_leg] += 1
                if only_leg == _NPCI_LEG:
                    npci_only_codes[npci_code or "<blank>"] += 1

        match_group_rows.append(
            TransactionMatchGroup(
                payment_module=payment_module,
                rrn=rrn,
                business_date=business_date_,
                settlement_cycle_id=settlement_cycle_id,
                amount_match=amount_match,
                status_match=None,  # CBS carries no status; NPCI's is on RegisterPresence.response_code
                direction_match=direction_match,
                cbs_reversed=cbs_reversed,
                is_superseded=False,
                superseded_at=None,
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
            update_fields=[
                "settlement_cycle",
                "amount_match",
                "status_match",
                "direction_match",
                "cbs_reversed",
                "is_superseded",
                "superseded_at",
                "processing_status",
            ],
            batch_size=500,
        )

    # Refetch IDs (bulk_create's upserted-row PKs aren't reliably populated
    # back onto the passed-in instances across backends) to build the
    # rrn -> match_group_id map needed for RegisterPresence rows. The same
    # read also finds groups no current file backs any more.
    match_group_ids: dict[str, int] = {}
    stale_ids: list[int] = []
    for rrn, group_id, is_superseded in TransactionMatchGroup.objects.filter(
        payment_module=payment_module, business_date=business_date_
    ).values_list("rrn", "id", "is_superseded"):
        match_group_ids[rrn] = group_id
        if rrn not in candidate_rrns and not is_superseded:
            stale_ids.append(group_id)

    # Superseded groups are marked, never deleted (owner decision 2026-09-30).
    now = timezone.now()
    for start in range(0, len(stale_ids), _LOOKUP_BATCH):
        TransactionMatchGroup.objects.filter(id__in=stale_ids[start : start + _LOOKUP_BATCH]).update(
            is_superseded=True, superseded_at=now
        )
    result.superseded = len(stale_ids)

    presence_rows = [
        RegisterPresence(
            match_group_id=match_group_ids[rrn],
            leg=leg_name,
            present=record is not None,
            transaction_record=record,
            amount=record.amount if record else None,
            direction=record.direction if record else "",
            response_code=response_codes.get(record.pk, "") if record else "",
        )
        for rrn, leg_name, record in presence_plan
    ]

    # Bulk upsert #2: RegisterPresence, keyed on unique_presence_per_leg.
    if presence_rows:
        RegisterPresence.objects.bulk_create(
            presence_rows,
            update_conflicts=True,
            unique_fields=["match_group", "leg"],
            update_fields=["present", "transaction_record", "amount", "direction", "response_code"],
            batch_size=500,
        )

    result.one_sided_counts = dict(one_sided_counter)
    result.npci_only_response_codes = dict(npci_only_codes.most_common())
    result.total_match_groups = len(candidate_rrns)
    return result
