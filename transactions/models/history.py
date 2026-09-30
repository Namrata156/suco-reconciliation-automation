"""
History layer: one `<Model>History` table per live staging table plus
`TransactionFactHistory` and `NtslSettlementLineHistory` (NTSL tables added
2026-09-27). Reconciliation's two history tables live in
`reconciliation/models.py`.

Schema redesign approved 2026-09-23 (tracker.md "Schema Redesign").
Staging archival was pulled forward from Phase 3 at the project owner's
request (decided 2026-09-28): `transactions.services.archival` copies live
staging rows whose file's business date is older than the latest loaded
business date into the matching table below (`HISTORY_MODEL_BY_STAGING_MODEL`),
then deletes them from staging. It runs manually via the
`archive_older_business_dates` management command. The fact, NTSL
settlement and reconciliation history tables are still schema-only -- see
tracker.md Open Questions ("Archival job trigger").

Conventions for every history table:
  - Mirrors the live model's columns (via the same shared abstract mixins,
    so they can't drift), plus `original_id` (the live row's PK),
    `archived_at`, and `archival_reason` (free text).
  - No unique constraints copied from the live table. The same logical row
    may be archived more than once (e.g. re-archived after a reprocess), so
    live-table uniqueness would block legitimate archiving.
  - `created_at`/`updated_at` are plain DateTimeFields (not auto_now) so
    the live row's original timestamps are preserved on copy.
  - FKs to stable, never-deleted tables (PaymentModule, SettlementCycle,
    UploadedFile) are kept as FKs with `related_name="+"`.
"""
from __future__ import annotations

from django.db import models
from django.utils import timezone

from core.models import PaymentModule, SettlementCycle
from file_processing.models import UploadedFile
from transactions.models.fact import TransactionFactColumns
from transactions.models.settlement import NtslSettlementLineColumns
from transactions.models.staging import (
    AdjustmentRawFields,
    CbsGlExtractRawFields,
    DisputeReportRawFields,
    NtslRawFields,
    RawDataRawFields,
    StagingAdjustment,
    StagingCbsGlExtract,
    StagingDrc,
    StagingMerchantRawData,
    StagingMerchantRawDataBbp,
    StagingNtsl,
    StagingRawData,
    StagingRowBase,
    StagingTimeout,
)


class ArchivalFields(models.Model):
    original_id = models.BigIntegerField(db_index=True, help_text="PK of the live row this was archived from")
    archived_at = models.DateTimeField(default=timezone.now, db_index=True)
    archival_reason = models.TextField(blank=True)

    class Meta:
        abstract = True


class StagingHistoryBase(ArchivalFields):
    """Mirrors transactions.models.staging.StagingRowBase's columns."""

    uploaded_file = models.ForeignKey(UploadedFile, on_delete=models.PROTECT, related_name="+")
    row_number = models.PositiveIntegerField()
    row_reference = models.CharField(max_length=100)
    raw_cells = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(help_text="Copied from the live staging row")

    class Meta:
        abstract = True
        ordering = ["-archived_at", "original_id"]

    def __str__(self) -> str:
        return f"{type(self).__name__}#{self.pk} (orig {self.original_id}, {self.row_reference})"


class StagingCbsGlExtractHistory(StagingHistoryBase, CbsGlExtractRawFields):
    class Meta(StagingHistoryBase.Meta):
        pass


class StagingRawDataHistory(StagingHistoryBase, RawDataRawFields):
    class Meta(StagingHistoryBase.Meta):
        pass


class StagingMerchantRawDataHistory(StagingHistoryBase, RawDataRawFields):
    class Meta(StagingHistoryBase.Meta):
        pass


class StagingMerchantRawDataBbpHistory(StagingHistoryBase, RawDataRawFields):
    class Meta(StagingHistoryBase.Meta):
        pass


class StagingDrcHistory(StagingHistoryBase, DisputeReportRawFields):
    class Meta(StagingHistoryBase.Meta):
        pass


class StagingTimeoutHistory(StagingHistoryBase, DisputeReportRawFields):
    class Meta(StagingHistoryBase.Meta):
        pass


class StagingAdjustmentHistory(StagingHistoryBase, AdjustmentRawFields):
    class Meta(StagingHistoryBase.Meta):
        pass


class StagingNtslHistory(StagingHistoryBase, NtslRawFields):
    class Meta(StagingHistoryBase.Meta):
        pass


# Live staging table -> its History mirror. Used by
# transactions.services.archival (copy target) and
# TransactionFact.staging_row() (lineage lookup after a row is archived).
HISTORY_MODEL_BY_STAGING_MODEL: dict[type[StagingRowBase], type[StagingHistoryBase]] = {
    StagingCbsGlExtract: StagingCbsGlExtractHistory,
    StagingRawData: StagingRawDataHistory,
    StagingMerchantRawData: StagingMerchantRawDataHistory,
    StagingMerchantRawDataBbp: StagingMerchantRawDataBbpHistory,
    StagingDrc: StagingDrcHistory,
    StagingTimeout: StagingTimeoutHistory,
    StagingAdjustment: StagingAdjustmentHistory,
    StagingNtsl: StagingNtslHistory,
}


class NtslSettlementLineHistory(ArchivalFields, NtslSettlementLineColumns):
    """Mirrors NtslSettlementLine. The live table's
    unique_ntsl_line_per_uploaded_file constraint is intentionally NOT
    copied (see module docstring)."""

    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="+")
    uploaded_file = models.ForeignKey(UploadedFile, on_delete=models.PROTECT, related_name="+")
    settlement_cycle = models.ForeignKey(
        SettlementCycle, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(help_text="Copied from the live settlement line")
    updated_at = models.DateTimeField(help_text="Copied from the live settlement line")

    class Meta:
        ordering = ["-archived_at", "original_id"]
        indexes = [
            models.Index(
                fields=["payment_module", "business_date", "settlement_cycle"],
                name="ntsllinehist_mod_date_cyc_idx",
            ),
            models.Index(fields=["uploaded_file", "row_reference"], name="ntsllinehist_file_row_idx"),
        ]

    def __str__(self) -> str:
        return f"History:NTSL:{self.description} (orig {self.original_id})"


class TransactionFactHistory(ArchivalFields, TransactionFactColumns):
    """Mirrors TransactionFact. The live table's unique_row_per_uploaded_file
    constraint is intentionally NOT copied (see module docstring); its
    lookup indexes are."""

    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="+")
    uploaded_file = models.ForeignKey(UploadedFile, on_delete=models.PROTECT, related_name="+")
    settlement_cycle = models.ForeignKey(
        SettlementCycle, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(help_text="Copied from the live fact row")
    updated_at = models.DateTimeField(help_text="Copied from the live fact row")

    class Meta:
        ordering = ["-archived_at", "original_id"]
        indexes = [
            models.Index(fields=["rrn"], name="txnfacthist_rrn_idx"),
            models.Index(fields=["payment_module", "business_date"], name="txnfacthist_module_date_idx"),
            models.Index(
                fields=["source_register_type", "business_date"], name="txnfacthist_reg_date_idx"
            ),
            models.Index(
                fields=["rrn", "business_date"],
                name="txnfacthist_rrn_bizdate_idx",
                condition=models.Q(rrn_valid=True),
            ),
            models.Index(fields=["uploaded_file", "row_reference"], name="txnfacthist_file_row_idx"),
        ]

    def __str__(self) -> str:
        return f"History:{self.source_register_type}:{self.rrn or self.row_reference} (orig {self.original_id})"
