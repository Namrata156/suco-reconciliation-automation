"""
One-off correction (2026-10-08): recomputes `direction` for every
`TransactionFact` row whose file's `FileTypeConfig` now uses the per-row
`ssk_party:` direction rule (DRC and Timeout since file_processing migration
0007; Adjustment, with the mapping mirrored, since 0008). Those rows were
loaded under the earlier whole-file `constant:` rule (DRC DEBIT, Timeout
CREDIT, Adjustment CREDIT), which mislabelled the rows where SSK is the
other party. Each fact's own config rule is used, so the mirrored
Adjustment mapping needs nothing special here.

The party cells are read from each fact's source row via
`TransactionFact.staging_row()` -- the live staging row or, for archived
dates, its History copy -- so nothing is re-ingested. Dispute volumes are
small (hundreds of rows), so the per-row lookup is fine. Only `direction`
(and `updated_at`) change. Idempotent: a second run changes 0 rows.

Run `reconciliation` presence-matrix rebuilds for the affected business
dates afterwards: overlay RegisterPresence rows copy the fact's direction.
"""
from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import Direction
from file_processing.tasks import SSK_PARTY_RULE_PREFIX, _direction_from_ssk_party, _parse_ssk_party_rule
from transactions.models import TransactionFact


class Command(BaseCommand):
    help = (
        "Recompute direction for TransactionFact rows whose FileTypeConfig uses the "
        "ssk_party: rule (DRC/Timeout/Adjustment), from their source cells. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would change; write nothing"
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        facts = list(
            TransactionFact.objects.filter(
                uploaded_file__file_type_config__direction_derivation_rule__startswith=SSK_PARTY_RULE_PREFIX
            )
            .select_related("uploaded_file__file_type_config")
            .order_by("source_register_type", "business_date", "id")
        )

        params_by_config: dict[int, dict[str, str]] = {}
        before: Counter = Counter()
        after: Counter = Counter()
        changed: Counter = Counter()
        unknown: list[TransactionFact] = []
        no_source_row: list[TransactionFact] = []
        to_update: list[TransactionFact] = []
        now = timezone.now()

        for fact in facts:
            config = fact.uploaded_file.file_type_config
            if config.pk not in params_by_config:
                params_by_config[config.pk] = _parse_ssk_party_rule(config.direction_derivation_rule)
            key = (fact.source_register_type, fact.business_date)
            before[(*key, fact.direction)] += 1

            source_row = fact.staging_row()
            if source_row is None:
                no_source_row.append(fact)
                after[(*key, fact.direction)] += 1
                continue

            new_direction = _direction_from_ssk_party(source_row.raw_cells or {}, params_by_config[config.pk])
            if new_direction == Direction.UNKNOWN:
                unknown.append(fact)
            after[(*key, new_direction)] += 1
            if new_direction != fact.direction:
                changed[(*key, f"{fact.direction}->{new_direction}")] += 1
                fact.direction = new_direction
                fact.updated_at = now
                to_update.append(fact)

        if not dry_run and to_update:
            with transaction.atomic():
                TransactionFact.objects.bulk_update(to_update, ["direction", "updated_at"], batch_size=1000)

        self._print_counts("Before", before)
        self._print_counts("After" + (" (dry run, not written)" if dry_run else ""), after)
        if changed:
            self.stdout.write("Changes:")
            for (register_type, business_date, transition), n in sorted(changed.items()):
                self.stdout.write(f"  {register_type:<10} {business_date}  {transition:<16} {n:>6,}")
        for label, rows in (("UNKNOWN (neither party is the bank)", unknown), ("No source row found", no_source_row)):
            self.stdout.write(f"{label}: {len(rows)}")
            for fact in rows[:20]:
                self.stdout.write(
                    f"  fact {fact.pk} {fact.source_register_type} {fact.business_date} rrn={fact.rrn}"
                )
        verb = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(to_update):,} of {len(facts):,} rows."))

    def _print_counts(self, title: str, counts: Counter) -> None:
        self.stdout.write(f"{title}:")
        for (register_type, business_date, direction), n in sorted(counts.items()):
            self.stdout.write(f"  {register_type:<10} {business_date}  {direction:<8} {n:>6,}")
