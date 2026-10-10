"""
Working-day calendar (owner decisions 2026-10-10): Sundays, 2nd/4th
Saturdays and Karnataka bank holidays are non-working; T+n is the n-th
working day after T; a year with no holidays loaded is still computed but
flagged calendar_incomplete and logged.
"""
from __future__ import annotations

import re
from datetime import date
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from core.models import BankHoliday
from core.seed_data import bank_holidays_karnataka_2026 as data
from core.working_days import WorkingDayCalendar, is_second_or_fourth_saturday

DOC = Path(__file__).resolve().parents[2] / "docs" / "bank_holidays_bengaluru_2026.txt"


class SeedTests(TestCase):
    def test_migration_seeded_the_22_doc_dates_exactly(self):
        doc_dates = {
            date.fromisoformat(m) for m in re.findall(r"^\s+(2026-\d\d-\d\d)\s+\w{3}\s", DOC.read_text(), re.M)
        }
        self.assertEqual(len(doc_dates), 22)
        rows = BankHoliday.objects.filter(region="Karnataka")
        self.assertEqual(set(rows.values_list("date", flat=True)), doc_dates)
        self.assertEqual({d for d, _ in data.HOLIDAYS}, doc_dates)
        self.assertFalse(rows.filter(confirmed_by_ops=True).exists())
        self.assertEqual(set(rows.values_list("source_url", flat=True)), {data.SOURCE_URL})
        self.assertEqual(set(rows.values_list("retrieved_on", flat=True)), {date(2026, 10, 10)})

    def test_command_is_idempotent_and_recreates_missing_rows(self):
        BankHoliday.objects.filter(date=date(2026, 12, 25)).delete()
        out = StringIO()
        call_command("seed_bank_holidays", stdout=out)
        self.assertIn("1 created, 21 already present", out.getvalue())
        call_command("seed_bank_holidays", stdout=out)
        self.assertIn("0 created, 22 already present", out.getvalue())
        self.assertEqual(BankHoliday.objects.count(), 22)

    def test_admin_is_read_only(self):
        from django.contrib.admin.sites import site
        from django.contrib.auth import get_user_model
        from django.test import RequestFactory

        user = get_user_model().objects.create_superuser("admin", "a@example.invalid", "x")
        request = RequestFactory().get("/")
        request.user = user
        model_admin = site._registry[BankHoliday]
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
        self.client.force_login(user)
        self.assertEqual(self.client.get("/admin/core/bankholiday/").status_code, 200)


class CalendarTests(TestCase):
    def setUp(self):
        self.cal = WorkingDayCalendar()

    def test_friday_before_second_saturday_holiday(self):
        # Sat 10 Oct is a 2nd Saturday and Mahalaya Amavasye; Sun 11 Oct.
        r = self.cal.add_working_days(date(2026, 10, 9), 1)
        self.assertEqual(r.date, date(2026, 10, 12))
        self.assertFalse(r.calendar_incomplete)

    def test_two_consecutive_holidays(self):
        # 20 Oct Mahanavami, 21 Oct Vijaya Dasami.
        self.assertEqual(self.cal.add_working_days(date(2026, 10, 19), 1).date, date(2026, 10, 22))

    def test_plain_weekday_and_r05_example(self):
        self.assertEqual(self.cal.add_working_days(date(2026, 7, 14), 1).date, date(2026, 7, 15))

    def test_first_third_fifth_saturdays_are_working(self):
        self.assertTrue(self.cal.is_working_day(date(2026, 10, 3)))  # 1st
        self.assertTrue(self.cal.is_working_day(date(2026, 10, 17)))  # 3rd
        self.assertTrue(self.cal.is_working_day(date(2026, 10, 31)))  # 5th
        self.assertFalse(self.cal.is_working_day(date(2026, 10, 24)))  # 4th
        self.assertEqual(self.cal.non_working_reason(date(2026, 10, 24)), "2nd/4th Saturday")
        self.assertEqual(self.cal.non_working_reason(date(2026, 10, 25)), "Sunday")
        self.assertTrue(self.cal.non_working_reason(date(2026, 12, 25)).startswith("Holiday"))

    def test_n_working_days_and_zero(self):
        # Thu 24 Sep 2026 + 2: Fri 25, (Sat 26 = 4th Sat, Sun 27), Mon 28.
        self.assertEqual(self.cal.add_working_days(date(2026, 9, 24), 2).date, date(2026, 9, 28))
        self.assertEqual(self.cal.add_working_days(date(2026, 9, 26), 0).date, date(2026, 9, 26))
        with self.assertRaises(ValueError):
            self.cal.add_working_days(date(2026, 9, 26), -1)

    def test_crossing_into_2027_sets_calendar_incomplete_and_logs(self):
        with self.assertLogs("core.working_days", level="WARNING") as logs:
            r = self.cal.add_working_days(date(2026, 12, 31), 1)
            self.cal.add_working_days(date(2026, 12, 31), 2)  # same year: warned once per calendar
        self.assertEqual(r.date, date(2027, 1, 1))  # Friday; no 2027 holidays known
        self.assertTrue(r.calendar_incomplete)
        self.assertEqual(r.missing_years, (2027,))
        self.assertEqual(len(logs.records), 1)
        self.assertIn("2027", logs.output[0])

    def test_within_loaded_year_not_incomplete(self):
        self.assertFalse(self.cal.add_working_days(date(2026, 12, 24), 1).calendar_incomplete)
        # Thu 24 Dec + 1 -> Fri 25 Dec is Christmas -> Sat 26 Dec is the 4th Saturday -> Mon 28.
        self.assertEqual(self.cal.add_working_days(date(2026, 12, 24), 1).date, date(2026, 12, 28))


class SaturdayDetectionTests(SimpleTestCase):
    def test_second_and_fourth_saturdays(self):
        # September 2026: Saturdays 5, 12, 19, 26.
        self.assertEqual(
            [d for d in (5, 12, 19, 26) if is_second_or_fourth_saturday(date(2026, 9, d))], [12, 26]
        )
        # August 2026: Saturdays 1, 8, 15, 22, 29.
        self.assertEqual(
            [d for d in (1, 8, 15, 22, 29) if is_second_or_fourth_saturday(date(2026, 8, d))], [8, 22]
        )
        self.assertFalse(is_second_or_fourth_saturday(date(2026, 9, 13)))  # a Sunday

    def test_injected_holidays_without_db(self):
        cal = WorkingDayCalendar(holidays={date(2026, 7, 15): "Test"})
        self.assertEqual(cal.add_working_days(date(2026, 7, 14), 1).date, date(2026, 7, 16))
