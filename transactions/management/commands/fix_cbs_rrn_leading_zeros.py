"""
One-off correction (2026-10-02): re-extracts the RRN of every CBS
`TransactionFact` row still flagged `rrn_valid=False`, using the current
`file_processing.tasks._extract_cbs_rrn` -- which now restores the leading
zeros CBS drops from its remarks. Rows loaded before that change carry the
short RRN; rows loaded afterwards never need this.

The raw remarks are read through each fact's `staging_row_id`, from the live
staging table or, for archived dates, its History copy (same lineage as
`TransactionFact.staging_row()`). Only `rrn`/`rrn_valid` are changed, and only
when re-extraction yields a valid RRN. Idempotent: a second run changes 0 rows.
"""
from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import RegisterType
from file_processing.tasks import _extract_cbs_rrn
from transactions.models import TransactionFact
from transactions.models.history import HISTORY_MODEL_BY_STAGING_MODEL
from transactions.models.staging import STAGING_MODEL_BY_REGISTER_TYPE

_BATCH = 5000


class Command(BaseCommand):
    help = (
        "Restore leading zeros on CBS TransactionFact RRNs loaded before extraction "
        "padded them. Changes only rrn/rrn_valid. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would change; write nothing"
        )

    def handle(self, *args, **options):
        staging_model = STAGING_MODEL_BY_REGISTER_TYPE[RegisterType.CBS_GL_EXTRACT]
        history_model = HISTORY_MODEL_BY_STAGING_MODEL[staging_model]
        facts = list(
            TransactionFact.objects.filter(
                source_register_type=RegisterType.CBS_GL_EXTRACT, rrn_valid=False
            ).select_related("uploaded_file__file_type_config")
        )

        staging_ids = [f.staging_row_id for f in facts if f.staging_row_id]
        remarks: dict[int, str | None] = {}
        for start in range(0, len(staging_ids), _BATCH):
            batch = staging_ids[start : start + _BATCH]
            remarks.update(staging_model.objects.filter(id__in=batch).values_list("id", "remarks"))
            missing = [i for i in batch if i not in remarks]
            if missing:
                remarks.update(
                    history_model.objects.filter(original_id__in=missing).values_list("original_id", "remarks")
                )

        to_update: list[TransactionFact] = []
        fixed_by_date: Counter = Counter()
        still_invalid_by_date: Counter = Counter()
        for fact in facts:
            expected_digits = fact.uploaded_file.file_type_config.rrn_expected_digit_length
            _direction, rrn, rrn_valid = _extract_cbs_rrn(remarks.get(fact.staging_row_id), expected_digits)
            if rrn_valid:
                fact.rrn, fact.rrn_valid = rrn, True
                to_update.append(fact)
                fixed_by_date[fact.business_date] += 1
            else:
                still_invalid_by_date[fact.business_date] += 1

        if not options["dry_run"] and to_update:
            with transaction.atomic():
                TransactionFact.objects.bulk_update(to_update, ["rrn", "rrn_valid"], batch_size=_BATCH)

        verb = "Would fix" if options["dry_run"] else "Fixed"
        for business_date in sorted(set(fixed_by_date) | set(still_invalid_by_date)):
            self.stdout.write(
                f"  {business_date}: {verb.lower()} {fixed_by_date[business_date]:,}, "
                f"still without a valid RRN {still_invalid_by_date[business_date]:,}"
            )
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(to_update):,} CBS RRNs."))
