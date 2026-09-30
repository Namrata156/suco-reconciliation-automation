"""Read-only admin visibility for the staging / fact / NTSL settlement /
history tables,
matching the admin-first, read-only convention used by
`reconciliation/admin.py` (no upload API/frontend exists yet). Rows in
these tables are only ever written by the pipeline (file_processing.tasks)
or, for staging history tables, by the archival job
(transactions.services.archival, pulled forward from Phase 3) -- never by
hand."""
from __future__ import annotations

from django.contrib import admin

from transactions.models import (
    NtslSettlementLine,
    NtslSettlementLineHistory,
    StagingAdjustment,
    StagingAdjustmentHistory,
    StagingCbsGlExtract,
    StagingCbsGlExtractHistory,
    StagingDrc,
    StagingDrcHistory,
    StagingMerchantRawData,
    StagingMerchantRawDataBbp,
    StagingMerchantRawDataBbpHistory,
    StagingMerchantRawDataHistory,
    StagingNtsl,
    StagingNtslHistory,
    StagingRawData,
    StagingRawDataHistory,
    StagingTimeout,
    StagingTimeoutHistory,
    TransactionFact,
    TransactionFactHistory,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TransactionFact)
class TransactionFactAdmin(ReadOnlyAdmin):
    list_display = (
        "source_register_type",
        "rrn",
        "rrn_valid",
        "business_date",
        "amount",
        "txn_amount",
        "direction",
        "uploaded_file",
        "row_reference",
        "staging_row_id",
    )
    list_filter = ("payment_module", "source_register_type", "direction", "rrn_valid")
    search_fields = ("rrn", "row_reference")
    date_hierarchy = "business_date"
    list_select_related = ("uploaded_file",)


@admin.register(TransactionFactHistory)
class TransactionFactHistoryAdmin(ReadOnlyAdmin):
    list_display = (
        "original_id",
        "source_register_type",
        "rrn",
        "business_date",
        "amount",
        "txn_amount",
        "direction",
        "archived_at",
        "archival_reason",
    )
    list_filter = ("source_register_type", "direction")
    search_fields = ("rrn", "row_reference", "original_id")


@admin.register(NtslSettlementLine)
class NtslSettlementLineAdmin(ReadOnlyAdmin):
    list_display = (
        "description",
        "txn_count",
        "debit",
        "credit",
        "is_final_settlement",
        "business_date",
        "settlement_cycle",
        "uploaded_file",
        "row_reference",
        "staging_row_id",
    )
    list_filter = ("payment_module", "settlement_cycle", "is_final_settlement")
    search_fields = ("description", "row_reference")
    date_hierarchy = "business_date"
    list_select_related = ("uploaded_file", "settlement_cycle")


@admin.register(NtslSettlementLineHistory)
class NtslSettlementLineHistoryAdmin(ReadOnlyAdmin):
    list_display = (
        "original_id",
        "description",
        "txn_count",
        "debit",
        "credit",
        "is_final_settlement",
        "business_date",
        "archived_at",
        "archival_reason",
    )
    list_filter = ("is_final_settlement",)
    search_fields = ("description", "row_reference", "original_id")


_STAGING_TYPED_COLUMNS = {
    StagingCbsGlExtract: ("worksheet", "raw_date", "remarks", "withdrawals", "deposits", "balance"),
    StagingRawData: ("rrn_raw", "amount_paise"),
    StagingMerchantRawData: ("rrn_raw", "amount_paise"),
    StagingMerchantRawDataBbp: ("rrn_raw", "amount_paise"),
    StagingDrc: ("rrn_raw", "txn_date_raw", "amount_raw"),
    StagingTimeout: ("rrn_raw", "txn_date_raw", "amount_raw"),
    StagingAdjustment: ("rrn_raw", "adj_date_raw", "txn_date_raw", "txn_amount_raw", "adj_amount_raw"),
    StagingNtsl: ("description_raw", "txn_count_raw", "debit_raw", "credit_raw"),
}
_STAGING_HISTORY_OF = {
    StagingCbsGlExtractHistory: StagingCbsGlExtract,
    StagingRawDataHistory: StagingRawData,
    StagingMerchantRawDataHistory: StagingMerchantRawData,
    StagingMerchantRawDataBbpHistory: StagingMerchantRawDataBbp,
    StagingDrcHistory: StagingDrc,
    StagingTimeoutHistory: StagingTimeout,
    StagingAdjustmentHistory: StagingAdjustment,
    StagingNtslHistory: StagingNtsl,
}

for _model, _typed in _STAGING_TYPED_COLUMNS.items():
    admin.site.register(
        _model,
        type(
            f"{_model.__name__}Admin",
            (ReadOnlyAdmin,),
            {
                "list_display": ("uploaded_file", "row_reference", *_typed),
                "search_fields": ("row_reference",),
                "list_select_related": ("uploaded_file",),
            },
        ),
    )

for _history_model, _live_model in _STAGING_HISTORY_OF.items():
    admin.site.register(
        _history_model,
        type(
            f"{_history_model.__name__}Admin",
            (ReadOnlyAdmin,),
            {
                "list_display": (
                    "original_id",
                    "uploaded_file",
                    "row_reference",
                    *_STAGING_TYPED_COLUMNS[_live_model],
                    "archived_at",
                    "archival_reason",
                ),
                "search_fields": ("row_reference", "original_id"),
            },
        ),
    )
