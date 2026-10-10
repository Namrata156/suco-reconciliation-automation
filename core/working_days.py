"""
Working-day calendar for rule deadlines (Phase 2 Step 4).

Working-day rule (project owner, 2026-10-10; docs/bank_holidays_bengaluru_2026.txt):
a day is NOT a working day if it is
  - a Sunday,
  - the 2nd or 4th Saturday of its month, or
  - a BankHoliday for the calendar region (Karnataka, RBI Bengaluru).
"T+n" = the n-th working day after T. Calendar-day TATs (7 / 35 days) are
not shifted -- that is the caller's business (see rule_engine.evaluator);
this module only does working days.

Missing years (owner, 2026-10-10): if a computation touches a year with NO
holidays loaded for the region (e.g. 2027, not yet published by RBI), it is
still computed, skipping only Sundays and 2nd/4th Saturdays, and the result
says so (`calendar_incomplete`). A warning is logged once per calendar
instance and year -- never silently.

"Touches" = any day examined while stepping from T+1 to the result; T itself
is not examined (the clock starts on T whatever kind of day it is).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

logger = logging.getLogger(__name__)

DEFAULT_REGION = "Karnataka"


def is_second_or_fourth_saturday(day: date) -> bool:
    return day.weekday() == 5 and (day.day - 1) // 7 in (1, 3)


@dataclass(frozen=True)
class WorkingDayResult:
    date: date
    calendar_incomplete: bool
    missing_years: tuple[int, ...] = ()


class WorkingDayCalendar:
    """Loads the region's holidays once (one query); use one instance per
    evaluation run. `holidays` can be passed directly (tests)."""

    def __init__(self, region: str = DEFAULT_REGION, holidays: dict[date, str] | None = None):
        self.region = region
        if holidays is None:
            from core.models import BankHoliday

            holidays = dict(BankHoliday.objects.filter(region=region).values_list("date", "name"))
        self.holidays = holidays
        self.loaded_years = frozenset(d.year for d in holidays)
        self._warned_years: set[int] = set()

    def has_year(self, year: int) -> bool:
        return year in self.loaded_years

    def non_working_reason(self, day: date) -> str | None:
        """Why `day` is not a working day, or None if it is one."""
        if day.weekday() == 6:
            return "Sunday"
        if is_second_or_fourth_saturday(day):
            return "2nd/4th Saturday"
        if day in self.holidays:
            return f"Holiday: {self.holidays[day]}"
        return None

    def is_working_day(self, day: date) -> bool:
        return self.non_working_reason(day) is None

    def add_working_days(self, start: date, n: int) -> WorkingDayResult:
        """The n-th working day after `start` (n >= 0; n = 0 returns start)."""
        if n < 0:
            raise ValueError("n must be >= 0")
        day = start
        missing: set[int] = set()
        remaining = n
        while remaining:
            day += timedelta(days=1)
            if not self.has_year(day.year):
                missing.add(day.year)
            if self.is_working_day(day):
                remaining -= 1
        for year in sorted(missing - self._warned_years):
            logger.warning(
                "Working-day calendar: no bank holidays loaded for %s %s. Deadlines touching %s skip only "
                "Sundays and 2nd/4th Saturdays and are flagged calendar_incomplete.",
                self.region,
                year,
                year,
            )
            self._warned_years.add(year)
        return WorkingDayResult(day, bool(missing), tuple(sorted(missing)))
