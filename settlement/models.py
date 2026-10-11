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
