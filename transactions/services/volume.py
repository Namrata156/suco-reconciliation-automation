"""
Fills the daily volume summary (transactions.models.volume; Phase 6 design
2.3 / R4a, contract B3) for one payment module + business date from the
date's current facts:

    refresh_daily_volume(module, business_date, source="...") -> VolumeRefresh

Two grouped queries over the date's facts (about 149,000 rows): counts and
amounts per file x cycle x direction, and the approved count of the NPCI
transaction files from their staging rows' response code (cell "5", read
from the live staging row or, once archived, its History copy -- the same
cell and cleaning as reconciliation.presence). Then the date's summary rows
are replaced in one transaction. Called at the end of every reconciliation
run (reconciliation.runs) and by `manage.py fill_daily_volume`.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import connection, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from core.models import PaymentModule, RegisterType, SettlementCycle
from file_processing.models import UploadedFile
from settlement.gap_rules import SETTLED_CODES
from transactions.models import TransactionFact
from transactions.models.history import HISTORY_MODEL_BY_STAGING_MODEL
from transactions.models.staging import STAGING_MODEL_BY_REGISTER_TYPE
from transactions.models.volume import DailyVolume, DailyVolumeDate, VolumeSide

# NPCI transaction files: one file per side (ACQ / ISS), response code in cell 5.
NPCI_TRANSACTION_TYPES = (RegisterType.RAW_DATA, RegisterType.MERCHANT_RAW_DATA, RegisterType.MERCHANT_RAW_DATA_BBP)
NPCI_RESPONSE_CODE_CELL = "5"  # = reconciliation.presence.NPCI_RESPONSE_CODE_CELL


def config_side(config) -> str:
    """ACQUIRER / ISSUER for a file type configured per side (its file-name
    pattern names exactly one of ACQ / ISS, as file_processing.intake.
    config_label), else ALL."""
    pattern = (config.outer_filename_pattern or "").upper()
    sides = [s for s in ("ACQ", "ISS") if s in pattern]
    if len(sides) != 1:
        return VolumeSide.ALL
    return VolumeSide.ACQUIRER if sides[0] == "ACQ" else VolumeSide.ISSUER


def _side(register_type: str, config, direction: str) -> str:
    if register_type == RegisterType.CBS_GL_EXTRACT:
        return direction if direction in (VolumeSide.DEBIT, VolumeSide.CREDIT) else VolumeSide.UNKNOWN
    if register_type in NPCI_TRANSACTION_TYPES:
        return config_side(config)
    return VolumeSide.ALL


@dataclass
class VolumeRefresh:
    business_date: date
    rows: int = 0
    facts: int = 0
    by_file_type: dict[str, int] = field(default_factory=dict)


def _approved_counts(module: PaymentModule, d: date, file_ids: list[int]) -> dict[tuple[int, int | None], int]:
    """{(uploaded file id, cycle id): approved rows} for the NPCI transaction
    files among file_ids. Raw SQL: PostgreSQL JSON operators."""
    out: dict[tuple[int, int | None], int] = {}
    if not file_ids:
        return out
    fact_table = connection.ops.quote_name(TransactionFact._meta.db_table)
    codes = ", ".join(["%s"] * len(SETTLED_CODES))
    for register_type in NPCI_TRANSACTION_TYPES:
        staging = STAGING_MODEL_BY_REGISTER_TYPE[register_type]
        history = HISTORY_MODEL_BY_STAGING_MODEL[staging]
        sql = f"""
            SELECT f.uploaded_file_id, f.settlement_cycle_id, COUNT(*) FILTER (WHERE
                btrim(regexp_replace(btrim(COALESCE(s.raw_cells ->> %s, h.raw_cells ->> %s, '')), '^''', ''))
                IN ({codes}))
            FROM {fact_table} f
            LEFT JOIN {connection.ops.quote_name(staging._meta.db_table)} s ON s.id = f.staging_row_id
            LEFT JOIN {connection.ops.quote_name(history._meta.db_table)} h
                   ON s.id IS NULL AND h.original_id = f.staging_row_id
            WHERE f.payment_module_id = %s AND f.business_date = %s AND f.source_register_type = %s
              AND f.uploaded_file_id = ANY(%s)
            GROUP BY 1, 2"""
        params = [NPCI_RESPONSE_CODE_CELL, NPCI_RESPONSE_CODE_CELL, *SETTLED_CODES, module.pk, d, register_type,
                  file_ids]
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            for file_id, cycle_id, approved in cursor.fetchall():
                out[(file_id, cycle_id)] = approved
    return out


def refresh_daily_volume(module: PaymentModule, d: date, *, source: str = "") -> VolumeRefresh:
    files = {f.pk: f for f in UploadedFile.objects.filter(payment_module=module, is_current=True)
             .select_related("file_type_config")}
    cycles = dict(SettlementCycle.objects.filter(payment_module=module).values_list("pk", "cycle_code"))
    grouped = (TransactionFact.objects.filter(payment_module=module, business_date=d, uploaded_file__is_current=True)
               .order_by().values("uploaded_file_id", "settlement_cycle_id", "direction", "source_register_type")
               .annotate(n=Count("id"), s=Sum("amount")))
    totals: dict[tuple[str, str, str], dict] = defaultdict(
        lambda: {"n": 0, "s": Decimal("0"), "approved": None, "declined": None})
    npci_keys: dict[tuple[int, int | None], tuple[str, str, str]] = {}
    npci_rows: dict[tuple[int, int | None], int] = defaultdict(int)
    for r in grouped:
        f = files[r["uploaded_file_id"]]
        config = f.file_type_config
        cycle = cycles.get(r["settlement_cycle_id"], "") if r["settlement_cycle_id"] else ""
        key = (config.file_type_code, _side(r["source_register_type"], config, r["direction"]), cycle)
        t = totals[key]
        t["n"] += r["n"]
        t["s"] += r["s"] or 0
        if r["source_register_type"] in NPCI_TRANSACTION_TYPES:
            npci_keys[(f.pk, r["settlement_cycle_id"])] = key
            npci_rows[(f.pk, r["settlement_cycle_id"])] += r["n"]
    approved = _approved_counts(module, d, sorted({fid for fid, _ in npci_keys}))
    for file_cycle, key in npci_keys.items():
        t = totals[key]
        a = approved.get(file_cycle, 0)
        t["approved"] = (t["approved"] or 0) + a
        t["declined"] = (t["declined"] or 0) + npci_rows[file_cycle] - a

    now = timezone.now()
    rows = [DailyVolume(payment_module=module, business_date=d, file_type_code=code, side=side, cycle_code=cycle,
                        row_count=t["n"], amount=t["s"], approved_count=t["approved"],
                        declined_count=t["declined"], computed_at=now)
            for (code, side, cycle), t in sorted(totals.items())]
    with transaction.atomic():
        DailyVolume.objects.filter(payment_module=module, business_date=d).delete()
        DailyVolume.objects.bulk_create(rows)
        DailyVolumeDate.objects.update_or_create(payment_module=module, business_date=d,
                                                 defaults={"computed_at": now, "source": source[:150]})
    out = VolumeRefresh(business_date=d, rows=len(rows), facts=sum(t["n"] for t in totals.values()))
    for (code, _side_, _cycle), t in totals.items():
        out.by_file_type[code] = out.by_file_type.get(code, 0) + t["n"]
    return out
