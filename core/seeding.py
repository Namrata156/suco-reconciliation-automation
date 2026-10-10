"""
Loads a frozen bank-holiday list (core/seed_data/bank_holidays_*.py) into
BankHoliday. Used by `manage.py seed_bank_holidays`; data migration core/0005 does the
same get_or_create with its historical model (migrations don't import live
app code), and core/tests/test_working_days.py checks both give the same rows.

Idempotent and create-only: an existing (region, date) row is left alone,
including any ops edits (name, notes, confirmed_by_ops -- e.g. the 2026
confirmation applied by core/0006 survives a re-seed); a name that differs
from the seed data is reported as a warning. Newly created rows get the
model default confirmed_by_ops=False.
"""
from __future__ import annotations

from types import ModuleType


def seed_bank_holidays(data: ModuleType) -> tuple[int, int, list[str]]:
    """Returns (created, existing, warnings)."""
    from core.models import BankHoliday as model

    created = existing = 0
    warnings: list[str] = []
    for day, name in data.HOLIDAYS:
        obj, was_created = model.objects.get_or_create(
            region=data.REGION,
            date=day,
            defaults=dict(
                name=name,
                source_url=data.SOURCE_URL,
                retrieved_on=data.RETRIEVED_ON,
                notes=data.NOTES,
            ),
        )
        if was_created:
            created += 1
        else:
            existing += 1
            if obj.name != name:
                warnings.append(f"{data.REGION} {day}: is '{obj.name}', seed has '{name}' (left unchanged)")
    return created, existing, warnings
