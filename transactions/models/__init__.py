"""
`transactions` app models, split into staging / fact / settlement / history
modules (schema redesign approved 2026-09-23 -- see tracker.md "Schema
Redesign -- Staging -> Fact -> History"; NTSL settlement tables added
2026-09-27).

Every concrete model MUST be re-exported here: Django discovers an app's
models by importing `<app>.models`, so a model left out of this file would
be silently skipped by makemigrations.
"""
from transactions.models.fact import TransactionFact, TransactionFactColumns
from transactions.models.history import (
    HISTORY_MODEL_BY_STAGING_MODEL,
    NtslSettlementLineHistory,
    StagingAdjustmentHistory,
    StagingCbsGlExtractHistory,
    StagingDrcHistory,
    StagingMerchantRawDataBbpHistory,
    StagingMerchantRawDataHistory,
    StagingNtslHistory,
    StagingRawDataHistory,
    StagingTimeoutHistory,
    TransactionFactHistory,
)
from transactions.models.settlement import NtslSettlementLine, NtslSettlementLineColumns
from transactions.models.staging import (
    STAGING_MODEL_BY_REGISTER_TYPE,
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

__all__ = [
    "STAGING_MODEL_BY_REGISTER_TYPE",
    "StagingRowBase",
    "StagingCbsGlExtract",
    "StagingRawData",
    "StagingMerchantRawData",
    "StagingMerchantRawDataBbp",
    "StagingDrc",
    "StagingTimeout",
    "StagingAdjustment",
    "StagingNtsl",
    "TransactionFactColumns",
    "TransactionFact",
    "NtslSettlementLineColumns",
    "NtslSettlementLine",
    "HISTORY_MODEL_BY_STAGING_MODEL",
    "StagingCbsGlExtractHistory",
    "StagingRawDataHistory",
    "StagingMerchantRawDataHistory",
    "StagingMerchantRawDataBbpHistory",
    "StagingDrcHistory",
    "StagingTimeoutHistory",
    "StagingAdjustmentHistory",
    "StagingNtslHistory",
    "TransactionFactHistory",
    "NtslSettlementLineHistory",
]
