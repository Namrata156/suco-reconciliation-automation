"""
The permissible difference for the settlement reconciliation (Phase 4
design Q4, owner 2026-10-11): Rs 0 by default, changed only by an
Administrator through maker-checker. Same pattern as the NTSL mapping
(settlement.mapping_changes) and the Phase 3 code table:

  - an Administrator proposes a NEW version (amount in Rs, transaction
    count) with a reason; it is stored PENDING and the active value is
    unchanged;
  - a Checker approves (the proposal becomes ACTIVE, the old one RETIRED)
    or rejects it (with a reason);
  - the same user may approve their own proposal only if they hold both
    roles; it is allowed and flagged (self_approved), as in Phase 3;
  - every step is logged in SettlementToleranceEvent (append-only).

Results already computed keep the tolerance they used; re-run
`reconcile_settlement` to apply a new one.

Version 1 (Rs 0.00 / 0 txn) is seeded as an approved release (logged
SEEDED, no maker or checker): by data migration settlement/0004 on an
existing database, and on first use (active_tolerance) on a fresh/test one.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from exception_management.roles import Role
from exception_management.services import InvalidTransition, MissingInput, _require, _text
from settlement.models import MappingEventType, MapStatus, SettlementTolerance, SettlementToleranceEvent

DEFAULT_AMOUNT = Decimal("0.00")
DEFAULT_COUNT = 0
SEEDED_BY = "seed"
SEED_REASON = "Phase 4 design Q4 (owner 2026-10-11): permissible difference Rs 0 by default"


def _log(tolerance, event_type, user, *, remarks="", changes=None, self_approved=False):
    return SettlementToleranceEvent.objects.create(
        tolerance=tolerance, event_type=event_type, actor=user,
        actor_username=user.get_username() if user else SEEDED_BY, remarks=remarks,
        changes=changes or {}, self_approved=self_approved,
    )


def seed_with(models: dict, payment_module) -> SettlementTolerance | None:
    """Creates v1 ACTIVE (Rs 0 / 0) if the module has no tolerance version yet.
    `models`: SettlementTolerance, SettlementToleranceEvent -- live or historical."""
    Tolerance, Event = models["SettlementTolerance"], models["SettlementToleranceEvent"]
    if Tolerance.objects.filter(payment_module=payment_module).exists():
        return None
    now = timezone.now()
    t = Tolerance(payment_module=payment_module, version=1, status="PENDING", amount=DEFAULT_AMOUNT,
                  txn_count=DEFAULT_COUNT, reason=SEED_REASON, proposed_by_username=SEEDED_BY, proposed_at=now)
    t.save()
    t.status, t.activated_at, t.decided_at, t.decided_by_username = "ACTIVE", now, now, SEEDED_BY
    t._workflow_change = True
    t.save()
    Event.objects.create(tolerance=t, event_type="SEEDED", actor_username=SEEDED_BY, remarks=SEED_REASON,
                         changes={"amount": str(DEFAULT_AMOUNT), "txn_count": DEFAULT_COUNT})
    return t


def active_tolerance(payment_module) -> SettlementTolerance:
    """The ACTIVE version; seeds v1 (Rs 0 / 0) the first time if none exists."""
    t = SettlementTolerance.objects.filter(payment_module=payment_module, status=MapStatus.ACTIVE).first()
    if t is not None:
        return t
    try:
        with transaction.atomic():
            seed_with({"SettlementTolerance": SettlementTolerance,
                       "SettlementToleranceEvent": SettlementToleranceEvent}, payment_module)
    except IntegrityError:  # seeded concurrently
        pass
    t = SettlementTolerance.objects.filter(payment_module=payment_module, status=MapStatus.ACTIVE).first()
    if t is None:
        raise InvalidTransition(f"{payment_module} has no ACTIVE settlement tolerance (a version exists but none "
                                "is active)")
    return t


def _amount(value) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise MissingInput(f"The tolerance amount {value!r} is not a number") from exc
    if amount < 0:
        raise MissingInput("The tolerance amount cannot be negative")
    return amount


def _count(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise MissingInput(f"The tolerance count {value!r} is not a whole number") from exc
    if n < 0 or str(value).strip() not in (str(n),):
        raise MissingInput("The tolerance count must be a whole number, 0 or more")
    return n


@transaction.atomic
def propose_tolerance_change(payment_module, user, *, amount, txn_count, reason: str) -> SettlementTolerance:
    """Administrator: a new PENDING version."""
    _require(user, Role.ADMINISTRATOR)
    reason = _text(reason, "A reason (and the source of the change)")
    amount, txn_count = _amount(amount), _count(txn_count)
    active = active_tolerance(payment_module)
    if SettlementTolerance.objects.filter(payment_module=payment_module, status=MapStatus.PENDING).exists():
        raise InvalidTransition(f"{payment_module} already has a settlement tolerance change pending approval")
    if amount == active.amount and txn_count == active.txn_count:
        raise MissingInput("The proposal changes nothing")
    last = SettlementTolerance.objects.filter(payment_module=payment_module).order_by("-version").first()
    t = SettlementTolerance.objects.create(
        payment_module=payment_module, version=last.version + 1, status=MapStatus.PENDING, amount=amount,
        txn_count=txn_count, based_on=active, reason=reason, proposed_by=user,
        proposed_by_username=user.get_username(), proposed_at=timezone.now(),
    )
    _log(t, MappingEventType.PROPOSED, user, remarks=reason, changes={
        "amount": [str(active.amount), str(amount)], "txn_count": [active.txn_count, txn_count],
    })
    return t


def _lock_pending(tolerance) -> SettlementTolerance:
    pk = tolerance.pk if isinstance(tolerance, SettlementTolerance) else tolerance
    t = SettlementTolerance.objects.select_for_update().get(pk=pk)
    if t.status != MapStatus.PENDING:
        raise InvalidTransition(f"Settlement tolerance v{t.version} is {t.status}, not pending")
    return t


@transaction.atomic
def approve_tolerance_change(tolerance, user, *, remarks: str = "") -> SettlementTolerance:
    """Checker. The proposer may approve their own change only if they also
    hold the Checker role (then flagged self_approved)."""
    _require(user, Role.CHECKER)
    t = _lock_pending(tolerance)
    active = SettlementTolerance.objects.select_for_update().filter(
        payment_module_id=t.payment_module_id, status=MapStatus.ACTIVE
    ).first()
    if active is None or active.pk != t.based_on_id:
        raise InvalidTransition("The active tolerance changed since this was proposed; reject it and propose again")
    now = timezone.now()
    active.status, active.retired_at = MapStatus.RETIRED, now
    active._workflow_change = True
    active.save()
    _log(active, MappingEventType.RETIRED, user, remarks=f"Replaced by v{t.version}")
    t.status, t.activated_at = MapStatus.ACTIVE, now
    t.decided_by, t.decided_by_username, t.decided_at = user, user.get_username(), now
    t.decision_remarks = (remarks or "").strip()
    t.self_approved = t.proposed_by_id == user.pk
    t._workflow_change = True
    t.save()
    _log(t, MappingEventType.APPROVED, user, remarks=t.decision_remarks, self_approved=t.self_approved)
    return t


@transaction.atomic
def reject_tolerance_change(tolerance, user, *, reason: str) -> SettlementTolerance:
    _require(user, Role.CHECKER)
    t = _lock_pending(tolerance)
    reason = _text(reason, "A rejection reason")
    t.status = MapStatus.REJECTED
    t.decided_by, t.decided_by_username, t.decided_at, t.decision_remarks = user, user.get_username(), timezone.now(), reason
    t._workflow_change = True
    t.save()
    _log(t, MappingEventType.REJECTED, user, remarks=reason)
    return t
