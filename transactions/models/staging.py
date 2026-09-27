"""
Staging layer: one raw, per-file-type table per in-scope `RegisterType`
(schema redesign approved 2026-09-23 -- see tracker.md "Schema Redesign --
Staging -> Fact -> History").

Each staging row is one parsed, pagination-stripped source row held in RAW
form -- nothing here is normalized (no RRN cleanup, no date parsing, no
amount scaling). That happens afterwards in `file_processing.tasks.
normalize_rows`, which reads these tables back by `uploaded_file_id`.

Every row always stores the full parsed cell dict in `raw_cells`
(string-keyed, exactly as the parser produced it). On top of that, each
model carries a small set of typed raw columns for the source fields that
are already confirmed for that file type. Those typed columns are filled
purely from the file type's existing `FileTypeConfig.column_mapping` (via
`file_processing.tasks._resolve_column_mapping`) -- see each model's
`TYPED_FIELD_MAPPING_KEYS`. A typed column whose mapping key is not present
in the config is left NULL; the value is still in `raw_cells`.

NTSL has a staging table (`StagingNtsl`, added 2026-09-27) but, unlike every
other entry here, it does NOT feed `TransactionFact`: NTSL is a settlement
summary with no RRN (see doubts.md), so its staged rows are promoted to
`transactions.models.settlement.NtslSettlementLine` instead, by
`file_processing.tasks.promote_to_settlement_summary`. SWITCH still has no
staging table (no Switch file exists at all -- see doubts.md).
"""
from __future__ import annotations

from django.db import models

from core.models import RegisterType
from file_processing.models import UploadedFile


class StagingRowBase(models.Model):
    """Common columns for every live staging table."""

    uploaded_file = models.ForeignKey(
        UploadedFile, on_delete=models.PROTECT, related_name="%(class)s_rows"
    )
    row_number = models.PositiveIntegerField(help_text="Row number as reported by the parser")
    row_reference = models.CharField(
        max_length=100,
        help_text=(
            "'<worksheet>:<row_number>' natural key within the source file -- same format "
            "as TransactionFact.row_reference / FileValidationException.row_reference"
        ),
    )
    raw_cells = models.JSONField(
        default=dict,
        blank=True,
        help_text="Full parsed cell dict for this row (string keys), always stored verbatim",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # {typed_field_name: FileTypeConfig.column_mapping key}. Overridden per
    # concrete model; consumed by file_processing.tasks.load_to_staging.
    TYPED_FIELD_MAPPING_KEYS: dict[str, str] = {}

    class Meta:
        abstract = True
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["uploaded_file", "row_reference"],
                name="%(app_label)s_%(class)s_unique_row",
            ),
        ]

    def __str__(self) -> str:
        return f"{type(self).__name__}#{self.pk} ({self.row_reference})"


# ---------------------------------------------------------------------------
# Typed raw-column mixins. Shared between each live staging model and its
# History mirror (transactions/models/history.py) so the two can't drift.
# All values are raw strings as parsed -- never normalized.
# ---------------------------------------------------------------------------
class CbsGlExtractRawFields(models.Model):
    worksheet = models.CharField(max_length=100, null=True, blank=True)
    raw_date = models.TextField(null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)
    withdrawals = models.TextField(null=True, blank=True)
    deposits = models.TextField(null=True, blank=True)
    balance = models.TextField(null=True, blank=True)

    TYPED_FIELD_MAPPING_KEYS = {
        "raw_date": "date",
        "remarks": "remarks",
        "withdrawals": "withdrawals",
        "deposits": "deposits",
        "balance": "balance",
    }

    class Meta:
        abstract = True


class RawDataRawFields(models.Model):
    """Shared by Raw Data, Merchant Raw Data, and Merchant Raw Data BBP
    (identical headerless TX-row shape -- only classification differs)."""

    rrn_raw = models.TextField(null=True, blank=True)
    amount_paise = models.TextField(
        null=True, blank=True, help_text="Raw amount cell as parsed (paise, unscaled)"
    )

    TYPED_FIELD_MAPPING_KEYS = {"rrn_raw": "rrn", "amount_paise": "amount"}

    class Meta:
        abstract = True


class DisputeReportRawFields(models.Model):
    """Shared by DRC and Timeout (identical column layout, confirmed)."""

    rrn_raw = models.TextField(null=True, blank=True)
    txn_date_raw = models.TextField(null=True, blank=True)
    amount_raw = models.TextField(null=True, blank=True)

    TYPED_FIELD_MAPPING_KEYS = {"rrn_raw": "rrn", "txn_date_raw": "date", "amount_raw": "amount"}

    class Meta:
        abstract = True


