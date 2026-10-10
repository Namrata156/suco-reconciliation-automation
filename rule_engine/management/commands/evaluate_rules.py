"""
Runs the Business Rule Engine evaluator (Phase 2 Step 4) for one business
date, or every date that has a current presence matrix (--all-loaded,
oldest first). Each date is evaluated against its own files only (no
cross-day checks, rule set v3.2). Idempotent: a re-run supersedes the date's current results
(kept, not deleted) and recreates them.

    manage.py evaluate_rules --business-date 2026-09-25
    manage.py evaluate_rules --all-loaded
"""
from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from core.models import PaymentModule
from core.working_days import WorkingDayCalendar
from rule_engine.evaluator import NoActiveRuleSet, evaluate_business_date, loaded_business_dates


class Command(BaseCommand):
    help = "Applies the active rule set to every match group of a business date and records the results."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--business-date", type=date.fromisoformat, help="YYYY-MM-DD")
        target.add_argument("--all-loaded", action="store_true", help="Every date with a current presence matrix")
        parser.add_argument("--payment-module", default="UPI")

    def handle(self, *args, **options):
        try:
            module = PaymentModule.objects.get(code=options["payment_module"])
        except PaymentModule.DoesNotExist as exc:
            raise CommandError(f"No payment module {options['payment_module']!r}") from exc
        dates = loaded_business_dates(module) if options["all_loaded"] else [options["business_date"]]
        if not dates:
            raise CommandError("No business dates with a current presence matrix")
        calendar = WorkingDayCalendar()
        for d in dates:
            try:
                s = evaluate_business_date(module, d, triggered_by="manage.py evaluate_rules", calendar=calendar)
            except NoActiveRuleSet as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(
                self.style.SUCCESS(
                    f"{module.code} {d}: {s.group_count} groups, rule set {s.rule_set_version}, "
                    f"{s.superseded_count} previous results superseded, {s.duration_seconds}s"
                )
            )
            self.stdout.write("  rules: " + ", ".join(f"{k} {v}" for k, v in s.rule_counts.items() if v))
            for label, n in (
                ("calendar_incomplete", s.calendar_incomplete_count),
                ("no TAT for Adjtype", s.tat_unresolved_count),
                ("groups missing a CBS/NPCI presence row", s.groups_missing_cbs_or_npci_presence),
            ):
                if n:
                    self.stdout.write(self.style.WARNING(f"  {label}: {n}"))
