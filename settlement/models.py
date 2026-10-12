"""
`settlement` app: Phase 4 Settlement Control / NTSL Validation
(docs/phase4_design_draft_v1.txt, approved by the project owner 2026-10-11).

Slice 1 builds:
  S1  NtslCategoryMapVersion / NtslCategoryMapEntry -- which NTSL description
      belongs to which category, held as DATA, versioned, and changed only
      through maker-checker (settlement.mapping_changes). NtslMappingEvent is
      the append-only log of every step.
  S2  NtslCycleCheck -- NTSL's own arithmetic per business date and cycle:
      categorised lines -> Sub Totals -> Settlement Amount -> Final.

Slice 2 (gap rules G01-G10 / Z, approved 2026-10-11) adds:
  SettlementTolerance / SettlementToleranceEvent -- the permissible
      difference (Q4), versioned, changed by an Administrator through
      maker-checker (settlement.tolerance).
  SettlementResult / SettlementCycleResult -- the reconciliation per
      business date with cycle x side detail and the full snapshot
      (settlement.reconcile).
  SettlementCase -- links a Phase 3 ExceptionCase of type SETTLEMENT to the
      result it is about (settlement.cases).

Why a new app: Settlement Control is its own tracked component and Phase
4 adds more to it (reconciliation results, settlement cases). The NTSL lines
themselves stay where Phase 1 put them (transactions.NtslSettlementLine);
this app only reads them.

Guards (same spirit as rule_engine's rule-set immutability and
core.append_only):
  - A version is created PENDING. Only settlement.mapping_changes (and the
    seed) may change its status; nothing else about it ever changes, and a
    version is never deleted.
  - Entries are insert-only, and only into a PENDING version. An ACTIVE or
    RETIRED mapping can therefore never change under a check result that
    points at it.
Raw SQL bypasses all of this, as everywhere else in the project.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from core.append_only import AppendOnlyError, AppendOnlyModel, AppendOnlyQuerySet
from core.models import PaymentModule, SettlementCycle
from settlement.categories import NtslCategory


class MappingImmutableError(AppendOnlyError):
    """An NTSL category mapping version or entry was about to change outside the workflow."""


class MapStatus(models.TextChoices):
    PENDING = "PENDING", "Pending approval"
    ACTIVE = "ACTIVE", "Active"
    RETIRED = "RETIRED", "Retired"
    REJECTED = "REJECTED", "Rejected"


class MatchType(models.TextChoices):
    EXACT = "EXACT", "Exact description"
    REGEX = "REGEX", "Regular expression (full match)"


class TxnType(models.TextChoices):
    NONE = "", "-"
    U2 = "U2", "U2"
    U3 = "U3", "U3"


class NtslCategoryMapVersionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise MappingImmutableError("NTSL mapping versions change only through settlement.mapping_changes")

    def delete(self):
        raise MappingImmutableError("NTSL mapping versions are never deleted")


class NtslCategoryMapVersion(models.Model):
    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="ntsl_map_versions")
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=MapStatus.choices, default=MapStatus.PENDING)
    based_on = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        help_text="The ACTIVE version this proposal was built from (approval refuses if it is no longer active)",
    )
    reason = models.TextField(help_text="Why the change is needed, and its source")
    change_summary = models.JSONField(
        default=dict, blank=True,
        help_text="Entries added/removed and seen descriptions whose category changes, for the checker",
    )
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        help_text="Empty for the seeded version",
    )
    proposed_by_username = models.CharField(max_length=150, blank=True)
    proposed_at = models.DateTimeField()
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    decided_by_username = models.CharField(max_length=150, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_remarks = models.TextField(blank=True)
    self_approved = models.BooleanField(
        default=False, help_text="Maker and checker were the same user (allowed, flagged -- as Phase 3)"
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    objects = NtslCategoryMapVersionQuerySet.as_manager()

    _workflow_change = False  # set only by settlement.mapping_changes / seeding

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["payment_module", "version"], name="unique_ntsl_map_version"),
            models.UniqueConstraint(
                fields=["payment_module"], condition=Q(status="ACTIVE"), name="one_active_ntsl_map_per_module"
            ),
            models.UniqueConstraint(
                fields=["payment_module"], condition=Q(status="PENDING"), name="one_pending_ntsl_map_per_module"
            ),
        ]
        ordering = ["payment_module", "-version"]

    def __str__(self) -> str:
        return f"NTSL map {self.payment_module_id} v{self.version} [{self.status}]"

    def save(self, *args, **kwargs):
        try:
            if self._state.adding:
                if self.status != MapStatus.PENDING:
                    raise MappingImmutableError("A new NTSL mapping version must start PENDING")
            elif not self._workflow_change:
                raise MappingImmutableError("NTSL mapping versions change only through settlement.mapping_changes")
            super().save(*args, **kwargs)
        finally:
            self._workflow_change = False

    def delete(self, *args, **kwargs):
        raise MappingImmutableError("NTSL mapping versions are never deleted")


class NtslCategoryMapEntryQuerySet(AppendOnlyQuerySet):
    def bulk_create(self, objs, *args, **kwargs):
        objs = list(objs)
        _assert_pending({o.version_id for o in objs})
        return super().bulk_create(objs, *args, **kwargs)


def _assert_pending(version_ids) -> None:
    bad = NtslCategoryMapVersion.objects.filter(pk__in=version_ids).exclude(status=MapStatus.PENDING)
    if bad.exists():
        raise MappingImmutableError("Entries can only be added to a PENDING mapping version")


class NtslCategoryMapEntry(AppendOnlyModel):
    append_only_error = MappingImmutableError

    version = models.ForeignKey(NtslCategoryMapVersion, on_delete=models.PROTECT, related_name="entries")
    match_type = models.CharField(max_length=10, choices=MatchType.choices)
    pattern = models.TextField(help_text="The exact description, or a regex matched against the whole description")
    category = models.CharField(max_length=30, choices=NtslCategory.choices)
    txn_type = models.CharField(max_length=2, choices=TxnType.choices, blank=True, default="")
    priority = models.PositiveIntegerField(default=100, help_text="REGEX only: lower is tried first")
    notes = models.TextField(blank=True)

    objects = NtslCategoryMapEntryQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["version", "match_type", "pattern"], name="unique_ntsl_map_entry"),
        ]
        ordering = ["version", "match_type", "priority", "id"]

    def __str__(self) -> str:
        return f"{self.match_type} {self.pattern!r} -> {self.category}"

    def save(self, *args, **kwargs):
        if self._state.adding:
            _assert_pending([self.version_id])
        super().save(*args, **kwargs)

    def as_dict(self) -> dict:
        return {
            "match_type": self.match_type, "pattern": self.pattern, "category": self.category,
            "txn_type": self.txn_type, "priority": self.priority, "notes": self.notes,
        }


class MappingEventType(models.TextChoices):
    SEEDED = "SEEDED", "Seeded (approved release)"
    PROPOSED = "PROPOSED", "Proposed"
    APPROVED = "APPROVED", "Approved and activated"
    REJECTED = "REJECTED", "Rejected"
    RETIRED = "RETIRED", "Retired (replaced)"


class NtslMappingEvent(AppendOnlyModel):
    append_only_error = MappingImmutableError

    version = models.ForeignKey(NtslCategoryMapVersion, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(max_length=10, choices=MappingEventType.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    actor_username = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    remarks = models.TextField(blank=True)
    changes = models.JSONField(default=dict, blank=True)
    self_approved = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.version} {self.event_type} by {self.actor_username or 'system'}"


# ---------------------------------------------------------------------------
# S2 self-check results
# ---------------------------------------------------------------------------
class CheckStatus(models.TextChoices):
    PASS = "PASS", "Pass"
    FAIL = "FAIL", "Fail"
    MISSING = "MISSING", "No NTSL lines loaded for this cycle"


class NtslCycleCheck(models.Model):
    """One result per (module, business date, cycle) and run. A re-run marks
    the previous result superseded; results are never deleted (audit)."""

    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="ntsl_cycle_checks")
    business_date = models.DateField()
    settlement_cycle = models.ForeignKey(SettlementCycle, on_delete=models.PROTECT, related_name="ntsl_cycle_checks")
    map_version = models.ForeignKey(NtslCategoryMapVersion, on_delete=models.PROTECT, related_name="cycle_checks")
    status = models.CharField(max_length=10, choices=CheckStatus.choices)
    failures = models.JSONField(default=list, blank=True, help_text="Plain-language reasons, empty on PASS")

    line_count = models.PositiveIntegerField(default=0)
    unmapped_count = models.PositiveIntegerField(default=0)
    unmapped_lines = models.JSONField(
        default=list, blank=True, help_text="[{description, row_reference, txn_count, debit, credit}]"
    )
    category_totals = models.JSONField(
        default=dict, blank=True, help_text="{category: {lines, txn_count, debit, credit}} (amounts as strings)"
    )

    ntsl_subtotal_debit = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    ntsl_subtotal_credit = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    computed_subtotal_debit = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    computed_subtotal_credit = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    ntsl_settlement_net = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True, help_text="Credit - debit; negative = SSK pays"
    )
    computed_settlement_net = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    ntsl_final_net = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    computed_final_net = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    checked_at = models.DateTimeField()
    source = models.CharField(max_length=150, blank=True, help_text="e.g. manage.py check_ntsl")
    is_current = models.BooleanField(default=True)
    superseded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["payment_module", "business_date", "settlement_cycle"],
                condition=Q(is_current=True),
                name="one_current_ntsl_check_per_cycle",
            ),
        ]
        indexes = [models.Index(fields=["payment_module", "business_date", "is_current"], name="ntslcheck_date_idx")]
        ordering = ["business_date", "settlement_cycle_id", "-checked_at"]

    def __str__(self) -> str:
        return f"NTSL check {self.business_date} {self.settlement_cycle_id} {self.status}"

    # differences, derived (None when either side is missing)
    @staticmethod
    def _diff(a, b):
        return None if a is None or b is None else a - b

    @property
    def subtotal_debit_diff(self):
        return self._diff(self.computed_subtotal_debit, self.ntsl_subtotal_debit)

    @property
    def subtotal_credit_diff(self):
        return self._diff(self.computed_subtotal_credit, self.ntsl_subtotal_credit)

    @property
    def settlement_diff(self):
        return self._diff(self.computed_settlement_net, self.ntsl_settlement_net)

    @property
    def final_diff(self):
        return self._diff(self.computed_final_net, self.ntsl_final_net)


# ---------------------------------------------------------------------------
# Slice 2: permissible difference (Q4) -- versioned, maker-checker
# ---------------------------------------------------------------------------
class ToleranceImmutableError(AppendOnlyError):
    """A settlement tolerance version or its log was about to change outside the workflow."""


class SettlementToleranceQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ToleranceImmutableError("Settlement tolerances change only through settlement.tolerance")

    def delete(self):
        raise ToleranceImmutableError("Settlement tolerance versions are never deleted")


class SettlementTolerance(models.Model):
    """The permissible difference (Q4, owner 2026-10-11): Rs 0 by default,
    changed only by an Administrator through maker-checker
    (settlement.tolerance). Same lifecycle and guards as
    NtslCategoryMapVersion: created PENDING, then ACTIVE (the old one
    RETIRED) or REJECTED; values never change, never deleted. A result
    records the version it used."""

    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="settlement_tolerances")
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=MapStatus.choices, default=MapStatus.PENDING)
    amount = models.DecimalField(
        max_digits=18, decimal_places=2,
        help_text="Rs: an unexplained amount up to this (either sign) still counts as tallied",
    )
    txn_count = models.PositiveIntegerField(
        default=0, help_text="Transactions: an unexplained count up to this (either sign) still counts as tallied",
    )
    based_on = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    reason = models.TextField()
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        help_text="Empty for the seeded version",
    )
    proposed_by_username = models.CharField(max_length=150, blank=True)
    proposed_at = models.DateTimeField()
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    decided_by_username = models.CharField(max_length=150, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_remarks = models.TextField(blank=True)
    self_approved = models.BooleanField(default=False, help_text="Proposer and approver were the same user (flagged)")
    activated_at = models.DateTimeField(null=True, blank=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    objects = SettlementToleranceQuerySet.as_manager()

    _workflow_change = False  # set only by settlement.tolerance / seeding

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["payment_module", "version"], name="unique_settlement_tolerance_version"),
            models.UniqueConstraint(
                fields=["payment_module"], condition=Q(status="ACTIVE"), name="one_active_tolerance_per_module"
            ),
            models.UniqueConstraint(
                fields=["payment_module"], condition=Q(status="PENDING"), name="one_pending_tolerance_per_module"
            ),
            models.CheckConstraint(condition=Q(amount__gte=0), name="settlement_tolerance_amount_not_negative"),
        ]
        ordering = ["payment_module", "-version"]
        permissions = [
            ("propose_tolerance_change", "Can propose a settlement tolerance change (Administrator)"),
            ("approve_tolerance_change", "Can approve or reject a settlement tolerance change (Checker)"),
        ]

    def __str__(self) -> str:
        return f"Tolerance {self.payment_module_id} v{self.version}: Rs {self.amount} / {self.txn_count} txn [{self.status}]"

    def save(self, *args, **kwargs):
        try:
            if self._state.adding:
                if self.status != MapStatus.PENDING:
                    raise ToleranceImmutableError("A new settlement tolerance version must start PENDING")
            elif not self._workflow_change:
                raise ToleranceImmutableError("Settlement tolerances change only through settlement.tolerance")
            super().save(*args, **kwargs)
        finally:
            self._workflow_change = False

    def delete(self, *args, **kwargs):
        raise ToleranceImmutableError("Settlement tolerance versions are never deleted")


class SettlementToleranceEvent(AppendOnlyModel):
    append_only_error = ToleranceImmutableError

    tolerance = models.ForeignKey(SettlementTolerance, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(max_length=10, choices=MappingEventType.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    actor_username = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    remarks = models.TextField(blank=True)
    changes = models.JSONField(default=dict, blank=True)
    self_approved = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.tolerance} {self.event_type} by {self.actor_username or 'system'}"


# ---------------------------------------------------------------------------
# Slice 2: settlement reconciliation result (S3/S4)
# ---------------------------------------------------------------------------
class ResultStatus(models.TextChoices):
    TALLIED = "TALLIED", "Tallied"
    UNEXPLAINED = "UNEXPLAINED", "Unexplained difference"
    NOT_READY = "NOT_READY", "Not ready (self-check or rule results missing)"


class SettlementResult(models.Model):
    """One result per (module, business date) and run (settlement.reconcile).
    A re-run marks the previous result superseded; results are never
    deleted (audit, same pattern as NtslCycleCheck / RuleResult). Changed
    after creation only by the supersede step."""

    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="settlement_results")
    business_date = models.DateField()
    status = models.CharField(max_length=12, choices=ResultStatus.choices)
    reasons = models.JSONField(default=list, blank=True, help_text="Why NOT_READY / what is unexplained (plain text)")

    map_version = models.ForeignKey(
        NtslCategoryMapVersion, on_delete=models.PROTECT, null=True, blank=True, related_name="settlement_results"
    )
    tolerance = models.ForeignKey(
        SettlementTolerance, on_delete=models.PROTECT, null=True, blank=True, related_name="results",
        help_text="The tolerance version applied",
    )
    tolerance_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    tolerance_count = models.PositiveIntegerField(default=0)
    rule_run = models.ForeignKey(
        "rule_engine.RuleEvaluationRun", on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        help_text="The date's latest rule evaluation run when computed",
    )

    # CBS-side SOP check (C1): (CBS deposits - withdrawals) - (NTSL TXN debit - credit) = explained items.
    sop_cbs_net = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    sop_ntsl_net = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    sop_difference = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    sop_explained = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    sop_unexplained = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    unexplained_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=0,
        help_text="Sum of |unexplained amount| over every cycle/side row (0 when tallied exactly)",
    )
    unexplained_count = models.IntegerField(default=0, help_text="Sum of |unexplained count| over every cycle/side row")
    fees_net = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="G10: fee / GST / rebate / mandate-fee lines, credit - debit, all cycles (NTSL-internal)",
    )
    snapshot = models.JSONField(default=dict, blank=True, help_text="Full calculation incl. contributing transactions")

    computed_at = models.DateTimeField()
    source = models.CharField(max_length=150, blank=True)
    is_current = models.BooleanField(default=True)
    superseded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["payment_module", "business_date"], condition=Q(is_current=True),
                name="one_current_settlement_result_per_date",
            ),
        ]
        indexes = [models.Index(fields=["payment_module", "business_date", "is_current"], name="settleresult_date_idx")]
        ordering = ["-business_date", "-computed_at"]

    def __str__(self) -> str:
        return f"Settlement {self.payment_module_id} {self.business_date} {self.status}"


class Side(models.TextChoices):
    REMITTER = "REMITTER", "Remitter (SSK pays)"
    BENEFICIARY = "BENEFICIARY", "Beneficiary (SSK receives)"
    ADJUSTMENT = "ADJUSTMENT", "Net adjusted amount (G09)"
    UNCOVERED = "UNCOVERED", "NTSL lines no G rule covers"


class SettlementCycleResultQuerySet(AppendOnlyQuerySet):
    pass


class SettlementCycleResult(AppendOnlyModel):
    """One comparison of a result: cycle x side. Insert-only.

    Amounts are positive in the side's own direction (remitter: what SSK
    pays, beneficiary: what SSK receives); ADJUSTMENT / UNCOVERED rows are
    nets, credit - debit (+ = SSK receives).
      unexplained = ntsl - matched (G01-G03) - explained (G04-G06 / G09)
    ntsl_count is NULL where NTSL gives no count (Net Adjusted Amount); the
    count is then not compared."""

    append_only_error = ToleranceImmutableError

    result = models.ForeignKey(SettlementResult, on_delete=models.PROTECT, related_name="cycle_rows")
    settlement_cycle = models.ForeignKey(
        SettlementCycle, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        help_text="NULL: NPCI rows with no settlement cycle",
    )
    side = models.CharField(max_length=12, choices=Side.choices)
    ntsl_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    ntsl_count = models.IntegerField(null=True, blank=True)
    matched_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    matched_count = models.IntegerField(default=0)
    explained_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    explained_count = models.IntegerField(default=0)
    unexplained_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    unexplained_count = models.IntegerField(default=0)
    within_tolerance = models.BooleanField()
    detail = models.JSONField(default=dict, blank=True, help_text="{G rule: {count, amount}} and notes")

    objects = SettlementCycleResultQuerySet.as_manager()

    class Meta:
        ordering = ["result", "settlement_cycle_id", "side"]

    def __str__(self) -> str:
        code = self.settlement_cycle.cycle_code if self.settlement_cycle_id else "-"
        return f"{self.result_id} {code} {self.side}: unexplained {self.unexplained_amount} / {self.unexplained_count}"


# ---------------------------------------------------------------------------
# Slice 2: settlement case (S5) -- the link from a Phase 3 case to its result
# ---------------------------------------------------------------------------
class SettlementCase(models.Model):
    """The settlement-specific half of an exception_management.ExceptionCase
    of case type SETTLEMENT (one per module + business date, Q2). The case
    itself lives in the Phase 3 queue and goes through its maker-checker and
    audit; this row points it at the settlement result (and so the snapshot
    and cycle detail) it is about. Re-linked by settlement.cases on every
    re-run; changed only there."""

    case = models.OneToOneField(
        "exception_management.ExceptionCase", on_delete=models.PROTECT, primary_key=True,
        related_name="settlement_case",
    )
    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="+")
    business_date = models.DateField()
    result = models.ForeignKey(
        SettlementResult, on_delete=models.PROTECT, related_name="cases",
        help_text="The latest result the case was synced with (UNEXPLAINED when raised)",
    )
    first_result = models.ForeignKey(SettlementResult, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["payment_module", "business_date"], name="one_settlement_case_per_date"),
        ]

    def __str__(self) -> str:
        return f"Settlement case {self.payment_module_id} {self.business_date}"
