"""
Seeds the Business Rule Engine repository from the frozen rule-table data,
oldest version first: UPI rule set v3.1 (24 rules), then v3.2 (v3.1 with
R13 changed, owner 2026-10-10), which is activated and retires v3.1. Also
the response/reason code table and the ageing buckets. See
rule_engine/seeding.py for what "idempotent" means here (create-only;
existing rows and versions are never modified, and any difference from the
seed data is printed as a warning).

A fresh DB without the UPI PaymentModule gets everything from this command;
an existing DB that already has UPI got it from data migrations
rule_engine/0002 (v3.1) and 0005 (v3.2) (same pattern as
seed_settlement_cycles / core 0003).
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from rule_engine.seed_data import v3_1, v3_2
from rule_engine.seeding import seed_rule_table

VERSIONS = (v3_1, v3_2)  # oldest first; the last one ends up ACTIVE


class Command(BaseCommand):
    help = "Seeds UPI rule sets v3.1 and v3.2 (v3.2 active), the response-code table and ageing buckets. Idempotent."

    def handle(self, *args, **options):
        for data in VERSIONS:
            report = seed_rule_table(data, created_by="seed_rule_sets")
            for warning in report.warnings:
                self.stdout.write(self.style.WARNING(warning))
            rule_set_msg = (
                f"rule set {data.VERSION_LABEL} created and activated with {report.rules_created} rules"
                if report.rule_set_created
                else f"rule set {data.VERSION_LABEL} not created (already present or blocked, see above)"
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Rule repository seed complete: {rule_set_msg}; codes {report.codes_created} created / "
                    f"{report.codes_existing} present; ageing buckets {report.buckets_created} created / "
                    f"{report.buckets_existing} present."
                )
            )
