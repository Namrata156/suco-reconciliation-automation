"""
`transactions` app: the single unified `TransactionRecord` table that
Phase 2's Transaction Processing Engine matches/reconciles against.

Design direction confirmed by the project owner (approved plan, "Create
the TransactionRecord Schema"): one unified table across all source file
types, not one table per file type. Common canonical fields (rrn,
business_date, amount, direction, source register type) are real indexed
columns; whatever is unique to a given source file type lives in
`extra_fields` (JSONField), mirroring `FileTypeConfig.column_mapping`'s
use of JSONField for irregularly-shaped data.

Table partitioning was considered and explicitly deferred -- see
`doubts.md`'s "TransactionRecord table partitioning" entry for the full
reasoning; nothing here builds against it.

Scope of this module is the schema only. Populating this table from a
given source file's normalized rows is `file_processing.tasks.
load_to_unified_table`'s job, which remains a deliberate stub pending
separate follow-up work -- not built here.

Corrections/reprocessing convention: rows in this table are NEVER
deleted when a corrected file supersedes an earlier `UploadedFile`. This
is a bank reconciliation system with an explicit Auditor role in the
spec -- deleting data would destroy the audit trail of "what did the
original file say, what was wrong, and when was it corrected."
`UploadedFile.is_current` already exists for exactly this purpose (see
its docstring: "Flips to False only once a reprocessing run reaches
LOADED"). Superseded data simply stays in this table; any matching or
reporting query must filter `uploaded_file__is_current=True` to see only
the current view. `TransactionRecord` reaches `is_current` via the
`uploaded_file` FK, so no separate flag is duplicated here.
"""
from __future__ import annotations

from django.db import models

from core.models import Direction, PaymentModule, RegisterType, SettlementCycle
from file_processing.models import UploadedFile


class TransactionRecord(models.Model):
    """
    One row per normalized transaction/entry loaded from a source file,
    across all payment-module source register types (CBS GL Extract, Raw
    Data, Merchant Raw Data, NTSL, DRC, Timeout, Adjustment, ...).

    See module docstring for the never-delete-on-reprocess convention --
    filter `uploaded_file__is_current=True` in matching/reporting queries
    rather than expecting superseded rows to be absent.
    """

    payment_module = models.ForeignKey(
        PaymentModule, on_delete=models.PROTECT, related_name="transaction_records"
    )
    source_register_type = models.CharField(max_length=30, choices=RegisterType.choices)

    uploaded_file = models.ForeignKey(
        UploadedFile, on_delete=models.PROTECT, related_name="transaction_records"
    )
    settlement_cycle = models.ForeignKey(
        SettlementCycle,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transaction_records",
        help_text="Null for CBS/DRC/Timeout/Adjustment; populated for cycle-wise types",
    )

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
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    direction = models.CharField(max_length=10, choices=Direction.choices)
    row_reference = models.CharField(
        max_length=100,
        blank=True,
        help_text="Row/worksheet natural key within the source file (same pattern as FileValidationException.row_reference)",
    )
    extra_fields = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Source-specific payload -- CBS remarks, DRC/Timeout STAN/Response Code, "
            "Adjustment's ~48 extra columns, etc."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["rrn"], name="txnrecord_rrn_idx"),
            models.Index(fields=["payment_module", "business_date"], name="txnrecord_module_date_idx"),
            models.Index(
                fields=["source_register_type", "business_date"], name="txnrecord_register_date_idx"
            ),
            # Matching key per project owner's direction: RRNs are not guaranteed
            # globally unique across different business dates, so Phase 2 matching
            # (and this app's own Stage 4 population) look transactions up by
            # (rrn, business_date) together, not rrn alone. Restricted to rrn_valid=True
            # rows (partial index, supported on Postgres/SQLite) since malformed RRNs
            # (CBS's confirmed ~10% non-12-digit rate) are excluded from RRN-keyed
            # lookups anyway -- see rrn_valid's help_text above.
            models.Index(
                fields=["rrn", "business_date"],
                name="txnrecord_rrn_bizdate_idx",
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
