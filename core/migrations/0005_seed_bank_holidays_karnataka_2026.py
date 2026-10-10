"""
Data migration: seed the 22 Karnataka 2026 bank holidays (RBI Bengaluru
regional office; working-day rule and region decided by the project owner
2026-10-10) for the rule-deadline calendar.

Unlike core/0003 this inserts on every DB, including fresh/test DBs: a
holiday is bank-wide and references no PaymentModule, so it cannot collide
with tests that create their own PaymentModule(code="UPI"). Values come from
the frozen plain-data module core/seed_data/bank_holidays_karnataka_2026.py
(same approach as rule_engine/0002). Create-only; backwards removes only
rows still exactly as seeded and never confirmed by ops.
"""
from django.db import migrations

from core.seed_data import bank_holidays_karnataka_2026 as data


def forwards(apps, schema_editor):
    BankHoliday = apps.get_model("core", "BankHoliday")
    for day, name in data.HOLIDAYS:
        BankHoliday.objects.get_or_create(
            region=data.REGION,
            date=day,
            defaults=dict(name=name, source_url=data.SOURCE_URL, retrieved_on=data.RETRIEVED_ON, notes=data.NOTES),
        )


def backwards(apps, schema_editor):
    BankHoliday = apps.get_model("core", "BankHoliday")
    for day, name in data.HOLIDAYS:
        BankHoliday.objects.filter(region=data.REGION, date=day, name=name, confirmed_by_ops=False).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_bankholiday"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
