"""
Transaction Presence Matrix engine -- Transaction Processing Engine.

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

2026-09-30 (Phase 2 Step 1): corrections found by profiling the real data
for 2026-07-14 / 09-25 / 09-26. (CBS's missing leading zeros were first
padded here at match time; since 2026-10-02 they are restored at extraction
instead -- file_processing.tasks._extract_cbs_rrn -- so only valid RRNs are
matched here.)
  - Direction: CBS and NPCI legs are always labelled with OPPOSITE
    directions (100% of ~61K pairs/day) -> `direction_match`.
  - Superseded: groups no longer backed by any current file are marked, not
    deleted.
Plus the NPCI response code (raw cell "5"; "00" = success) is recorded per
NPCI presence, so declined transactions can be told apart from real
exceptions by the Business Rule Engine.

2026-10-03 (Phase 2 Step 2):
  - On-us vs reversal: when payer and payee both bank with SSK, NPCI holds an
    issuer (CREDIT) + acquirer (DEBIT) pair for the RRN and CBS legitimately
    holds a CREDIT + DEBIT pair too -> `is_on_us`, and both rows of each pair
    are kept (`paired_transaction_record`). A CBS pair is a reversal ->
    `cbs_reversed` only when NPCI has no pair for the RRN, or when NPCI's
    on-us pair carries failure codes (a failed on-us payment that CBS
    debited and reversed). Step 1 had counted on-us payments as reversals:
    810 of 1,006 on 14 Jul.
  - Dispute overlays: DRC / Timeout / Adjustment rows are attached to the
    match group of their RRN as extra presence rows (DEFAULT_OVERLAY_LEGS),
    with which bank's side SSK is on (`ssk_side`). They never count toward
    MATCHED / UNMATCHED. An Adjustment attaches to the date of its original
    transaction (Txndate) when that date is loaded.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable

from django.db import transaction as db_transaction
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
# logical comparison side. NTSL is out of scope for this component (separate
# settlement-total table, Phase 4).
DEFAULT_PRESENCE_LEGS: dict[str, list[str]] = {
    "CBS": [RegisterType.CBS_GL_EXTRACT],
    "NPCI": [
        RegisterType.RAW_DATA,
        RegisterType.MERCHANT_RAW_DATA,
        RegisterType.MERCHANT_RAW_DATA_BBP,
    ],
}

# DRC / Timeout / Adjustment are dispute overlays, not presence legs: they
# only list transactions already flagged by NPCI (doubts.md, 2026-09-13).
DEFAULT_OVERLAY_LEGS: dict[str, list[str]] = {
    "DRC": [RegisterType.DRC],
    "TIMEOUT": [RegisterType.TIMEOUT],
    "ADJUSTMENT": [RegisterType.ADJUSTMENT],
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

# The two legs direction_match / cbs_reversed / is_on_us are defined between.
_CBS_LEG = "CBS"
_NPCI_LEG = "NPCI"

NPCI_SUCCESS_RESPONSE_CODE = "00"
SSK_BANK_CODE = "SSK"

# Raw cells read from each source row (same 1-based keys as
# FileTypeConfig.column_mapping). NPCI response code: verified 2026-09-30,
# "00" on 99.8% of RRNs found in CBS; RRNs absent from CBS carry decline
# codes. Dispute columns confirmed from the files' own headers (2026-10-03):
# DRC/Timeout 6 "Response Code", 9 "Remitter", 10 "Beneficiary"; Adjustment
# 4 "Adjtype", 5 "Remitter", 7 "Response", 8 "Txndate". "ssk_side" is the
# column the SOP actions SSK's cases on: DRC Remitter, Timeout Beneficiary,
# Adjustment Remitter.
NPCI_RESPONSE_CODE_CELL = "5"
_CELLS_BY_REGISTER_TYPE: dict[str, dict[str, str]] = {
    RegisterType.RAW_DATA: {"response_code": NPCI_RESPONSE_CODE_CELL},
    RegisterType.MERCHANT_RAW_DATA: {"response_code": NPCI_RESPONSE_CODE_CELL},
    RegisterType.MERCHANT_RAW_DATA_BBP: {"response_code": NPCI_RESPONSE_CODE_CELL},
    RegisterType.DRC: {"response_code": "6", "ssk_side": "9"},
    RegisterType.TIMEOUT: {"response_code": "6", "ssk_side": "10"},
    RegisterType.ADJUSTMENT: {"response_code": "7", "ssk_side": "5", "dispute_type": "4", "txn_date": "8"},
}
_ADJUSTMENT_TXN_DATE_FORMAT = "%d-%m-%Y"

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
    matched: int = 0
    pending_rule_evaluation: int = 0
    unmatched: int = 0
    one_sided_counts: dict[str, int] = field(default_factory=dict)
    # NPCI-only groups, by the NPCI row's response code ("00" = success).
    npci_only_response_codes: dict[str, int] = field(default_factory=dict)
    on_us: int = 0
    # On-us in NPCI, but CBS posted only one of the two customers.
    on_us_incomplete: int = 0
    cbs_reversed: int = 0
    direction_mismatches: int = 0
    # {overlay leg: {"attached", "ssk_side", "other_bank_side",
    #  "overlay_only_groups", "duplicates", ...}}
    overlays: dict[str, dict[str, int]] = field(default_factory=dict)
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
            "matched": self.matched,
            "pending_rule_evaluation": self.pending_rule_evaluation,
            "unmatched": self.unmatched,
            "one_sided_counts": self.one_sided_counts,
            "npci_only_response_codes": self.npci_only_response_codes,
            "on_us": self.on_us,
            "on_us_incomplete": self.on_us_incomplete,
            "cbs_reversed": self.cbs_reversed,
            "direction_mismatches": self.direction_mismatches,
            "overlays": self.overlays,
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


def _split_pair(records: list[TransactionFact]) -> tuple[TransactionFact, TransactionFact] | None:
    """(DEBIT row, CREDIT row) when `records` is exactly one CREDIT and one
    DEBIT of the same amount -- the only duplicate shape CBS and NPCI show on
    real data -- else None."""
    if len(records) != 2 or records[0].amount != records[1].amount:
        return None
    by_direction = {r.direction: r for r in records}
    if set(by_direction) != {Direction.CREDIT, Direction.DEBIT}:
        return None
    return by_direction[Direction.DEBIT], by_direction[Direction.CREDIT]


def _original_of_reversal(records: list[TransactionFact], counterpart: TransactionFact | None) -> TransactionFact:
    """The CBS row that pairs with NPCI (opposite direction) is the original;
    the other one is the reversal. Without an NPCI row, the first-loaded
    row is taken as the original."""
    if counterpart is not None:
        for record in records:
            if record.direction != counterpart.direction:
                return record
    return min(records, key=lambda r: r.pk)


def _clean_cell(value) -> str:
    """Cell text with whitespace and the spreadsheet "force text" leading
    apostrophe removed (e.g. "'UR" -> "UR"), as for RRNs at extraction."""
    return str(value or "").strip().removeprefix("'").strip()


def _read_cells(records: Iterable[TransactionFact]) -> dict[int, dict[str, str]]:
    """{TransactionFact.pk: {name: cleaned cell}} for the cells named in
    _CELLS_BY_REGISTER_TYPE, read in bulk from each row's source staging row
    -- or its staging History copy once archived -- via staging_row_id (same
    lineage as TransactionFact.staging_row())."""
    by_register_type: dict[str, list[TransactionFact]] = {}
    for record in records:
        if record.source_register_type in _CELLS_BY_REGISTER_TYPE and record.staging_row_id:
            by_register_type.setdefault(record.source_register_type, []).append(record)

    values: dict[int, dict[str, str]] = {}
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
        wanted = _CELLS_BY_REGISTER_TYPE[register_type]
        for fact in facts:
            cells = cells_by_staging_id.get(fact.staging_row_id) or {}
            values[fact.pk] = {name: _clean_cell(cells.get(key)) for name, key in wanted.items()}
    return values


def _parse_txn_date(raw: str) -> date | None:
    try:
        return datetime.strptime(raw, _ADJUSTMENT_TXN_DATE_FORMAT).date()
    except ValueError:
        return None


def _build_leg_index(
    payment_module: PaymentModule,
    business_date_: date | None,
    register_types: list[str],
) -> tuple[dict[str, list[TransactionFact]], int, int]:
    """Returns ({rrn: [TransactionFact, ...]}, duplicate_rrn_count,
    collision_count) for one leg -- for every business date when
    `business_date_` is None. Which candidate is used is decided by the
    caller (pair handling needs the other leg).

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
        rrn_valid=True,
        source_register_type__in=register_types,
        uploaded_file__is_current=True,
    )
    if business_date_ is not None:
        queryset = queryset.filter(business_date=business_date_)

    by_rrn: dict[str, list[TransactionFact]] = {}
    for record in queryset.order_by("id"):
        by_rrn.setdefault(record.rrn, []).append(record)

    duplicate_rrn_count = 0
    collision_count = 0
    for records in by_rrn.values():
        if len(records) > 1:
            duplicate_rrn_count += 1
            distinct_register_types = {r.source_register_type for r in records}
            if len(distinct_register_types) > 1:
                collision_count += 1

    return by_rrn, duplicate_rrn_count, collision_count