class AdjustmentRawFields(models.Model):
    rrn_raw = models.TextField(null=True, blank=True)
    adj_date_raw = models.TextField(null=True, blank=True)
    txn_date_raw = models.TextField(null=True, blank=True)
    txn_amount_raw = models.TextField(null=True, blank=True)
    adj_amount_raw = models.TextField(null=True, blank=True)

    # "txn_date"/"txn_amount" are NOT present in the ADJUSTMENT
    # FileTypeConfig.column_mapping today (only rrn/date/amount are) -- so
    # txn_date_raw/txn_amount_raw stay NULL (values remain in raw_cells)
    # until those keys are added to the config. No column position is
    # hardcoded here.
    TYPED_FIELD_MAPPING_KEYS = {
        "rrn_raw": "rrn",
        "adj_date_raw": "date",
        "adj_amount_raw": "amount",
        "txn_date_raw": "txn_date",
        "txn_amount_raw": "txn_amount",
    }

    class Meta:
        abstract = True


class NtslRawFields(models.Model):
    """NTSL settlement summary: `Description | No of Txns | Debit | Credit`
    (columns 1-4, confirmed against all 12 real files in 14072026/NTSL/).
    Every parsed row is staged -- titles, header, blank and heading rows
    included -- so these are all nullable raw strings."""

    description_raw = models.TextField(null=True, blank=True)
    txn_count_raw = models.TextField(null=True, blank=True)
    debit_raw = models.TextField(null=True, blank=True)
    credit_raw = models.TextField(null=True, blank=True)

    TYPED_FIELD_MAPPING_KEYS = {
        "description_raw": "description",
        "txn_count_raw": "txn_count",
        "debit_raw": "debit",
        "credit_raw": "credit",
    }

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# Live staging tables
# ---------------------------------------------------------------------------
class StagingCbsGlExtract(StagingRowBase, CbsGlExtractRawFields):
    TYPED_FIELD_MAPPING_KEYS = CbsGlExtractRawFields.TYPED_FIELD_MAPPING_KEYS

    class Meta(StagingRowBase.Meta):
        pass


class StagingRawData(StagingRowBase, RawDataRawFields):
    TYPED_FIELD_MAPPING_KEYS = RawDataRawFields.TYPED_FIELD_MAPPING_KEYS

    class Meta(StagingRowBase.Meta):
        pass


class StagingMerchantRawData(StagingRowBase, RawDataRawFields):
    TYPED_FIELD_MAPPING_KEYS = RawDataRawFields.TYPED_FIELD_MAPPING_KEYS

    class Meta(StagingRowBase.Meta):
        pass


class StagingMerchantRawDataBbp(StagingRowBase, RawDataRawFields):
    TYPED_FIELD_MAPPING_KEYS = RawDataRawFields.TYPED_FIELD_MAPPING_KEYS

    class Meta(StagingRowBase.Meta):
        pass


class StagingDrc(StagingRowBase, DisputeReportRawFields):
    TYPED_FIELD_MAPPING_KEYS = DisputeReportRawFields.TYPED_FIELD_MAPPING_KEYS

    class Meta(StagingRowBase.Meta):
        pass


class StagingTimeout(StagingRowBase, DisputeReportRawFields):
    TYPED_FIELD_MAPPING_KEYS = DisputeReportRawFields.TYPED_FIELD_MAPPING_KEYS

    class Meta(StagingRowBase.Meta):
        pass


class StagingAdjustment(StagingRowBase, AdjustmentRawFields):
    TYPED_FIELD_MAPPING_KEYS = AdjustmentRawFields.TYPED_FIELD_MAPPING_KEYS

    class Meta(StagingRowBase.Meta):
        pass


class StagingNtsl(StagingRowBase, NtslRawFields):
    TYPED_FIELD_MAPPING_KEYS = NtslRawFields.TYPED_FIELD_MAPPING_KEYS

    class Meta(StagingRowBase.Meta):
        pass


# Resolves TransactionFact.staging_row_id (a plain ID, no DB FK) back to its
# staging table, keyed by TransactionFact.source_register_type. Also used by
# load_to_staging to pick the table for an UploadedFile's register_type.
# NOTE: not every entry feeds TransactionFact -- RegisterType.NTSL's staging
# rows are promoted to NtslSettlementLine instead (see module docstring), so
# don't assume "has a staging table" implies "produces TransactionFact rows".
STAGING_MODEL_BY_REGISTER_TYPE: dict[str, type[StagingRowBase]] = {
    RegisterType.CBS_GL_EXTRACT: StagingCbsGlExtract,
    RegisterType.RAW_DATA: StagingRawData,
    RegisterType.MERCHANT_RAW_DATA: StagingMerchantRawData,
    RegisterType.MERCHANT_RAW_DATA_BBP: StagingMerchantRawDataBbp,
    RegisterType.DRC: StagingDrc,
    RegisterType.TIMEOUT: StagingTimeout,
    RegisterType.ADJUSTMENT: StagingAdjustment,
    RegisterType.NTSL: StagingNtsl,
}
