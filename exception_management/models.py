"""
`exception_management` app: Exception Management + Maker-Checker Workflow,
Phase 3 Slice 1 (design docs/phase3_design_draft_v1.txt, approved by the
project owner 2026-10-10). Backend only: no admin screens, API or bulk
actions in this slice.

Named `exception_management` (not `exceptions`) so the top-level package
cannot be mistaken for, or shadow, anything called "exceptions"; it is also
the component's name in the spec and the tracker. One app holds both
components because the maker-checker steps are transitions of the same case.

  ExceptionCase  one per (payment module, business date, RRN) for every
                 current RuleResult with a due date (design section 1).
                 Created and re-linked by exception_management.sync; changed
                 by users only through exception_management.services.
  ProposedAction the maker's submission (action, code, remarks, evidence)
                 and the checker's decision on it. One row per submission,
                 so a rejected proposal and its re-submission both remain.
  CaseEvent      append-only audit of every change (design section 5).
                 Cannot be updated or deleted through the ORM (see
                 CaseEventQuerySet / CaseEvent.save / delete and
                 exception_management.signals). Raw SQL bypasses this, as
                 for the rule set.

on_delete / archival (design section 7, P6 -- archival is designed, not
built):
  - Every FK here is PROTECT. Nothing is ever cascaded away or silently
    nulled: deleting a match group, rule result, response code or user that
    a case, action or event refers to fails loudly (ProtectedError).
  - That fixes the order a future archive job must follow for a business
    date with no open cases: cases' events, actions and cases first (into
    history tables, as for staging), then rule results, then match groups.
    The same order already applies to RuleResult -> match group.
  - CaseEvent rows cannot be deleted through the ORM at all, so the archive
    job will need one explicit, reviewed bypass for moving events to a
    history table (not built). Events carry payment module / business date /
    RRN / actor username copies so an archived event stands on its own.
  - Users are never deleted, only deactivated (PROTECT from events,
    actions and assignments).
"""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q

from core.append_only import AppendOnlyError, AppendOnlyModel, AppendOnlyQuerySet
from core.models import PaymentModule
from exception_management.actions import ActionType
from reconciliation.models import TransactionMatchGroup
from rule_engine.models import Priority, ResponseCode, RuleResult, StatusCategory


class CaseImmutableError(AppendOnlyError):
    """A CaseEvent / code-change event (append-only audit) was about to be changed or deleted."""


class CaseStatus(models.TextChoices):
    OPEN = "OPEN", "Open"
    IN_PROGRESS = "IN_PROGRESS", "In progress"
    PENDING_APPROVAL = "PENDING_APPROVAL", "Pending approval"
    APPROVED = "APPROVED", "Approved"
    COMPLETED = "COMPLETED", "Completed"
    CLOSED_SUPERSEDED = "CLOSED_SUPERSEDED", "Closed (superseded by re-evaluation)"


# Statuses still in someone's work queue.
ACTIVE_STATUSES = (CaseStatus.OPEN, CaseStatus.IN_PROGRESS, CaseStatus.PENDING_APPROVAL, CaseStatus.APPROVED)
# Approved or done: a re-run never changes these silently (design section 6).
DECIDED_STATUSES = (CaseStatus.APPROVED, CaseStatus.COMPLETED)

_PRIORITY_RANK = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}

# R07x: chargeback more than 35 days after the original transaction -- "bring
# to the user's notice" (rule table v3.1, design section 3).
ATTENTION_RULE_CODES = ("R07x",)

# P5 hint (display only, no linking; v3.2 one-day rule): an R13 (CBS only) case
# whose RRN has a current R12 (NPCI only, successful) result on the previous
# business date -- the same payment recorded by NPCI on D-1 and CBS on D.
HINT_RULE_CODE, HINT_PREVIOUS_DAY_RULE_CODE = "R13", "R12"

QUEUE_ORDER = (
    "priority_rank",
    models.F("is_overdue").desc(nulls_last=True),
    models.F("due_date").asc(nulls_last=True),
    models.F("age_days").desc(nulls_last=True),
    "business_date",
    "rrn",
)