def _overlay_attachments(
    payment_module: PaymentModule,
    business_date_: date,
    overlay_legs: dict[str, list[str]],
    result: PresenceMatrixResult,
) -> dict[str, dict[str, tuple[TransactionFact, dict[str, str], bool]]]:
    """{overlay leg: {rrn: (fact, cells, original_not_loaded)}} -- the
    dispute rows that belong to this business date's groups.

    DRC / Timeout rows belong to their own business date. An Adjustment
    belongs to the date of the transaction it adjusts (its Txndate) when CBS
    data for that date is loaded; otherwise to its own Adjdate, flagged
    original_not_loaded. Adjustment volumes are small (hundreds), so all
    current Adjustment rows are read and the ones for this date picked here.
    """
    loaded_cbs_dates = set(
        TransactionFact.objects.filter(
            payment_module=payment_module,
            source_register_type=RegisterType.CBS_GL_EXTRACT,
            uploaded_file__is_current=True,
        )
        .values_list("business_date", flat=True)
        .distinct()
    )

    attachments: dict[str, dict[str, tuple[TransactionFact, dict[str, str], bool]]] = {}
    for leg_name, register_types in overlay_legs.items():
        is_adjustment = RegisterType.ADJUSTMENT in register_types
        by_rrn, _dups, _collisions = _build_leg_index(
            payment_module, None if is_adjustment else business_date_, register_types
        )
        cells = _read_cells(record for records in by_rrn.values() for record in records)
        stats = Counter()
        attached: dict[str, tuple[TransactionFact, dict[str, str], bool]] = {}
        for rrn, records in by_rrn.items():
            belonging = []
            for record in records:
                record_cells = cells.get(record.pk, {})
                original_not_loaded = False
                if is_adjustment:
                    txn_date = _parse_txn_date(record_cells.get("txn_date", ""))
                    if txn_date is not None and txn_date in loaded_cbs_dates:
                        if txn_date != business_date_:
                            continue
                        stats["linked_to_txn_date"] += 1
                    elif record.business_date == business_date_:
                        original_not_loaded = True
                        stats["original_not_loaded"] += 1
                    else:
                        continue
                belonging.append((record, record_cells, original_not_loaded))
            if not belonging:
                continue
            # One overlay presence per (group, leg): the first row is used and
            # any further rows for the same RRN are counted, never hidden.
            stats["duplicates"] += len(belonging) - 1
            attached[rrn] = belonging[0]
            stats["attached"] += 1
            stats["ssk_side" if belonging[0][1].get("ssk_side") == SSK_BANK_CODE else "other_bank_side"] += 1
        attachments[leg_name] = attached
        result.overlays[leg_name] = dict(stats)
    return attachments


