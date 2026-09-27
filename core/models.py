"""
`core` app: shared reference data + enums used across payment modules and
(later) reconciliation phases.

Per the approved plan (Phase 1, "Database Design + File Processing
Framework"): `PaymentModule` and `SettlementCycle` are real DB tables
(admin-editable reference data). `RegisterType` and `Direction` are Django
`TextChoices` rather than tables, since they're a closed enum set, not
business-editable data.
"""
from __future__ import annotations

from django.db import models


class RegisterType(models.TextChoices):
    """
    Which source register/file-type a given uploaded file (and, later, a
    normalized transaction row) belongs to.

    NOTE: no "Bank Switch" source file exists anywhere in the spec or the
    real sample data (see TRACKER.md Open Questions / the approved plan's
    open question #7), yet the Transaction Processing Engine's Transaction
    Presence Matrix / 3-way-4-way recon design assumes a Switch register.
    SWITCH is included here only so the enum's shape is stable for Phase 2 --
    nothing in this app writes rows with it, and it should not be read as
    confirmation that a Switch source file has been identified.
    """

    CBS_GL_EXTRACT = "CBS_GL_EXTRACT", "CBS GL Extract"
    RAW_DATA = "RAW_DATA", "Raw Data"
    MERCHANT_RAW_DATA = "MERCHANT_RAW_DATA", "Merchant Raw Data"
    MERCHANT_RAW_DATA_BBP = (
        "MERCHANT_RAW_DATA_BBP",
        "Merchant Raw Data (BBP) -- kept separately identifiable from standard "
        "Merchant Raw Data ACQ for audit/reporting (project owner decision, 2026-09-10, "
        "see doubts.md); identical row shape/parsing to standard Merchant Raw Data ACQ, "
        "classification only",
    )
    NTSL = "NTSL", "NTSL"
    DRC = "DRC", "Debit Reversal Confirmation Report"
    TIMEOUT = "TIMEOUT", "Timeout Report"
    ADJUSTMENT = "ADJUSTMENT", "Adjustment Report"
    SWITCH = "SWITCH", "Bank Switch (no source file identified yet -- see Open Questions)"


class Direction(models.TextChoices):
    DEBIT = "DEBIT", "Debit"
    CREDIT = "CREDIT", "Credit"
    UNKNOWN = "UNKNOWN", "Unknown"


class PaymentModule(models.Model):
    """
    A payment rail the platform reconciles: UPI is the initial scope; IMPS,
    NEFT, RTGS, ATM are planned follow-ons on the same architecture (see
    claude.md).
    """

    code = models.CharField(max_length=20, unique=True, help_text="e.g. UPI, IMPS, NEFT, RTGS, ATM")
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.code


class SettlementCycleType(models.TextChoices):
    """
    What a settlement cycle settles, per the NPCI circular's "Annexure I --
    Revised Settlement Cycles" (approved by the project owner 2026-09-27):
    cycles 1-10 settle AUTH (authorised transactions) only, with no
    disputes; DC1/DC2 are dispute-only settlement cycles. Both kinds are
    real money settlements -- the previous FINANCIAL / NON_FINANCIAL
    labels wrongly implied DC1/DC2 moved no money (renamed by
    core/migrations/0003).
    """

    AUTH = "AUTH", "AUTH (authorised transactions only, no disputes)"
    DISPUTE = "DISPUTE", "Disputes only"


class SettlementCycle(models.Model):
    """
    A settlement cycle within a payment module's business day.

    Shared across Raw Data / Merchant Raw Data / NTSL, which are the only
    cycle-wise file types per the approved plan's sample-data findings (CBS
    GL Extract, DRC, Timeout, and Adjustment reports are single daily
    files, not cycle-wise).

    UPI has 12 settlement cycles per the NPCI circular's "Annexure I --
    Revised Settlement Cycles" (approved by the project owner 2026-09-27):
    1C-10C are AUTH-only settlement cycles (no disputes), and DC1
    (00:00-16:00) / DC2 (16:00-00:00) are dispute-only settlement cycles.
    Only NTSL comes in all 12 cycles (its filenames carry "DC1"/"DC2");
    the other cycle-wise NPCI files come in (a subset of) 1C-10C only -- see each
    FileTypeConfig.cycle_set. The spec/SOP's "8 cycles" is stale (see
    doubts.md / TRACKER.md).

    Cycle rows are admin-editable reference data, not hardcoded. A fresh
    DB gets them from `manage.py seed_settlement_cycles`; the 2026-09-27
    correction of existing rows was applied by core/migrations/0003.
    """

    payment_module = models.ForeignKey(
        PaymentModule, on_delete=models.PROTECT, related_name="settlement_cycles"
    )
    cycle_code = models.CharField(max_length=10, help_text="e.g. '1C'..'10C', 'DC1', 'DC2'")
    cycle_type = models.CharField(max_length=20, choices=SettlementCycleType.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    rtgs_settlement_time = models.TimeField(
        null=True,
        blank=True,
        help_text="RTGS settlement time for this cycle, per NPCI Annexure I (e.g. 1C -> 04:00).",
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["payment_module", "cycle_code"], name="unique_cycle_per_module"
            )
        ]
        ordering = ["payment_module", "cycle_code"]

    def __str__(self) -> str:
        return f"{self.payment_module.code}-{self.cycle_code}"