# ---------------------------------------------------------------------------
# ExceptionCase
# ---------------------------------------------------------------------------
class ExceptionCaseQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status__in=ACTIVE_STATUSES)

    def assigned_to(self, user):
        return self.filter(assigned_to=user)

    def awaiting_approval(self):
        return self.filter(status=CaseStatus.PENDING_APPROVAL)

    def with_ageing(self, today=None, now=None):
        """Annotates age_days, ageing_bucket and is_overdue from the linked
        rule result, via RuleResultQuerySet.with_ageing (the one place the
        working-day due date, the 16:00 RET cutoff and AgeingBucket are
        interpreted -- not re-implemented here)."""
        aged = RuleResult.objects.with_ageing(today=today, now=now).filter(pk=models.OuterRef("rule_result_id"))
        return self.annotate(
            age_days=models.Subquery(aged.values("age_days")[:1], output_field=models.IntegerField()),
            ageing_bucket=models.Subquery(aged.values("ageing_bucket")[:1], output_field=models.CharField()),
            is_overdue=models.Subquery(aged.values("is_overdue")[:1], output_field=models.BooleanField()),
        )

    def with_previous_day_r12(self):
        """Annotates previous_day_r12 (P5 hint): True on an R13 case whose RRN
        has a current R12 result on business_date - 1 day. Computed at read
        time, so it follows a later re-evaluation of D-1."""
        r12 = RuleResult.objects.filter(
            is_superseded=False,
            payment_module_id=models.OuterRef("payment_module_id"),
            rrn=models.OuterRef("rrn"),
            rule_code=HINT_PREVIOUS_DAY_RULE_CODE,
            business_date=models.ExpressionWrapper(
                models.OuterRef("business_date") - models.Value(timedelta(days=1)), output_field=models.DateField()
            ),
        )
        return self.annotate(
            previous_day_r12=models.Case(
                models.When(rule_code=HINT_RULE_CODE, then=models.Exists(r12)),
                default=models.Value(False),
                output_field=models.BooleanField(),
            )
        )

    def with_queue_annotations(self, today=None, now=None):
        """with_ageing + the P5 hint + priority_rank + needs_attention (R07x,
        overdue, or no deadline)."""
        rank = models.Case(
            *(models.When(priority=p, then=models.Value(r)) for p, r in _PRIORITY_RANK.items()),
            default=models.Value(len(_PRIORITY_RANK)),
            output_field=models.IntegerField(),
        )
        return (
            self.with_ageing(today=today, now=now)
            .with_previous_day_r12()
            .annotate(
                priority_rank=rank,
                needs_attention=models.ExpressionWrapper(
                    Q(rule_code__in=ATTENTION_RULE_CODES) | Q(is_overdue=True) | Q(no_deadline=True),
                    output_field=models.BooleanField(),
                ),
            )
        )

    def queue(self, today=None, now=None):
        """The shared work queue (design section 3): active cases, HIGH first,
        then overdue, then due date, then oldest."""
        return self.active().with_queue_annotations(today=today, now=now).order_by(*QUEUE_ORDER)


class CaseType(models.TextChoices):
    TRANSACTION = "TRANSACTION", "Transaction (one RRN, from a rule result)"
    # Phase 4 Slice 2 (design S5, Q2): one case per module + business date when
    # the settlement reconciliation is UNEXPLAINED. It has no RRN, match group
    # or rule result (rrn is ""); its detail is settlement.SettlementCase.
    SETTLEMENT = "SETTLEMENT", "Settlement (NTSL difference for a business date)"


