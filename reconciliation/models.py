"""
`reconciliation` app: Transaction Processing Engine, first slice --
2-way presence matching (CBS vs. NPCI-represented-by-Raw-Data/Merchant-
Raw-Data), designed to be extensible to a future 3rd/4th register
without a schema rewrite.

Why a new app rather than extending `transactions`: `transactions`'
stated scope is the source-of-truth ledger table (`TransactionFact`,
formerly `TransactionRecord`)
populated by the File Processing Framework; the Presence Matrix here is
a derived/computed layer on top of that ledger, consumed next by the
Business Rule Engine (a separate tracked component) -- mirrors the
existing precedent of `file_processing` (intake) vs `transactions`
(unified ledger) as separate apps for separate responsibilities.

Confirmed inputs this design is built against (not re-derived here --
see `doubts.md` "Resolved" section, `TRACKER.md` Open Questions, and the
approved plan for this slice):
  - Model: 2-way (CBS vs NPCI-represented-by-Raw-Data/Merchant-Raw-Data),
    confirmed with the project owner 2026-09-13.
  - Matching key: (rrn, business_date) together, filtered to
    rrn_valid=True -- the direction already baked into
    `TransactionFact`'s partial composite index.
  - DRC/Timeout/Adjustment are dispute overlays, NOT presence-matrix legs
    -- excluded from this engine's leg set entirely (second-slice work).
  - NTSL is out of scope for this component (separate settlement-total
    table, Phase 4).
  - Spec field list (Transaction Presence Matrix section): Transaction
    ID, Payment Module, Business Date, Settlement Cycle, CBS Presence,
    Switch Presence, NPCI Presence, GL Presence, Amount Match, Status
    Match, Error Code, Processing Status (Matched/Unmatched/Pending Rule
    Evaluation). Spec's own closing line: "The Transaction Processing
    Engine shall remain independent of the number of participating
    registers."
"""
from __future__ import annotations

from django.db import models

from core.models import PaymentModule, SettlementCycle
from django.utils import timezone

from transactions.models import TransactionFact


class ProcessingStatus(models.TextChoices):
    """Exact 3 values from the spec's Processing Status field."""

    MATCHED = "MATCHED", "Matched"
    UNMATCHED = "UNMATCHED", "Unmatched"
    PENDING_RULE_EVALUATION = "PENDING_RULE_EVALUATION", "Pending Rule Evaluation"


class TransactionMatchGroup(models.Model):
    """
    One row per matching key `(payment_module, rrn, business_date)` found
    in at least one comparison leg -- the spec's Transaction Presence
    Matrix, one row per candidate transaction.
    """

    payment_module = models.ForeignKey(
        PaymentModule, on_delete=models.PROTECT, related_name="match_groups"
    )
    rrn = models.CharField(max_length=20, help_text="Matching key component; see TransactionFact.rrn")
    business_date = models.DateField(help_text="Matching key component")

    settlement_cycle = models.ForeignKey(
        SettlementCycle,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="match_groups",
        help_text=(
            "Informational only, derived from whichever present leg's "
            "TransactionFact.settlement_cycle is set (CBS has none; NPCI-side "
            "cycle-wise types do) -- NOT part of the matching key."
        ),
    )

    amount_match = models.BooleanField(
        null=True,
        blank=True,
        help_text=(
            "True/False only when 2+ legs are present and comparable; None = not "
            "applicable (one-sided)."
        ),
    )
    status_match = models.BooleanField(
        null=True,
        blank=True,
        help_text=(
            "Spec says 'if applicable'; TransactionFact has no transaction-status "
            "field today, so this stays None for this slice (documented as N/A, not "
            "silently dropped). The NPCI side's status is recorded on "
            "RegisterPresence.response_code instead (CBS carries no status)."
        ),
    )
    direction_match = models.BooleanField(
        null=True,
        blank=True,
        help_text=(
            "True when the CBS and NPCI legs carry OPPOSITE directions -- the normal "
            "pairing on real data (CBS GL 1535 vs NPCI's labelling, 100% of ~61K "
            "pairs/day, profiled 2026-09-30). None when either leg is absent or "
            "UNKNOWN."
        ),
    )
    cbs_reversed = models.BooleanField(
        default=False,
        help_text=(
            "CBS holds both a CREDIT and a DEBIT of the same amount for this RRN -- "
            "the transaction was reversed in CBS (the manual sheet's 'rev' check). "
            "The CBS presence row points at the original leg, not the reversal."
        ),
    )
    is_superseded = models.BooleanField(
        default=False,
        help_text=(
            "Set when a rebuild for this business date no longer finds this RRN in "
            "any current file (e.g. a corrected file dropped it). Kept, not deleted, "
            "for audit (project owner decision 2026-09-30); cleared if the RRN "
            "reappears."
        ),
    )
    superseded_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(
        max_length=50,
        blank=True,
        help_text=(
            "Reserved placeholder -- Business Rule Engine's error/response-code table "
            "is still an unresolved spec gap (see TRACKER.md Open Questions), so this "
            "field is intentionally left for that component to populate later, not "
            "guessed at here."
        ),
    )
    processing_status = models.CharField(
        max_length=30, choices=ProcessingStatus.choices, default=ProcessingStatus.PENDING_RULE_EVALUATION
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["payment_module", "rrn", "business_date"],
                name="unique_match_group_per_module_rrn_date",
            ),
        ]
        indexes = [
            models.Index(fields=["payment_module", "business_date"], name="matchgroup_module_date_idx"),
            models.Index(fields=["processing_status"], name="matchgroup_status_idx"),
        ]
        ordering = ["payment_module", "business_date", "rrn"]

    def __str__(self) -> str:
        return f"{self.payment_module.code}:{self.rrn} ({self.business_date}) [{self.processing_status}]"


