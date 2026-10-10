"""
Data migration: mark the Karnataka 2026 bank holidays as confirmed by ops
(project owner decision 2026-10-10: ops confirmed the 22-day list seeded by
core/0005).

Updates every Karnataka row dated 2026 -- not only the 22 seeded dates -- so
an ops-added 2026 row present at migrate time is covered too. Appends the
confirmation note to `notes` without clobbering existing text, and is
idempotent (a row already carrying the note is not appended to again).
Backwards sets confirmed_by_ops=False and strips exactly that note.
"""
from django.db import migrations

REGION = "Karnataka"
YEAR = 2026
CONFIRMATION_NOTE = "Confirmed by ops via project owner, 2026-10-10"


def forwards(apps, schema_editor):
    BankHoliday = apps.get_model("core", "BankHoliday")
    for row in BankHoliday.objects.filter(region=REGION, date__year=YEAR):
        row.confirmed_by_ops = True
        if CONFIRMATION_NOTE not in row.notes:
            row.notes = f"{row.notes}\n{CONFIRMATION_NOTE}" if row.notes else CONFIRMATION_NOTE
        row.save(update_fields=["confirmed_by_ops", "notes"])


def backwards(apps, schema_editor):
    BankHoliday = apps.get_model("core", "BankHoliday")
    for row in BankHoliday.objects.filter(region=REGION, date__year=YEAR):
        row.confirmed_by_ops = False
        row.notes = row.notes.replace(f"\n{CONFIRMATION_NOTE}", "").replace(CONFIRMATION_NOTE, "")
        row.save(update_fields=["confirmed_by_ops", "notes"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_seed_bank_holidays_karnataka_2026"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