@db_transaction.atomic
def build_presence_matrix(
    payment_module: PaymentModule,
    business_date_: date,
    legs: dict[str, Iterable[str]] | None = None,
    overlay_legs: dict[str, Iterable[str]] | None = None,
) -> PresenceMatrixResult:
    """
    Builds/refreshes the Transaction Presence Matrix for one
    (payment_module, business_date).

    `legs` defaults to `DEFAULT_PRESENCE_LEGS` (CBS vs NPCI). Passing a
    different/extended dict (e.g. adding a synthetic "SWITCH" leg) proves
    the engine's extensibility without any model/migration change -- see
    reconciliation/tests/test_presence.py's extensibility proof test.
    `overlay_legs` defaults to `DEFAULT_OVERLAY_LEGS`; pass {} for none.
    """
    resolved_legs: dict[str, list[str]] = {
        leg_name: list(register_types) for leg_name, register_types in (legs or DEFAULT_PRESENCE_LEGS).items()
    }
    resolved_overlays: dict[str, list[str]] = {
        leg_name: list(register_types)
        for leg_name, register_types in (DEFAULT_OVERLAY_LEGS if overlay_legs is None else overlay_legs).items()
    }

    leg_candidates: dict[str, dict[str, list[TransactionFact]]] = {}
    result = PresenceMatrixResult(
        payment_module_code=payment_module.code,
        business_date=business_date_,
        legs=resolved_legs,
    )

    candidate_rrns: set[str] = set()
    for leg_name, register_types in resolved_legs.items():
        by_rrn, dup_count, collision_count = _build_leg_index(payment_module, business_date_, register_types)
        leg_candidates[leg_name] = by_rrn
        result.per_leg_row_counts[leg_name] = len(by_rrn)
        result.per_leg_duplicate_rrn_counts[leg_name] = dup_count
        result.leg_collision_counts[leg_name] = collision_count
        candidate_rrns |= by_rrn.keys()

    overlays = _overlay_attachments(payment_module, business_date_, resolved_overlays, result)
    overlay_rrns = {rrn for attached in overlays.values() for rrn in attached}
    all_rrns = candidate_rrns | overlay_rrns

    cells = _read_cells(
        record
        for by_rrn in leg_candidates.values()
        for records in by_rrn.values()
        for record in records
    )

    def response_code(record: TransactionFact | None) -> str:
        return cells.get(record.pk, {}).get("response_code", "") if record else ""

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
    # (rrn, leg_name, record, paired record), built alongside so a second
    # pass can attach the right match_group_id once IDs are known.
    presence_plan: list[tuple[str, str, TransactionFact | None, TransactionFact | None]] = []

    for rrn in sorted(all_rrns):
        present_records: dict[str, TransactionFact] = {
            leg_name: records[0] if len(records) == 1 else _priority_pick(records, _NPCI_LEG_PRIORITY)
            for leg_name, by_rrn in leg_candidates.items()
            if (records := by_rrn.get(rrn))
        }
        paired_records: dict[str, TransactionFact] = {}

        cbs_candidates = leg_candidates.get(_CBS_LEG, {}).get(rrn, [])
        npci_pair = _split_pair(leg_candidates.get(_NPCI_LEG, {}).get(rrn, []))
        cbs_pair = _split_pair(cbs_candidates)
        is_on_us = npci_pair is not None
        cbs_reversed = False
        if is_on_us:
            # NPCI acquirer (DEBIT) pairs with CBS CREDIT; issuer (CREDIT)
            # with CBS DEBIT. Both rows of each side are kept.
            present_records[_NPCI_LEG], paired_records[_NPCI_LEG] = npci_pair
            npci_pair_succeeded = all(
                response_code(r) in ("", NPCI_SUCCESS_RESPONSE_CODE) for r in npci_pair
            )
            if cbs_pair and npci_pair_succeeded:
                paired_records[_CBS_LEG], present_records[_CBS_LEG] = cbs_pair
                result.on_us += 1
            elif cbs_pair:
                # A FAILED on-us payment (NPCI codes e.g. R9/U9/RR on both
                # rows): CBS's credit + debit is the payer's debit and its
                # reversal, not two customers. Verified against the manual
                # sheet's DRC eff/rev for 14 Jul (10 RRNs).
                cbs_reversed = True
                present_records[_CBS_LEG] = _original_of_reversal(cbs_candidates, present_records[_NPCI_LEG])
                result.cbs_reversed += 1
            elif _CBS_LEG in present_records:
                result.on_us_incomplete += 1
        elif cbs_pair:
            cbs_reversed = True
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
            distinct_amounts = {r.amount for r in (*present_records.values(), *paired_records.values())}
            amount_match = len(distinct_amounts) == 1

        direction_match: bool | None = None
        cbs_record = present_records.get(_CBS_LEG)
        npci_record = present_records.get(_NPCI_LEG)
        pairings = [(cbs_record, npci_record)]
        if _CBS_LEG in paired_records and _NPCI_LEG in paired_records:
            pairings.append((paired_records[_CBS_LEG], paired_records[_NPCI_LEG]))
        if cbs_record and npci_record and all(
            Direction.UNKNOWN not in (a.direction, b.direction) for a, b in pairings
        ):
            direction_match = all(a.direction != b.direction for a, b in pairings)
            if not direction_match:
                result.direction_mismatches += 1

        npci_codes = {response_code(npci_record), response_code(paired_records.get(_NPCI_LEG))}
        # MATCHED only when nothing needs a rule's judgement; anything present
        # everywhere but irregular goes to the Business Rule Engine.
        irregular = (
            amount_match is False
            or direction_match is False
            or cbs_reversed
            or (is_on_us and cbs_pair is None)
            or bool(npci_codes - {"", NPCI_SUCCESS_RESPONSE_CODE})
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
                    npci_only_codes[response_code(npci_record) or "<blank>"] += 1
            elif present_leg_count == 0:
                # Only a dispute file carries this RRN (e.g. DRC "not effected").
                for leg_name, attached in overlays.items():
                    if rrn in attached:
                        result.overlays[leg_name]["overlay_only_groups"] = (
                            result.overlays[leg_name].get("overlay_only_groups", 0) + 1
                        )

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
                is_on_us=is_on_us,
                is_superseded=False,
                superseded_at=None,
                processing_status=processing_status,
            )
        )
        for leg_name in resolved_legs:
            presence_plan.append((rrn, leg_name, present_records.get(leg_name), paired_records.get(leg_name)))

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
                "is_on_us",
                "is_superseded",
                "superseded_at",
                "processing_status",
                # auto_now is only applied to the INSERT values; without this
                # an upserted row kept its first-build updated_at forever
                # (found 2026-10-08: groups rebuilt 2026-10-04 still showed
                # 2026-09-30..10-03).
                "updated_at",
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
        if rrn not in all_rrns and not is_superseded:
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
            paired_transaction_record=paired,
            amount=record.amount if record else None,
            direction=record.direction if record else "",
            response_code=response_code(record),
        )
        for rrn, leg_name, record, paired in presence_plan
    ]

    # Bulk upsert #2: RegisterPresence, keyed on unique_presence_per_leg.
    if presence_rows:
        RegisterPresence.objects.bulk_create(
            presence_rows,
            update_conflicts=True,
            unique_fields=["match_group", "leg"],
            update_fields=[
                "present",
                "transaction_record",
                "paired_transaction_record",
                "amount",
                "direction",
                "response_code",
                "updated_at",  # see the TransactionMatchGroup upsert above
            ],
            batch_size=500,
        )

    # Overlay presences are derived links: this date's are replaced wholesale
    # so a dispute row that moved (e.g. an Adjustment now linked to its
    # Txndate) never lingers on its old group.
    if resolved_overlays:
        RegisterPresence.objects.filter(
            match_group__payment_module=payment_module,
            match_group__business_date=business_date_,
            leg__in=list(resolved_overlays),
        ).delete()
        RegisterPresence.objects.bulk_create(
            [
                RegisterPresence(
                    match_group_id=match_group_ids[rrn],
                    leg=leg_name,
                    present=True,
                    transaction_record=record,
                    amount=record.amount,
                    direction=record.direction,
                    response_code=record_cells.get("response_code", ""),
                    ssk_side=record_cells.get("ssk_side") == SSK_BANK_CODE,
                    dispute_type=record_cells.get("dispute_type", ""),
                    original_not_loaded=original_not_loaded,
                )
                for leg_name, attached in overlays.items()
                for rrn, (record, record_cells, original_not_loaded) in attached.items()
            ],
            batch_size=500,
        )

    result.one_sided_counts = dict(one_sided_counter)
    result.npci_only_response_codes = dict(npci_only_codes.most_common())
    result.total_match_groups = len(all_rrns)
    return result
