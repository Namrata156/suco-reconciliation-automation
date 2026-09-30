"""
Staging archival: moves live staging rows for superseded business dates
into their History mirrors (transactions/models/history.py).

Pulled forward from Phase 3 at the project owner's request (decided
2026-09-28). Trigger rule: every staging row whose file's business date is
OLDER than the latest business date loaded for that payment module is
archived; the latest date stays live. Run manually via the
`archive_older_business_dates` management command -- deliberately not
wired into the pipeline yet (see tracker.md "Archival job trigger").

Scope is staging only. TransactionFact and NtslSettlementLine stay live;
TransactionFact.staging_row_id (a plain id, no FK) keeps resolving via
History.original_id -- see TransactionFact.staging_row().

A file whose business_date is still NULL (e.g. a CBS file paused at
AWAITING_DATE_SELECTION) is never archived; it is counted in the report.

Idempotent: archived rows are no longer in staging, so a second run with
no newer date loaded moves nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from django.db import transaction
from django.db.models import Count, Max

from core.models import PaymentModule
from file_processing.models import ProcessingStatus, UploadedFile
from transactions.models.history import HISTORY_MODEL_BY_STAGING_MODEL
from transactions.models.staging import STAGING_MODEL_BY_REGISTER_TYPE

# Fields the History row gets from ArchivalFields rather than a copy.
_NOT_COPIED = {"id"}


@dataclass
class ArchivalReport:
    payment_module: str
    latest_business_date: date | None
    dry_run: bool
    # {staging model name: {business_date: row count}}
    rows_by_table: dict[str, dict[date, int]] = field(default_factory=dict)
    # {staging model name: rows whose file has no business_date yet}
    undated_rows_by_table: dict[str, int] = field(default_factory=dict)

    @property
    def total_rows(self) -> int:
        return sum(n for per_date in self.rows_by_table.values() for n in per_date.values())


def _copied_field_names(staging_model) -> list[str]:
    return [
        f.attname
        for f in staging_model._meta.concrete_fields
        if f.name not in _NOT_COPIED
    ]


def archive_older_business_dates(
    payment_module_code: str = "UPI", dry_run: bool = False, batch_size: int = 5000
) -> ArchivalReport:
    payment_module = PaymentModule.objects.get(code=payment_module_code)
    latest = UploadedFile.objects.filter(
        payment_module=payment_module, processing_status=ProcessingStatus.LOADED
    ).aggregate(latest=Max("business_date"))["latest"]
    report = ArchivalReport(payment_module_code, latest, dry_run)
    if latest is None:
        return report

    reason = f"superseded by business_date {latest.isoformat()}"
    for staging_model in STAGING_MODEL_BY_REGISTER_TYPE.values():
        history_model = HISTORY_MODEL_BY_STAGING_MODEL[staging_model]
        name = staging_model.__name__
        module_rows = staging_model.objects.filter(uploaded_file__payment_module=payment_module)

        undated = module_rows.filter(uploaded_file__business_date__isnull=True).count()
        if undated:
            report.undated_rows_by_table[name] = undated

        to_archive = module_rows.filter(uploaded_file__business_date__lt=latest)
        per_date = dict(
            to_archive.values_list("uploaded_file__business_date")
            .annotate(n=Count("id"))
            .order_by("uploaded_file__business_date")
        )
        if per_date:
            report.rows_by_table[name] = per_date
        if dry_run or not per_date:
            continue

        fields = _copied_field_names(staging_model)
        while True:
            with transaction.atomic():
                batch = list(to_archive.order_by("id").values("id", *fields)[:batch_size])
                if not batch:
                    break
                history_model.objects.bulk_create(
                    [
                        history_model(
                            original_id=row["id"],
                            archival_reason=reason,
                            **{f: row[f] for f in fields},
                        )
                        for row in batch
                    ],
                    batch_size=batch_size,
                )
                staging_model.objects.filter(id__in=[row["id"] for row in batch]).delete()

    return report
