"""
Creates / re-links exception cases from the current rule results of one
business date, or of every evaluated date (--all-evaluated, oldest first).
Idempotent. Normally not needed by hand: evaluate_rules runs the same sync
for each date it evaluates.

    manage.py sync_cases --date 2026-09-25
    manage.py sync_cases --all-evaluated
"""
from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from core.models import PaymentModule
from exception_management.sync import evaluated_business_dates, sync_cases_for_date


class Command(BaseCommand):
    help = "Creates and re-links exception cases from the current rule results (Phase 3 design section 6)."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--date", type=date.fromisoformat, help="Business date, YYYY-MM-DD")
        target.add_argument("--all-evaluated", action="store_true", help="Every date that has rule results")
        parser.add_argument("--payment-module", default="UPI")

    def handle(self, *args, **options):
        try:
            module = PaymentModule.objects.get(code=options["payment_module"])
        except PaymentModule.DoesNotExist as exc:
            raise CommandError(f"No payment module {options['payment_module']!r}") from exc
        dates = evaluated_business_dates(module) if options["all_evaluated"] else [options["date"]]
        if not dates:
            raise CommandError("No evaluated business dates")
        for d in dates:
            write_summary(self, sync_cases_for_date(module, d, source="manage.py sync_cases"))


def write_summary(command: BaseCommand, s) -> None:
    command.stdout.write(command.style.SUCCESS(
        f"{s.payment_module_code} {s.business_date}: {s.actionable_results} results need action; cases "
        f"{s.created} created, {s.relinked} re-linked, {s.unchanged} unchanged, {s.result_updated} updated, "
        f"{s.rule_changed} rule changed, {s.superseded} closed (superseded), {s.reopened} reopened, "
        f"{s.flagged_for_review} flagged for review; {s.events_written} events"
    ))
    if s.cases_by_rule_status:
        command.stdout.write("  cases: " + ", ".join(f"{k} {v}" for k, v in s.cases_by_rule_status.items()))
    if s.multiple_adjustment_rows:
        command.stdout.write(command.style.WARNING(f"  multiple adjustment rows: {s.multiple_adjustment_rows}"))
    if s.rrc_already_sent:
        command.stdout.write(command.style.WARNING(f"  RRC already sent (A1): {s.rrc_already_sent}"))
    if s.rrc_warning_changed:
        command.stdout.write(f"  RRC warnings changed on cases of any date: {s.rrc_warning_changed}")
    if s.no_deadline:
        command.stdout.write(command.style.WARNING(f"  no deadline (A4): {s.no_deadline}"))
    if s.flagged_for_review:
        command.stdout.write(command.style.WARNING(f"  flagged for review: {s.flagged_for_review}"))