class ExceptionCase(models.Model):
    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="exception_cases")
    business_date = models.DateField()
    case_type = models.CharField(max_length=12, choices=CaseType.choices, default=CaseType.TRANSACTION)
    rrn = models.CharField(max_length=20, help_text='Empty ("") for a settlement case')
    match_group = models.ForeignKey(
        TransactionMatchGroup, on_delete=models.PROTECT, related_name="exception_cases", null=True, blank=True,
        help_text="Always set on a transaction case; empty on a settlement case",
    )
    rule_result = models.ForeignKey(
        RuleResult,
        on_delete=models.PROTECT,
        related_name="exception_cases",
        null=True,
        blank=True,
        help_text="The key's current rule result (re-linked on every re-run); the last one seen if the "
        "key has no current result any more (result_missing). Empty on a settlement case.",
    )

    # Copied from rule_result for queue sorting / filtering (refreshed by sync).
    rule_code = models.CharField(max_length=10)
    status_category = models.CharField(max_length=40, choices=StatusCategory.choices)
    priority = models.CharField(max_length=10, choices=Priority.choices)
    due_date = models.DateField(null=True, blank=True)
    due_cutoff_time = models.TimeField(null=True, blank=True)

    default_action_type = models.CharField(
        max_length=30, choices=ActionType.choices, blank=True, help_text="Pre-filled from the rule; blank = maker chooses"
    )
    default_response_code = models.ForeignKey(
        ResponseCode, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    status = models.CharField(max_length=20, choices=CaseStatus.choices, default=CaseStatus.OPEN)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="assigned_cases"
    )

    needs_review = models.BooleanField(
        default=False, help_text="A re-run changed an APPROVED/COMPLETED case (design section 6); see the events"
    )
    needs_review_reason = models.TextField(blank=True)
    result_missing = models.BooleanField(
        default=False, help_text="The key has no current rule result (its match group was superseded)"
    )
    multiple_adjustment_rows = models.BooleanField(
        default=False, help_text="More than one Adjustment row belongs to this RRN and date (design section 7)"
    )
    no_deadline = models.BooleanField(
        default=False, help_text="Needs action but the rule's TAT was not found (tat_unresolved; Amendment 1 A4)"
    )
    rrc_already_sent_on = models.DateField(
        null=True,
        blank=True,
        help_text="Earliest Adjdate of a 'Refund Reversal Confirmation' row for this RRN on any loaded date "
        "(Amendment 1 A1: RRC already sent, check before crediting)",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ExceptionCaseQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["payment_module", "business_date", "rrn"], name="one_case_per_module_date_rrn"
            ),
            # A transaction case always has its RRN, group and result; a
            # settlement case never does (rrn "" also makes the unique
            # constraint above "one settlement case per module + date").
            models.CheckConstraint(
                condition=(
                    Q(case_type="TRANSACTION", match_group__isnull=False, rule_result__isnull=False) & ~Q(rrn="")
                ) | Q(case_type="SETTLEMENT", match_group__isnull=True, rule_result__isnull=True, rrn=""),
                name="case_type_matches_keys",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "priority", "due_date"], name="case_queue_idx"),
            models.Index(fields=["rrn"], name="case_rrn_idx"),
        ]
        ordering = ["business_date", "rrn"]
        permissions = [
            ("work_case", "Can take, release, submit and complete cases and add remarks (Maker)"),
            ("approve_case", "Can approve or reject proposed actions (Checker)"),
            ("reassign_case", "Can reassign cases (Administrator)"),
        ]

    def __str__(self) -> str:
        if self.is_settlement:
            return f"{self.payment_module.code}:SETTLEMENT ({self.business_date}) [{self.status}]"
        return f"{self.payment_module.code}:{self.rrn} ({self.business_date}) {self.rule_code} [{self.status}]"

    @property
    def is_settlement(self) -> bool:
        return self.case_type == CaseType.SETTLEMENT

    @property
    def label(self) -> str:
        """The RRN, or "Settlement" for a settlement case (messages, alerts)."""
        return "Settlement (NTSL)" if self.is_settlement else self.rrn

    @property
    def rrc_warning(self) -> str:
        if self.rrc_already_sent_on is None:
            return ""
        return f"RRC already sent for this RRN on {self.rrc_already_sent_on:%Y-%m-%d}; check before crediting"

    @property
    def pending_action(self):
        return self.actions.filter(decision=Decision.PENDING).first()

    @property
    def approved_action(self):
        return self.actions.filter(decision=Decision.APPROVED).order_by("-decided_at").first()


# ---------------------------------------------------------------------------
# ProposedAction
# ---------------------------------------------------------------------------
class Decision(models.TextChoices):
    PENDING = "PENDING", "Pending approval"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    WITHDRAWN = "WITHDRAWN", "Withdrawn by the system (the case changed under it)"


