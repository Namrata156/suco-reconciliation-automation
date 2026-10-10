"""
Case lifecycle (Phase 3 design section 2). Every user-driven change to a
case goes through one of these functions; each locks the case row
(select_for_update) inside a transaction, checks the user's role and the
current status, applies the change and writes a CaseEvent.

  OPEN --take--> IN_PROGRESS --submit--> PENDING_APPROVAL --approve--> APPROVED
                   ^   |                       |                          |
                   |   +--release--> OPEN      +--reject (reason)--+      +--mark_completed (ref)--> COMPLETED
                   +-------------------------------------------------+
  reassign (Administrator): OPEN / IN_PROGRESS / APPROVED -> assigned to another maker
  add_remark (Maker): any status, append-only
  CLOSED_SUPERSEDED is set only by the system (exception_management.sync).

Roles: Maker = take, release, submit, mark_completed, add_remark; Checker =
approve, reject; Administrator = reassign (and release a case someone else
holds). A user holding Maker and Checker may approve their own submission
(owner D3); the approval is then recorded with self_approved=True (P4).

Approval only RECORDS the decision (owner D2): ops still carries out the
action in the NPCI portal / CBS and then marks the case done with the
external reference.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from exception_management.actions import ACTION_SPECS, ActionType
from exception_management.models import (
    CaseEvent,
    CaseStatus,
    Decision,
    EventType,
    ExceptionCase,
    ProposedAction,
)
from exception_management.roles import Role, has_role
from rule_engine.models import ResponseCode


class WorkflowError(Exception):
    """Base class: the requested case change is not allowed."""


class RoleRequired(WorkflowError):
    pass


class InvalidTransition(WorkflowError):
    pass


class MissingInput(WorkflowError):
    pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _lock(case) -> ExceptionCase:
    pk = case.pk if isinstance(case, ExceptionCase) else case
    # of=("self",): lock the case row only, not the joined payment module row.
    return ExceptionCase.objects.select_for_update(of=("self",)).select_related("payment_module").get(pk=pk)


def _require(user, *roles: str) -> None:
    if not any(has_role(user, r) for r in roles):
        who = getattr(user, "username", None) or "anonymous"
        raise RoleRequired(f"{who} needs the {' or '.join(roles)} role for this")


def _require_status(case: ExceptionCase, *statuses: str, verb: str) -> None:
    if case.status not in statuses:
        raise InvalidTransition(
            f"Cannot {verb} case {case.pk} ({case.rrn}, {case.business_date}): it is {case.status}, "
            f"must be {' or '.join(statuses)}"
        )


def _require_assignee(case: ExceptionCase, user, verb: str) -> None:
    if case.assigned_to_id != user.pk:
        holder = case.assigned_to.get_username() if case.assigned_to_id else "nobody"
        raise InvalidTransition(f"Cannot {verb} case {case.pk}: it is assigned to {holder}")


def _text(value, what: str) -> str:
    value = (value or "").strip()
    if not value:
        raise MissingInput(f"{what} is required")
    return value


def record_event(case: ExceptionCase, event_type: str, *, actor=None, old_status="", new_status="",
                 action: ProposedAction | None = None, remarks="", changes=None, source="") -> CaseEvent:
    return CaseEvent.objects.create(**event_kwargs(
        case, event_type, actor=actor, old_status=old_status, new_status=new_status, action=action,
        remarks=remarks, changes=changes, source=source,
    ))


def event_kwargs(case, event_type, *, actor=None, old_status="", new_status="", action=None, remarks="",
                 changes=None, source="") -> dict:
    return dict(
        case=case,
        payment_module_code=case.payment_module.code,
        business_date=case.business_date,
        rrn=case.rrn,
        event_type=event_type,
        actor=actor,
        actor_username=actor.get_username() if actor is not None else "",
        source=source,
        old_status=old_status,
        new_status=new_status,
        action_snapshot=action.snapshot() if action is not None else None,
        remarks=remarks,
        changes=changes or {},
    )


def _set_status(case: ExceptionCase, new_status: str, **fields) -> str:
    old = case.status
    case.status = new_status
    for k, v in fields.items():
        setattr(case, k, v)
    case.save()
    return old


# ---------------------------------------------------------------------------
# transitions
# ---------------------------------------------------------------------------
@transaction.atomic
def take(case, user, *, remarks: str = "", source: str = "") -> ExceptionCase:
    case = _lock(case)
    _require(user, Role.MAKER)
    _require_status(case, CaseStatus.OPEN, verb="take")
    old = _set_status(case, CaseStatus.IN_PROGRESS, assigned_to=user)
    record_event(case, EventType.TAKEN, actor=user, old_status=old, new_status=case.status, remarks=remarks,
                 changes={"assigned_to": [None, user.get_username()]}, source=source)
    return case


@transaction.atomic
def release(case, user, *, remarks: str = "", source: str = "") -> ExceptionCase:
    """Puts an IN_PROGRESS case back on the shared queue. The maker holding
    it, or an Administrator."""
    case = _lock(case)
    _require(user, Role.MAKER, Role.ADMINISTRATOR)
    _require_status(case, CaseStatus.IN_PROGRESS, verb="release")
    if not has_role(user, Role.ADMINISTRATOR):
        _require_assignee(case, user, "release")
    previous = case.assigned_to.get_username() if case.assigned_to_id else None
    old = _set_status(case, CaseStatus.OPEN, assigned_to=None)
    record_event(case, EventType.RELEASED, actor=user, old_status=old, new_status=case.status, remarks=remarks,
                 changes={"assigned_to": [previous, None]}, source=source)
    return case


@transaction.atomic
def reassign(case, user, to_user, *, remarks: str = "", source: str = "") -> ExceptionCase:
    """Administrator only. The new holder must be a Maker. An OPEN case
    becomes IN_PROGRESS with them; a PENDING_APPROVAL case is with the
    checker and cannot be reassigned."""
    case = _lock(case)
    _require(user, Role.ADMINISTRATOR)
    _require_status(case, CaseStatus.OPEN, CaseStatus.IN_PROGRESS, CaseStatus.APPROVED, verb="reassign")
    if to_user is None or not has_role(to_user, Role.MAKER):
        raise RoleRequired("A case can only be assigned to an active user with the Maker role")
    if case.assigned_to_id == to_user.pk:
        raise InvalidTransition(f"Case {case.pk} is already assigned to {to_user.get_username()}")
    previous = case.assigned_to.get_username() if case.assigned_to_id else None
    new_status = CaseStatus.IN_PROGRESS if case.status == CaseStatus.OPEN else case.status
    old = _set_status(case, new_status, assigned_to=to_user)
    record_event(case, EventType.REASSIGNED, actor=user, old_status=old, new_status=case.status, remarks=remarks,
                 changes={"assigned_to": [previous, to_user.get_username()]}, source=source)
    return case


def _resolve_response_code(case: ExceptionCase, action_type: str, response_code) -> ResponseCode | None:
    spec = ACTION_SPECS[action_type]
    if response_code is not None and not isinstance(response_code, ResponseCode):
        response_code = ResponseCode.objects.filter(pk=response_code).first()
        if response_code is None:
            raise MissingInput("Unknown response code")
    if spec.code_flag is None:
        if response_code is not None:
            raise MissingInput(f"{action_type} is not sent with a response code")
        return None
    if response_code is None:
        if spec.fixed_code is not None:
            response_code = ResponseCode.objects.filter(
                payment_module_id=case.payment_module_id, flag=spec.code_flag, code=spec.fixed_code
            ).first()
            if response_code is None:
                raise MissingInput(f"No {spec.code_flag} {spec.fixed_code} in the response-code table")
        elif case.default_response_code_id and case.default_action_type == action_type:
            response_code = case.default_response_code
        else:
            raise MissingInput(f"{action_type} needs a {spec.code_flag} reason code")
    if response_code.payment_module_id != case.payment_module_id:
        raise MissingInput("The response code belongs to another payment module")
    if response_code.flag != spec.code_flag or (spec.fixed_code is not None and response_code.code != spec.fixed_code):
        raise MissingInput(
            f"{action_type} must be sent as {spec.code_flag} {spec.fixed_code or '<reason>'}, not {response_code}"
        )
    return response_code


@transaction.atomic
def submit(
    case,
    user,
    *,
    action_type: str | None = None,
    response_code=None,
    remarks: str = "",
    evidence_cbs_reversal_date: date | None = None,
    evidence_cbs_reversal_amount=None,
    evidence_cbs_reversal_rrn: str = "",
    evidence_notes: str = "",
    source: str = "",
) -> ProposedAction:
    """The maker proposes the action. action_type / response_code default to
    the case's pre-fill from the rule; a rule with no default (R07b/R07x,
    R07c, R13, ...) needs an explicit choice."""
    case = _lock(case)
    _require(user, Role.MAKER)
    _require_status(case, CaseStatus.IN_PROGRESS, verb="submit")
    _require_assignee(case, user, "submit")

    if not action_type:
        if not case.default_action_type:
            raise MissingInput(f"Rule {case.rule_code} has no default action; choose one")
        action_type = case.default_action_type
    if action_type not in ACTION_SPECS:
        raise MissingInput(f"Unknown action type {action_type!r}")
    if action_type == case.default_action_type and response_code is None:
        response_code = case.default_response_code
    rc = _resolve_response_code(case, action_type, response_code)
    spec = ACTION_SPECS[action_type]
    remarks = (remarks or "").strip()
    if spec.requires_remarks and not remarks:
        raise MissingInput(f"{action_type} needs remarks (the finding)")
    evidence_cbs_reversal_rrn = (evidence_cbs_reversal_rrn or "").strip()
    if evidence_cbs_reversal_amount is not None:
        try:
            evidence_cbs_reversal_amount = Decimal(str(evidence_cbs_reversal_amount))
        except InvalidOperation as exc:
            raise MissingInput("The CBS reversal amount is not a number") from exc
    if spec.requires_cbs_reversal_evidence:
        missing = [
            label
            for label, value in (
                ("date", evidence_cbs_reversal_date),
                ("amount", evidence_cbs_reversal_amount),
                ("RRN", evidence_cbs_reversal_rrn),
            )
            if not value
        ]
        if missing:
            raise MissingInput(
                f"{action_type} needs the CBS reversal already posted as evidence: {', '.join(missing)} missing"
            )
        if evidence_cbs_reversal_amount <= 0:
            raise MissingInput("The CBS reversal amount must be positive")

    action = ProposedAction.objects.create(
        case=case,
        rule_result_id=case.rule_result_id,
        rule_code=case.rule_code,
        action_type=action_type,
        response_code=rc,
        remarks=remarks,
        evidence_cbs_reversal_date=evidence_cbs_reversal_date,
        evidence_cbs_reversal_amount=evidence_cbs_reversal_amount,
        evidence_cbs_reversal_rrn=evidence_cbs_reversal_rrn,
        evidence_notes=(evidence_notes or "").strip(),
        submitted_by=user,
        submitted_at=timezone.now(),
    )
    old = _set_status(case, CaseStatus.PENDING_APPROVAL)
    record_event(case, EventType.SUBMITTED, actor=user, old_status=old, new_status=case.status, action=action,
                 remarks=remarks, source=source)
    return action


def _pending(case: ExceptionCase) -> ProposedAction:
    action = ProposedAction.objects.select_for_update(of=("self",)).filter(case=case, decision=Decision.PENDING).first()
    if action is None:  # cannot happen through this module
        raise InvalidTransition(f"Case {case.pk} has no pending action")
    return action


@transaction.atomic
def approve(case, user, *, remarks: str = "", source: str = "") -> ProposedAction:
    case = _lock(case)
    _require(user, Role.CHECKER)
    _require_status(case, CaseStatus.PENDING_APPROVAL, verb="approve")
    action = _pending(case)
    action.decision = Decision.APPROVED
    action.decided_by = user
    action.decided_at = timezone.now()
    action.decision_remarks = (remarks or "").strip()
    action.self_approved = action.submitted_by_id == user.pk
    action.save()
    old = _set_status(case, CaseStatus.APPROVED)
    record_event(case, EventType.APPROVED, actor=user, old_status=old, new_status=case.status, action=action,
                 remarks=action.decision_remarks, source=source)
    return action


@transaction.atomic
def reject(case, user, *, reason: str, source: str = "") -> ProposedAction:
    """Back to the maker (IN_PROGRESS, same assignee) with the reason."""
    case = _lock(case)
    _require(user, Role.CHECKER)
    _require_status(case, CaseStatus.PENDING_APPROVAL, verb="reject")
    reason = _text(reason, "A rejection reason")
    action = _pending(case)
    action.decision = Decision.REJECTED
    action.decided_by = user
    action.decided_at = timezone.now()
    action.decision_remarks = reason
    action.save()
    old = _set_status(case, CaseStatus.IN_PROGRESS)
    record_event(case, EventType.REJECTED, actor=user, old_status=old, new_status=case.status, action=action,
                 remarks=reason, source=source)
    return action


@transaction.atomic
def mark_completed(case, user, *, external_reference: str, remarks: str = "", source: str = "") -> ProposedAction:
    """The maker holding the case records the NPCI portal / CBS reference
    after carrying out the approved action."""
    case = _lock(case)
    _require(user, Role.MAKER)
    _require_status(case, CaseStatus.APPROVED, verb="mark done")
    _require_assignee(case, user, "mark done")
    external_reference = _text(external_reference, "The external (NPCI portal / CBS) reference")
    action = ProposedAction.objects.select_for_update(of=("self",)).filter(case=case, decision=Decision.APPROVED).order_by(
        "-decided_at"
    ).first()
    if action is None:  # cannot happen through this module
        raise InvalidTransition(f"Case {case.pk} has no approved action")
    action.external_reference = external_reference
    action.completed_by = user
    action.completed_at = timezone.now()
    action.save()
    old = _set_status(case, CaseStatus.COMPLETED)
    record_event(case, EventType.COMPLETED, actor=user, old_status=old, new_status=case.status, action=action,
                 remarks=(remarks or "").strip(), changes={"external_reference": [None, external_reference]},
                 source=source)
    return action


@transaction.atomic
def add_remark(case, user, text: str, *, source: str = "") -> CaseEvent:
    case = _lock(case)
    _require(user, Role.MAKER)
    text = _text(text, "Remark text")
    return record_event(case, EventType.REMARK, actor=user, old_status=case.status, new_status=case.status,
                        remarks=text, source=source)


__all__ = [
    "ActionType",
    "InvalidTransition",
    "MissingInput",
    "RoleRequired",
    "WorkflowError",
    "add_remark",
    "approve",
    "mark_completed",
    "reassign",
    "reject",
    "release",
    "submit",
    "take",
]
