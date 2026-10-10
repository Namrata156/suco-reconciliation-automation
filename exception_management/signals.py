"""CaseEvent deletion guard for the paths that never call CaseEvent.delete()
or CaseEventQuerySet.delete(): a cascade from a related object's delete
(the Collector deletes in SQL batches). CaseEvent.case is PROTECT, so a
cascade cannot reach events today; this keeps it that way if an FK is ever
changed. Having a pre_delete receiver also stops Django from "fast
deleting" CaseEvent rows without sending signals."""
from __future__ import annotations

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from exception_management.models import CaseEvent, CaseImmutableError


@receiver(pre_delete, sender=CaseEvent)
def _block_case_event_delete(sender, instance, **kwargs):
    raise CaseImmutableError("Case events are append-only; they cannot be deleted")
