"""
Settlement-summary layer: `NtslSettlementLine`, one row per NTSL category
line (e.g. "Remitter QR Code-Default U2 Approved Transaction Amount") plus
the file's "Final Settlement Amount" total line.

NTSL is a per-cycle settlement summary/totals report with no RRN column
(see doubts.md "NTSL -- not an RRN-level register"), so it structurally
cannot live in the RRN-indexed `TransactionFact`. Its raw rows are staged in
`transactions.models.staging.StagingNtsl` like every other file type, then
`file_processing.tasks.promote_to_settlement_summary` promotes only the
genuine category lines here (title, header, blank and heading rows are not
loaded).

This table is loading only. NTSL validation / settlement reconciliation
(the SOP's "(deposit - withdrawal) vs (NTSL credit - NTSL debit)" check) is
Phase 4 Settlement Control work and is not built here.

Corrections/reprocessing convention matches TransactionFact: rows are never
deleted when a corrected file supersedes an earlier UploadedFile; queries
filter `uploaded_file__is_current=True`.
"""
from __future__ import annotations

from django.db import models

from core.models import PaymentModule, RegisterType, SettlementCycle
from file_processing.models import UploadedFile


class NtslSettlementLineColumns(models.Model):
    """Non-FK columns of NtslSettlementLine, shared with
    NtslSettlementLineHistory (transactions/models/history.py) so the two
    can't drift."""

    business_date = models.DateField(help_text="The UploadedFile's business_date")
    row_reference = models.CharField(
        max_length=100,
        help_text="'<worksheet>:<row_number>' natural key within the source file (same as the staging row's)",
    )
    staging_row_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Lineage: ID of the source StagingNtsl row. Plain ID, no DB FK.",
    )
    description = models.TextField(help_text="Category description, whitespace-stripped")
    txn_count = models.BigIntegerField(
        null=True,
        blank=True,
        help_text=(
            "'No of Txns' as an integer. NULL when blank in the source (e.g. the Final "
            "Settlement Amount line) or when the source value is not integral (a WARNING "
            "is recorded in that case rather than guessing)."
        ),
    )
    debit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    is_final_settlement = models.BooleanField(
        default=False, help_text="True for the file's 'Final Settlement Amount' total line"
    )

    class Meta:
        abstract = True


class NtslSettlementLine(NtslSettlementLineColumns):
    payment_module = models.ForeignKey(
        PaymentModule, on_delete=models.PROTECT, related_name="ntsl_settlement_lines"
    )
    uploaded_file = models.ForeignKey(
        UploadedFile, on_delete=models.PROTECT, related_name="ntsl_settlement_lines"
    )
    settlement_cycle = models.ForeignKey(
        SettlementCycle,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ntsl_settlement_lines",
        help_text="NTSL is cycle-wise (1C-10C, DC1, DC2); nullable to match TransactionFact",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["uploaded_file_id", "id"]
        indexes = [
            models.Index(
                fields=["payment_module", "business_date", "settlement_cycle"],
                name="ntslline_module_date_cycle_idx",
            ),
            models.Index(fields=["description"], name="ntslline_description_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["uploaded_file", "row_reference"],
                name="unique_ntsl_line_per_uploaded_file",
            ),
        ]

    def __str__(self) -> str:
        return f"NTSL:{self.description} ({self.business_date}, Dr {self.debit} / Cr {self.credit})"

    def staging_row(self):
        """Resolves staging_row_id to its StagingNtsl row, or None."""
        from transactions.models.staging import STAGING_MODEL_BY_REGISTER_TYPE

        if self.staging_row_id is None:
            return None
        return STAGING_MODEL_BY_REGISTER_TYPE[RegisterType.NTSL].objects.filter(pk=self.staging_row_id).first()
