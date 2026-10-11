"""
Seeds NTSL category mapping v1 (seed_data/ntsl_category_map_v1.py) as the
ACTIVE version. Create-only and idempotent: if the module already has any
mapping version, nothing is created (a seeded version is never edited --
changes go through settlement.mapping_changes).

Used by `manage.py seed_ntsl_category_map` (fresh/test DBs) and, with
historical models, by data migration settlement/0002 (an existing DB that
already has the UPI PaymentModule). Same split as rule_engine's seeding.

The seed counts as an approved release (the owner approved the design and
the mapping is built from real files), like the rule-table seed; its log
entry is SEEDED, with no maker or checker.
"""
from __future__ import annotations

from types import ModuleType

from django.db import transaction
from django.utils import timezone

FIELDS = ("match_type", "pattern", "category", "txn_type", "priority", "notes")
SEEDED_BY = "seed"


def entry_dicts(data: ModuleType) -> list[dict]:
    return [dict(zip(FIELDS, e)) for e in data.ENTRIES]


def seed_with(models: dict, data: ModuleType) -> tuple[bool, str]:
    """`models`: PaymentModule, NtslCategoryMapVersion, NtslCategoryMapEntry,
    NtslMappingEvent -- live or historical."""
    PaymentModule = models["PaymentModule"]
    Version = models["NtslCategoryMapVersion"]
    Entry = models["NtslCategoryMapEntry"]
    Event = models["NtslMappingEvent"]
    module, _ = PaymentModule.objects.get_or_create(
        code=data.PAYMENT_MODULE_CODE, defaults={"name": data.PAYMENT_MODULE_CODE}
    )
    existing = Version.objects.filter(payment_module=module).order_by("-version").first()
    if existing is not None:
        return False, f"{module.code} already has NTSL mapping v{existing.version} ({existing.status}); nothing seeded"
    now = timezone.now()
    version = Version(
        payment_module=module, version=data.VERSION, status="PENDING", reason=data.REASON,
        proposed_by_username=SEEDED_BY, proposed_at=now,
    )
    version.save()
    Entry.objects.bulk_create(Entry(version=version, **e) for e in entry_dicts(data))
    version.status, version.activated_at, version.decided_at = "ACTIVE", now, now
    version.decided_by_username = SEEDED_BY
    version._workflow_change = True
    version.save()
    Event.objects.create(
        version=version, event_type="SEEDED", actor_username=SEEDED_BY, remarks=data.REASON,
        changes={"entries": len(data.ENTRIES)},
    )
    return True, f"{module.code} NTSL mapping v{data.VERSION} created and activated with {len(data.ENTRIES)} entries"


@transaction.atomic
def seed_ntsl_category_map(data: ModuleType | None = None) -> tuple[bool, str]:
    from core.models import PaymentModule
    from settlement.models import NtslCategoryMapEntry, NtslCategoryMapVersion, NtslMappingEvent
    from settlement.seed_data import ntsl_category_map_v1

    return seed_with(
        {
            "PaymentModule": PaymentModule, "NtslCategoryMapVersion": NtslCategoryMapVersion,
            "NtslCategoryMapEntry": NtslCategoryMapEntry, "NtslMappingEvent": NtslMappingEvent,
        },
        data or ntsl_category_map_v1,
    )