class ProposedAction(models.Model):
    case = models.ForeignKey(ExceptionCase, on_delete=models.PROTECT, related_name="actions")
    rule_result = models.ForeignKey(
        RuleResult, on_delete=models.PROTECT, related_name="+", null=True, blank=True,
        help_text="The case's rule result when submitted (empty for a settlement case)",
    )
    rule_code = models.CharField(max_length=10, blank=True, help_text="The rule the action was proposed under")

    action_type = models.CharField(max_length=30, choices=ActionType.choices)
    response_code = models.ForeignKey(ResponseCode, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    remarks = models.TextField(blank=True)
    # Evidence. The CBS reversal fields are mandatory for RRC 501 without a
    # new credit (design D4).
    evidence_cbs_reversal_date = models.DateField(null=True, blank=True)
    evidence_cbs_reversal_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    evidence_cbs_reversal_rrn = models.CharField(max_length=40, blank=True)
    evidence_notes = models.TextField(blank=True)

    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    submitted_at = models.DateTimeField()

    decision = models.CharField(max_length=10, choices=Decision.choices, default=Decision.PENDING)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_remarks = models.TextField(blank=True, help_text="Rejection reason (mandatory) or approval remarks")
    self_approved = models.BooleanField(
        default=False, help_text="Approved by the same user who submitted it (owner D3, flagged per P4)"
    )

    external_reference = models.CharField(
        max_length=100, blank=True, help_text="NPCI portal / CBS reference recorded when ops marked it done"
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["case"], condition=Q(decision="PENDING"), name="one_pending_action_per_case"
            ),
        ]
        ordering = ["case", "submitted_at"]

    def __str__(self) -> str:
        return f"{self.case_id}: {self.action_type} [{self.decision}]"

    def snapshot(self) -> dict:
        """JSON-safe copy for CaseEvent.action_snapshot."""
        rc = self.response_code
        return {
            "id": self.pk,
            "action_type": self.action_type,
            "response_code": None if rc is None else {"id": rc.pk, "flag": rc.flag, "code": rc.code, "description": rc.description},
            "rule_code": self.rule_code,
            "remarks": self.remarks,
            "evidence_cbs_reversal_date": self.evidence_cbs_reversal_date.isoformat() if self.evidence_cbs_reversal_date else None,
            "evidence_cbs_reversal_amount": None if self.evidence_cbs_reversal_amount is None else str(self.evidence_cbs_reversal_amount),
            "evidence_cbs_reversal_rrn": self.evidence_cbs_reversal_rrn,
            "evidence_notes": self.evidence_notes,
            "submitted_by": self.submitted_by.get_username() if self.submitted_by_id else None,
            "decision": self.decision,
            "decided_by": self.decided_by.get_username() if self.decided_by_id else None,
            "decision_remarks": self.decision_remarks,
            "self_approved": self.self_approved,
            "external_reference": self.external_reference,
        }


# ---------------------------------------------------------------------------
# CaseEvent -- append-only
# ---------------------------------------------------------------------------
class EventType(models.TextChoices):
    # system (sync)
    CREATED = "CREATED", "Case created"
    RESULT_UPDATED = "RESULT_UPDATED", "Rule result details changed (same rule)"
    RULE_CHANGED = "RULE_CHANGED", "Rule changed by re-evaluation"
    FLAGGED_FOR_REVIEW = "FLAGGED_FOR_REVIEW", "Flagged for review (changed after approval)"
    SUPERSEDED = "SUPERSEDED", "Closed: no longer needs action"
    REOPENED = "REOPENED", "Reopened: needs action again"
    ADJUSTMENT_ROWS_CHANGED = "ADJUSTMENT_ROWS_CHANGED", "Multiple-adjustment-rows flag changed"
    WARNINGS_CHANGED = "WARNINGS_CHANGED", "Warning changed (RRC already sent)"
    # users
    TAKEN = "TAKEN", "Taken"
    RELEASED = "RELEASED", "Released"
    REASSIGNED = "REASSIGNED", "Reassigned"
    SUBMITTED = "SUBMITTED", "Action submitted"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    COMPLETED = "COMPLETED", "Marked done"
    REMARK = "REMARK", "Remark"
    REVIEW_CLEARED = "REVIEW_CLEARED", "Needs-review flag cleared"


class CaseEventQuerySet(AppendOnlyQuerySet):
    """update / delete / upserts blocked (core.append_only)."""


class CaseEvent(AppendOnlyModel):
    append_only_error = CaseImmutableError

    case = models.ForeignKey(ExceptionCase, on_delete=models.PROTECT, related_name="events")
    # Copies so an event stands on its own (e.g. once archived).
    payment_module_code = models.CharField(max_length=20)
    business_date = models.DateField()
    rrn = models.CharField(max_length=20)

    event_type = models.CharField(max_length=30, choices=EventType.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        help_text="NULL = the system (case sync)",
    )
    actor_username = models.CharField(max_length=150, blank=True)
    source = models.CharField(max_length=150, blank=True, help_text="e.g. manage.py sync_cases, celery:evaluate_rules")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    old_status = models.CharField(max_length=20, choices=CaseStatus.choices, blank=True)
    new_status = models.CharField(max_length=20, choices=CaseStatus.choices, blank=True)
    action_snapshot = models.JSONField(null=True, blank=True, help_text="The proposed action as it stood")
    remarks = models.TextField(blank=True, help_text="Remark text, rejection reason, external reference note")
    changes = models.JSONField(default=dict, blank=True, help_text="{field: [old, new]} for system changes")

    objects = CaseEventQuerySet.as_manager()

    class Meta:
        ordering = ["case", "created_at", "id"]
        indexes = [models.Index(fields=["business_date", "event_type"], name="caseevent_date_type_idx")]

    def __str__(self) -> str:
        return f"{self.case_id} {self.event_type} @ {self.created_at:%Y-%m-%d %H:%M}"


