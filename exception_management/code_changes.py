"""
Response-code change requests (Phase 3 design P3, approved 2026-10-10):
response-code edits -- e.g. filling in the numeric code of the seven RET
reasons stored as unknown -- go through maker-checker. A Maker proposes, a
Checker approves (the change is then applied) or rejects. Every step is
logged append-only (ResponseCodeChangeEvent). Rule sets are unaffected:
they stay versioned releases.

Creating a NEW code goes the same way (Amendment 2 B2, 2026-10-11): a
"create" request, approved -> created.

rule_engine.ResponseCode refuses any edit or creation unless this module
applies it (ResponseCode._approved_change) -- the one exception is seeding
an approved rule-table release (rule_engine.seeding_response_codes). Its
existing immutability still holds: a code used by an ACTIVE/RETIRED rule set
may only have an unknown (NULL) numeric code filled in, once.

Self-approval (same user Maker + Checker, owner D3) is allowed and flagged
(P4), as for cases.
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.models import PaymentModule
from exception_management.models import (
    ChangeRequestKind,
    ChangeRequestStatus,
    CodeChangeEventType,
    ResponseCodeChangeEvent,
    ResponseCodeChangeRequest,
)
from exception_management.roles import Role
from exception_management.services import InvalidTransition, MissingInput, _require, _text
from rule_engine.models import CodeFlag, ResponseCode, RuleSetImmutableError


def _clean_code(value) -> str | None:
    value = (value or "").strip()
    return value or None


def _validate(rc: ResponseCode, new_code, new_description: str, new_notes: str) -> None:
    changes = {f for f, old, new in (("code", rc.code, new_code), ("description", rc.description, new_description),
                                     ("notes", rc.notes, new_notes)) if old != new}
    if not changes:
        raise MissingInput("The proposal changes nothing")
    if new_code is not None and len(new_code) > 10:
        raise MissingInput("A code is at most 10 characters")
    if not new_description:
        raise MissingInput("The description is required")
    if rc.is_locked():
        if changes != {"code"} or rc.code is not None or new_code is None:
            raise MissingInput(
                f"{rc} is used by an ACTIVE/RETIRED rule set: only its unknown numeric code may be filled in"
            )
    others = ResponseCode.objects.filter(payment_module_id=rc.payment_module_id, flag=rc.flag).exclude(pk=rc.pk)
    if new_code is not None and others.filter(code=new_code).exists():
        raise MissingInput(f"{rc.flag} {new_code} is already used by another code")
    if others.filter(description=new_description).exists():
        raise MissingInput(f"{rc.flag} '{new_description}' already exists")


def _validate_new(payment_module_id, flag, new_code, new_description, new_source) -> None:
    if flag not in CodeFlag.values:
        raise MissingInput(f"Unknown flag {flag!r}; one of {', '.join(CodeFlag.values)}")
    if not new_description:
        raise MissingInput("The description is required")
    if not new_source:
        raise MissingInput("The source (citation) of the new code is required")
    if new_code is not None and len(new_code) > 10:
        raise MissingInput("A code is at most 10 characters")
    same_flag = ResponseCode.objects.filter(payment_module_id=payment_module_id, flag=flag)
    if new_code is not None and same_flag.filter(code=new_code).exists():
        raise MissingInput(f"{flag} {new_code} already exists")
    if same_flag.filter(description=new_description).exists():
        raise MissingInput(f"{flag} '{new_description}' already exists")


def _log(req, event_type, user, *, remarks="", self_approved=False):
    return ResponseCodeChangeEvent.objects.create(
        change_request=req, response_code_id=req.response_code_id, event_type=event_type, actor=user,
        actor_username=user.get_username(), changes=req.changes(), remarks=remarks, self_approved=self_approved,
    )


@transaction.atomic
def propose_code_change(response_code, user, *, new_code=None, new_description=None, new_notes=None,
                        reason: str) -> ResponseCodeChangeRequest:
    """Fields left as None keep their current value."""
    _require(user, Role.MAKER)
    rc = ResponseCode.objects.select_for_update().get(
        pk=response_code.pk if isinstance(response_code, ResponseCode) else response_code
    )
    reason = _text(reason, "A reason (and the source of the new value)")
    new_code = rc.code if new_code is None else _clean_code(new_code)
    new_description = rc.description if new_description is None else (new_description or "").strip()
    new_notes = rc.notes if new_notes is None else (new_notes or "").strip()
    if ResponseCodeChangeRequest.objects.filter(response_code=rc, status=ChangeRequestStatus.PENDING).exists():
        raise InvalidTransition(f"{rc} already has a change request pending approval")
    _validate(rc, new_code, new_description, new_notes)
    req = ResponseCodeChangeRequest.objects.create(
        kind=ChangeRequestKind.EDIT, payment_module_id=rc.payment_module_id, flag=rc.flag,
        response_code=rc, old_code=rc.code, old_description=rc.description, old_notes=rc.notes,
        new_code=new_code, new_description=new_description, new_notes=new_notes, reason=reason,
        requested_by=user, requested_at=timezone.now(),
    )
    _log(req, CodeChangeEventType.PROPOSED, user, remarks=reason)
    return req


@transaction.atomic
def propose_code_creation(payment_module, user, *, flag: str, new_description: str, new_source: str,
                          new_code=None, new_notes: str = "", reason: str) -> ResponseCodeChangeRequest:
    """Amendment 2 B2: a maker proposes a NEW response code."""
    _require(user, Role.MAKER)
    module = payment_module if isinstance(payment_module, PaymentModule) else PaymentModule.objects.get(pk=payment_module)
    reason = _text(reason, "A reason (and the source of the new value)")
    new_code = _clean_code(new_code)
    new_description = (new_description or "").strip()
    new_source = (new_source or "").strip()
    new_notes = (new_notes or "").strip()
    _validate_new(module.pk, flag, new_code, new_description, new_source)
    pending = ResponseCodeChangeRequest.objects.filter(
        kind=ChangeRequestKind.CREATE, status=ChangeRequestStatus.PENDING, payment_module=module, flag=flag
    )
    if pending.filter(new_description=new_description).exists() or (
        new_code is not None and pending.filter(new_code=new_code).exists()
    ):
        raise InvalidTransition(f"A request to create {flag} '{new_description}' / {new_code} is already pending")
    req = ResponseCodeChangeRequest.objects.create(
        kind=ChangeRequestKind.CREATE, payment_module=module, flag=flag, old_description="",
        new_code=new_code, new_description=new_description, new_notes=new_notes, new_source=new_source,
        reason=reason, requested_by=user, requested_at=timezone.now(),
    )
    _log(req, CodeChangeEventType.PROPOSED, user, remarks=reason)
    return req


def _lock_pending(request) -> ResponseCodeChangeRequest:
    pk = request.pk if isinstance(request, ResponseCodeChangeRequest) else request
    req = ResponseCodeChangeRequest.objects.select_for_update().get(pk=pk)
    if req.status != ChangeRequestStatus.PENDING:
        raise InvalidTransition(f"Change request {req.pk} is {req.status}, not pending")
    return req


@transaction.atomic
def approve_code_change(request, user, *, remarks: str = "") -> ResponseCodeChangeRequest:
    _require(user, Role.CHECKER)
    req = _lock_pending(request)
    if req.kind == ChangeRequestKind.CREATE:
        _validate_new(req.payment_module_id, req.flag, req.new_code, req.new_description, req.new_source)
        rc = ResponseCode(payment_module_id=req.payment_module_id, flag=req.flag, code=req.new_code,
                          description=req.new_description, notes=req.new_notes, source=req.new_source)
    else:
        rc = ResponseCode.objects.select_for_update().get(pk=req.response_code_id)
        if (rc.code, rc.description, rc.notes) != (req.old_code, req.old_description, req.old_notes):
            raise InvalidTransition("The code changed since this was proposed; reject it and propose again")
        _validate(rc, req.new_code, req.new_description, req.new_notes)
        rc.code, rc.description, rc.notes = req.new_code, req.new_description, req.new_notes
    rc._approved_change = True
    try:
        rc.save()
    except RuleSetImmutableError as exc:  # the code table's own guard (should not happen after _validate)
        raise InvalidTransition(str(exc)) from exc
    req.response_code = rc  # CREATE: the new code
    req.status = ChangeRequestStatus.APPROVED
    req.decided_by, req.decided_at = user, timezone.now()
    req.decision_remarks = (remarks or "").strip()
    req.self_approved = req.requested_by_id == user.pk
    req.save()
    _log(req, CodeChangeEventType.APPROVED, user, remarks=req.decision_remarks, self_approved=req.self_approved)
    return req


@transaction.atomic
def reject_code_change(request, user, *, reason: str) -> ResponseCodeChangeRequest:
    _require(user, Role.CHECKER)
    req = _lock_pending(request)
    reason = _text(reason, "A rejection reason")
    req.status = ChangeRequestStatus.REJECTED
    req.decided_by, req.decided_at, req.decision_remarks = user, timezone.now(), reason
    req.save()
    _log(req, CodeChangeEventType.REJECTED, user, remarks=reason)
    return req
