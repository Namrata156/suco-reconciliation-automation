"""
Daily transaction volume summary (Phase 6 design 2.3, R4a; contract B3).

One row per payment module + business date + file type + side + cycle: how
many rows the date's CURRENT files of that type carried and their amount.
The volume report reads only this table, never the raw facts (about 149,000
facts a day), so a 24-month report adds up a few thousand rows.

Written only by transactions.services.volume.refresh_daily_volume: at the
end of every reconciliation run (reconciliation.runs) and by the
`fill_daily_volume` command for dates loaded before this table existed.
A refresh replaces the date's rows, so the table follows corrected files at
the next run. It is a derived summary, not an audit record: rows may be
replaced, and nothing points at them.

Side (R5, until ops confirm the NPCI direction labels):
  - Raw Data / Merchant Raw Data / BBP: ACQUIRER or ISSUER, from the file
    (each side has its own file and FileTypeConfig);
  - CBS GL extract: DEBIT / CREDIT / UNKNOWN, the fact's direction;
  - DRC, Timeout, Adjustment (one file for both sides): ALL.
approved / declined: NPCI transaction files only (response code 00 / RB =
approved, anything else declined); NULL for every other file type.
"""
from __future__ import annotations

from django.db import models

from core.models import PaymentModule


class VolumeSide(models.TextChoices):
    ACQUIRER = "ACQUIRER", "Acquirer"
    ISSUER = "ISSUER", "Issuer"
    DEBIT = "DEBIT", "Debit"
    CREDIT = "CREDIT", "Credit"
    UNKNOWN = "UNKNOWN", "Direction unknown"
    ALL = "ALL", "All rows"


class DailyVolume(models.Model):
    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="+")
    business_date = models.DateField()
    file_type_code = models.CharField(max_length=50, help_text="FileTypeConfig.file_type_code")
    side = models.CharField(max_length=10, choices=VolumeSide.choices)
    cycle_code = models.CharField(max_length=10, blank=True, help_text='"" for files that are not cycle-wise')
    row_count = models.PositiveIntegerField()
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    approved_count = models.PositiveIntegerField(null=True, blank=True)
    declined_count = models.PositiveIntegerField(null=True, blank=True)
    computed_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["payment_module", "business_date", "file_type_code", "side", "cycle_code"],
                                    name="unique_daily_volume_row"),
        ]
        indexes = [models.Index(fields=["payment_module", "business_date"], name="dailyvolume_module_date_idx")]
        ordering = ["payment_module", "business_date", "file_type_code", "side", "cycle_code"]

    def __str__(self) -> str:
        return f"{self.payment_module_id} {self.business_date} {self.file_type_code} {self.side} {self.cycle_code}"


class DailyVolumeDate(models.Model):
    """Marks a module + business date as summarised (even when it had no
    transaction files), with the time, so the report can tell a date with
    no figures yet, or with files changed since, from an empty one."""

    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="+")
    business_date = models.DateField()
    computed_at = models.DateTimeField()
    source = models.CharField(max_length=150, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["payment_module", "business_date"], name="unique_daily_volume_date"),
        ]
        ordering = ["payment_module", "business_date"]

    def __str__(self) -> str:
        return f"{self.payment_module_id} {self.business_date} @ {self.computed_at:%Y-%m-%d %H:%M}"