# ---------------------------------------------------------------------------
# Response-code change requests (design P3, owner 2026-10-10)
# ---------------------------------------------------------------------------
class ChangeRequestStatus(models.TextChoices):
    PENDING = "PENDING", "Pending approval"
    APPROVED = "APPROVED", "Approved and applied"
    REJECTED = "REJECTED", "Rejected"


class ChangeRequestKind(models.TextChoices):
    EDIT = "EDIT", "Edit an existing code"
    CREATE = "CREATE", "Create a new code"  # Amendment 2 B2


class ResponseCodeChangeRequest(models.Model):
    """A maker's proposed edit of one rule_engine.ResponseCode (e.g. filling
    in the numeric code of a RET reason stored as unknown) or a proposed NEW
    code (kind CREATE, Amendment 2 B2: response_code is set when the approved
    code is created). Applied only when a checker approves
    (exception_management.code_changes); the code table refuses any other
    edit or creation (except seeding an approved rule-table release). old_*
    are the values when proposed (empty for CREATE). Changed only through
    code_changes; history is ResponseCodeChangeEvent."""

    kind = models.CharField(max_length=10, choices=ChangeRequestKind.choices, default=ChangeRequestKind.EDIT)
    response_code = models.ForeignKey(
        ResponseCode, on_delete=models.PROTECT, related_name="change_requests", null=True, blank=True,
        help_text="The code edited; for CREATE, the code created on approval",
    )
    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="+")
    flag = models.CharField(max_length=20)
    old_code = models.CharField(max_length=10, null=True, blank=True)
    old_description = models.CharField(max_length=255)
    old_notes = models.TextField(blank=True)
    new_code = models.CharField(max_length=10, null=True, blank=True)
    new_description = models.CharField(max_length=255)
    new_notes = models.TextField(blank=True)
    new_source = models.CharField(max_length=255, blank=True, help_text="CREATE only: the new code's citation")
    reason = models.TextField(help_text="Why, and the source of the new value (e.g. an NPCI circular)")

    status = models.CharField(max_length=10, choices=ChangeRequestStatus.choices, default=ChangeRequestStatus.PENDING)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField()
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_remarks = models.TextField(blank=True)
    self_approved = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["response_code"], condition=Q(status="PENDING"), name="one_pending_change_per_code"
            ),
            models.UniqueConstraint(
                fields=["payment_module", "flag", "new_description"],
                condition=Q(status="PENDING", kind="CREATE"),
                name="one_pending_create_per_description",
            ),
        ]
        ordering = ["-requested_at", "-id"]
        permissions = [
            ("propose_code_change", "Can propose response-code changes (Maker)"),
            ("approve_code_change", "Can approve or reject response-code changes (Checker)"),
        ]

    def __str__(self) -> str:
        target = self.response_code if self.kind == ChangeRequestKind.EDIT else f"NEW {self.flag}"
        return f"{target} -> {self.new_code or '(unknown)'} / {self.new_description} [{self.status}]"

    def changes(self) -> dict:
        if self.kind == ChangeRequestKind.CREATE:
            return {f: [None, getattr(self, f"new_{f}")] for f in ("code", "description", "notes", "source")}
        return {
            f: [getattr(self, f"old_{f}"), getattr(self, f"new_{f}")]
            for f in ("code", "description", "notes")
            if getattr(self, f"old_{f}") != getattr(self, f"new_{f}")
        }


class CodeChangeEventType(models.TextChoices):
    PROPOSED = "PROPOSED", "Proposed"
    APPROVED = "APPROVED", "Approved and applied"
    REJECTED = "REJECTED", "Rejected"


class ResponseCodeChangeEvent(AppendOnlyModel):
    """Append-only log of the code change requests (who, when, what)."""

    append_only_error = CaseImmutableError

    change_request = models.ForeignKey(ResponseCodeChangeRequest, on_delete=models.PROTECT, related_name="events")
    response_code = models.ForeignKey(
        ResponseCode, on_delete=models.PROTECT, related_name="+", null=True, blank=True,
        help_text="NULL for a CREATE proposal (the code does not exist yet)",
    )
    event_type = models.CharField(max_length=10, choices=CodeChangeEventType.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    actor_username = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)
    changes = models.JSONField(default=dict, blank=True, help_text="{field: [old, new]}")
    remarks = models.TextField(blank=True)
    self_approved = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.change_request_id} {self.event_type} by {self.actor_username}"