class RegisterPresence(models.Model):
    """
    The extensible per-register child table -- the mechanism satisfying
    "don't hardcode two fixed presence columns". A future 3rd/4th register
    is one new `DEFAULT_PRESENCE_LEGS` config entry + new `RegisterPresence`
    rows, never a new named boolean column or a migration.
    """

    match_group = models.ForeignKey(
        TransactionMatchGroup, on_delete=models.CASCADE, related_name="register_presences"
    )
    leg = models.CharField(
        max_length=30,
        help_text=(
            "Logical leg name (e.g. 'CBS', 'NPCI') -- not a Django TextChoices enum "
            "tied to a migration; the leg's meaning comes from reconciliation.presence."
            "DEFAULT_PRESENCE_LEGS, a plain Python config dict, so adding a 3rd/4th leg "
            "is a code-level config change, never a schema/migration change."
        ),
    )
    present = models.BooleanField(default=False)
    # Field name kept as `transaction_record` (not renamed) so presence.py's
    # logic is unchanged by the 2026-09-23 schema redesign -- only the FK
    # target moved from the removed TransactionRecord to TransactionFact.
    transaction_record = models.ForeignKey(
        TransactionFact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="register_presences",
        help_text=(
            "Which specific row satisfied presence, for traceability -- matters since "
            "e.g. the 'NPCI' leg aggregates 3 real RegisterTypes."
        ),
    )
    # Denormalized from the matched TransactionFact for convenience/reporting
    # (avoids a join for every report query).
    amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    direction = models.CharField(max_length=10, blank=True)
    response_code = models.CharField(
        max_length=10,
        blank=True,
        help_text=(
            "NPCI response code of the matched row ('00' = success), read from the "
            "source row's raw cells -- see reconciliation.presence."
            "NPCI_RESPONSE_CODE_CELL. Blank for legs that carry no response code (CBS)."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["match_group", "leg"], name="unique_presence_per_leg"),
        ]
        ordering = ["match_group", "leg"]

    def __str__(self) -> str:
        return f"{self.match_group_id}:{self.leg} present={self.present}"


# ---------------------------------------------------------------------------
# History tables (schema only -- schema redesign approved 2026-09-23, see
# tracker.md "Schema Redesign"). The archival job that populates these is
# Phase 3 work (trigger: reconciliation complete for a business date, keyed
# off TransactionMatchGroup.processing_status) and is NOT built here.
#
# Same conventions as transactions/models/history.py: live columns mirrored
# plus original_id/archived_at/archival_reason; live unique constraints NOT
# copied (the same logical row may be archived more than once); original
# created_at/updated_at preserved as plain DateTimeFields. References to
# tables that are themselves archivable (match group, fact) are stored as
# plain IDs rather than FKs, since the live row they point at is expected
# to be gone once archived.
# ---------------------------------------------------------------------------
class TransactionMatchGroupHistory(models.Model):
    original_id = models.BigIntegerField(db_index=True, help_text="PK of the live TransactionMatchGroup")
    archived_at = models.DateTimeField(default=timezone.now, db_index=True)
    archival_reason = models.TextField(blank=True)

    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="+")
    rrn = models.CharField(max_length=20)
    business_date = models.DateField()
    settlement_cycle = models.ForeignKey(
        SettlementCycle, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    amount_match = models.BooleanField(null=True, blank=True)
    status_match = models.BooleanField(null=True, blank=True)
    direction_match = models.BooleanField(null=True, blank=True)
    cbs_reversed = models.BooleanField(default=False)
    is_superseded = models.BooleanField(default=False)
    superseded_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=50, blank=True)
    processing_status = models.CharField(max_length=30, choices=ProcessingStatus.choices)
    created_at = models.DateTimeField(help_text="Copied from the live row")
    updated_at = models.DateTimeField(help_text="Copied from the live row")

    class Meta:
        indexes = [
            models.Index(fields=["payment_module", "rrn", "business_date"], name="mghist_module_rrn_date_idx"),
            models.Index(fields=["payment_module", "business_date"], name="mghist_module_date_idx"),
            models.Index(fields=["processing_status"], name="mghist_status_idx"),
        ]
        ordering = ["-archived_at", "original_id"]

    def __str__(self) -> str:
        return f"History:{self.rrn} ({self.business_date}) orig {self.original_id}"


class RegisterPresenceHistory(models.Model):
    original_id = models.BigIntegerField(db_index=True, help_text="PK of the live RegisterPresence")
    archived_at = models.DateTimeField(default=timezone.now, db_index=True)
    archival_reason = models.TextField(blank=True)

    match_group_id = models.BigIntegerField(
        db_index=True, help_text="Live TransactionMatchGroup PK (plain ID -- see TransactionMatchGroupHistory.original_id)"
    )
    leg = models.CharField(max_length=30)
    present = models.BooleanField(default=False)
    transaction_record_id = models.BigIntegerField(
        null=True, blank=True, help_text="Live TransactionFact PK (plain ID -- see TransactionFactHistory.original_id)"
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    direction = models.CharField(max_length=10, blank=True)
    response_code = models.CharField(max_length=10, blank=True)
    created_at = models.DateTimeField(help_text="Copied from the live row")
    updated_at = models.DateTimeField(help_text="Copied from the live row")

    class Meta:
        ordering = ["-archived_at", "original_id"]

    def __str__(self) -> str:
        return f"History:{self.match_group_id}:{self.leg} present={self.present} (orig {self.original_id})"
