"""
Fact layer: `TransactionFact`, the single unified, RRN-indexed transaction
table that Phase 2's Transaction Processing Engine matches against.
Replaces the former `TransactionRecord` (schema redesign approved
2026-09-23 -- see tracker.md "Schema Redesign -- Staging -> Fact ->
History").

Same unified shape, indexes, and constraints as `TransactionRecord` had
(one table across all source file types, canonical fields as real indexed
columns -- see doubts.md's "TransactionRecord schema shape" resolution),
with these changes:
  - `extra_fields` is removed. The raw source row now lives in its
    per-file-type staging table (transactions/models/staging.py), reached
    through `staging_row_id` + `source_register_type`.
  - `staging_row_id`: plain BigIntegerField lineage pointer, no DB FK (the
    target table varies by source_register_type). Resolve it with
    `staging_row()` / `STAGING_MODEL_BY_REGISTER_TYPE`.
  - `txn_amount`: nullable, Adjustment only -- the original disputed
    transaction's `Txnamount`. `amount` still holds `Adjamount` for
    Adjustment rows, unchanged (see doubts.md's Adjustment amount
    resolution, 2026-09-17).

Table partitioning is still deferred -- see doubts.md / tracker.md Open
Questions (archival now being in scope is one of that row's revisit
triggers).

Corrections/reprocessing convention (unchanged from TransactionRecord):
rows are NEVER deleted when a corrected file supersedes an earlier
`UploadedFile`. Matching/reporting queries filter
`uploaded_file__is_current=True` instead.
"""
from __future__ import annotations

from django.db import models

from core.models import Direction, PaymentModule, RegisterType, SettlementCycle
from file_processing.models import UploadedFile


class TransactionFactColumns(models.Model):
    """Non-FK columns of TransactionFact, shared with TransactionFactHistory
    (transactions/models/history.py) so the two can't drift. FKs are
    declared on each concrete model since their related_name/on_delete
    differ between the live and history tables."""

    source_register_type = models.CharField(max_length=30, choices=RegisterType.choices)
    business_date = models.DateField(
        help_text="Canonical per-row date after normalize_rows' date resolution"
    )
    rrn = models.CharField(max_length=20, blank=True, help_text="Main matching key")
    rrn_valid = models.BooleanField(
        default=False,
        help_text=(
            "Carry-over of normalize_rows' rrn_valid. CBS's ~10% non-12-digit rate is "
            "expected, not an error -- Phase 2 matching must filter on this before "
            "RRN-keyed joins to avoid false-positive collisions between malformed RRNs."
        ),
    )
    amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="This row's own amount. For Adjustment rows this is Adjamount (unchanged).",
    )
    txn_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Adjustment rows only: the original disputed transaction's Txnamount. "
            "NULL for every other source register type."
        ),
    )
    direction = models.CharField(max_length=10, choices=Direction.choices)
    row_reference = models.CharField(
        max_length=100,
        blank=True,
        help_text="Row/worksheet natural key within the source file (same pattern as FileValidationException.row_reference)",
    )
    staging_row_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Lineage: ID of the source row in the staging table for source_register_type "
            "(see STAGING_MODEL_BY_REGISTER_TYPE). Plain ID, no DB FK. NULL only for rows "
            "not loaded through the staging pipeline (e.g. a register type with no "
            "staging table, such as SWITCH)."
        ),
    )

    class Meta:
        abstract = True


class TransactionFact(TransactionFactColumns):
    """
    One row per normalized transaction/entry promoted from a staging row,
    across all in-scope source register types (CBS GL Extract, Raw Data,
    Merchant Raw Data, Merchant Raw Data BBP, DRC, Timeout, Adjustment).
    """

    payment_module = models.ForeignKey(
        PaymentModule, on_delete=models.PROTECT, related_name="transaction_facts"
    )
    uploaded_file = models.ForeignKey(
        UploadedFile, on_delete=models.PROTECT, related_name="transaction_facts"
    )
    settlement_cycle = models.ForeignKey(
        SettlementCycle,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transaction_facts",
        help_text="Null for CBS/DRC/Timeout/Adjustment; populated for cycle-wise types",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["rrn"], name="txnfact_rrn_idx"),
            models.Index(fields=["payment_module", "business_date"], name="txnfact_module_date_idx"),
            models.Index(
                fields=["source_register_type", "business_date"], name="txnfact_register_date_idx"
            ),
            # Matching key: (rrn, business_date) together, restricted to
            # rrn_valid=True rows -- carried over unchanged from
            # TransactionRecord's txnrecord_rrn_bizdate_idx.
            models.Index(
                fields=["rrn", "business_date"],
                name="txnfact_rrn_bizdate_idx",
                condition=models.Q(rrn_valid=True),
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["uploaded_file", "row_reference"],
                name="unique_row_per_uploaded_file",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_register_type}:{self.rrn or self.row_reference} ({self.business_date})"

    def staging_row(self):
        """Resolves staging_row_id to its staging-table row, or None. Once
        the staging row has been archived (transactions.services.archival),
        falls back to its History copy (matched on original_id)."""
        from transactions.models.history import HISTORY_MODEL_BY_STAGING_MODEL
        from transactions.models.staging import STAGING_MODEL_BY_REGISTER_TYPE

        staging_model = STAGING_MODEL_BY_REGISTER_TYPE.get(self.source_register_type)
        if staging_model is None or self.staging_row_id is None:
            return None
        live = staging_model.objects.filter(pk=self.staging_row_id).first()
        if live is not None:
            return live
        return (
            HISTORY_MODEL_BY_STAGING_MODEL[staging_model]
            .objects.filter(original_id=self.staging_row_id)
            .order_by("-archived_at")
            .first()
        )
