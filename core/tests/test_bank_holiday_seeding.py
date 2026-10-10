"""
Ops confirmation of the Karnataka 2026 bank holidays (project owner decision
2026-10-10, data migration core/0006) and its interaction with re-seeding.
"""
from __future__ import annotations

import importlib
from datetime import date
from io import StringIO
from types import SimpleNamespace

from django.apps import apps as global_apps
from django.core.management import call_command
from django.test import TestCase

from core.models import BankHoliday
from core.seed_data import bank_holidays_karnataka_2026 as data
from core.seeding import seed_bank_holidays

migration_0006 = importlib.import_module("core.migrations.0006_confirm_bank_holidays_2026")
NOTE = migration_0006.CONFIRMATION_NOTE


class ConfirmationMigrationTests(TestCase):
    def test_all_22_confirmed_with_note_and_seed_notes_kept(self):
        rows = BankHoliday.objects.filter(region="Karnataka", date__year=2026)
        self.assertEqual(rows.count(), 22)
        self.assertEqual(rows.filter(confirmed_by_ops=True).count(), 22)
        for row in rows:
            self.assertEqual(row.notes, f"{data.NOTES}\n{NOTE}")

    def test_forwards_is_idempotent_and_backwards_restores_seed_state(self):
        migration_0006.forwards(global_apps, None)
        row = BankHoliday.objects.get(region="Karnataka", date=date(2026, 12, 25))
        self.assertEqual(row.notes.count(NOTE), 1)

        migration_0006.backwards(global_apps, None)
        rows = BankHoliday.objects.filter(region="Karnataka", date__year=2026)
        self.assertFalse(rows.filter(confirmed_by_ops=True).exists())
        self.assertEqual(set(rows.values_list("notes", flat=True)), {data.NOTES})

    def test_empty_notes_get_note_without_leading_newline(self):
        BankHoliday.objects.filter(date=date(2026, 1, 26)).update(notes="", confirmed_by_ops=False)
        migration_0006.forwards(global_apps, None)
        self.assertEqual(BankHoliday.objects.get(date=date(2026, 1, 26)).notes, NOTE)


class ReseedPreservesConfirmationTests(TestCase):
    def test_reseed_keeps_confirmed_flag_and_note(self):
        before = dict(BankHoliday.objects.values_list("id", "notes"))
        created, existing, _ = seed_bank_holidays(data)
        self.assertEqual((created, existing), (0, 22))
        rows = BankHoliday.objects.filter(region="Karnataka", date__year=2026)
        self.assertEqual(rows.filter(confirmed_by_ops=True).count(), 22)
        self.assertEqual(dict(rows.values_list("id", "notes")), before)

    def test_command_reports_confirmed_count(self):
        out = StringIO()
        call_command("seed_bank_holidays", stdout=out)
        self.assertIn("0 created, 22 already present (22 of 22 confirmed by ops)", out.getvalue())
        self.assertEqual(BankHoliday.objects.filter(confirmed_by_ops=True).count(), 22)

    def test_new_year_seeded_later_defaults_to_unconfirmed(self):
        data_2027 = SimpleNamespace(
            REGION="Karnataka",
            YEAR=2027,
            SOURCE_URL=data.SOURCE_URL,
            RETRIEVED_ON=date(2027, 1, 1),
            NOTES="test 2027 list",
            HOLIDAYS=[(date(2027, 1, 26), "Republic Day"), (date(2027, 8, 16), "Independence Day (obs.)")],
        )
        created, existing, _ = seed_bank_holidays(data_2027)
        self.assertEqual((created, existing), (2, 0))
        new_rows = BankHoliday.objects.filter(region="Karnataka", date__year=2027)
        self.assertFalse(new_rows.filter(confirmed_by_ops=True).exists())
        self.assertEqual(set(new_rows.values_list("notes", flat=True)), {"test 2027 list"})
        # 2026 untouched.
        self.assertEqual(
            BankHoliday.objects.filter(date__year=2026, confirmed_by_ops=True).count(), 22
        )
