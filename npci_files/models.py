"""
`npci_files` app: NPCI File Generation (Phase 4 component, un-deferred
2026-10-22). Design: docs/npci_file_design_draft_v1.txt, approved by the
project owner 2026-10-22/23 with Amendment 1; Amendment 2 (owner 2026-10-24):
the file is built right after reconciliation from the reply each case carries
now (the Maker's submitted or approved reply, else the rule's suggestion).

  NpciFile     one generated upload file (e.g. SSKUPI14072026.csv), with its
               exact bytes, hash and counts. The first file of a date is
               sequence 1; a later file for the same date (sequence 2, 3...,
               named ..._2.csv, ..._3.csv) holds ONLY cases that appeared or
               became eligible since the earlier files (Amendments 1 and 2).
               Files are never changed, replaced or deleted.
  NpciFileRow  one line of a file: which case, which reply (and the Maker's
               action, if the reply was the Maker's) it came from. A case appears in at most one row of all files (DB
               constraint), so a case is never included in two files.
               (The app never sends anything to NPCI: the user downloads
               the file and uploads it to the portal by hand.)

Both tables are append-only (core.append_only): rows may be inserted, never
changed or deleted through the ORM. Every FK is PROTECT.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from core.append_only import AppendOnlyError, AppendOnlyModel
from core.models import PaymentModule
from exception_management.models import ExceptionCase, ProposedAction


class ReplySource(models.TextChoices):
    """Where a row's reply (flag + code) came from (Amendment 2, owner 2026-10-24)."""

    RULE = "RULE", "The rule's suggested reply"
    SUBMITTED = "SUBMITTED", "The Maker's submitted reply (not yet approved)"
    APPROVED = "APPROVED", "The Checker-approved reply"


class NpciFileImmutableError(AppendOnlyError):
    """A generated NPCI file (or one of its rows) was about to change."""


class NpciFile(AppendOnlyModel):
    append_only_error = NpciFileImmutableError

    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="npci_files")
    file_date = models.DateField(help_text="The date in the file name (the business date the file is for)")
    sequence = models.PositiveSmallIntegerField(
        help_text="1 = the date's first file; 2, 3... = later files holding only cases not in an earlier file"
    )
    file_format = models.CharField(max_length=20, help_text="e.g. SSKUPI (npci_files.formats)")
    file_name = models.CharField(max_length=64)
    content = models.TextField(help_text="The exact file text, CRLF line endings (what the download sends)")
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField()
    row_count = models.PositiveIntegerField()
    total_amount = models.DecimalField(max_digits=18, decimal_places=2)
    counts = models.JSONField(default=list, blank=True, help_text="[{flag, code, text, rows, amount}]")
    partial = models.BooleanField(
        default=False, help_text="Some cases for this date were not included (user confirmed)"
    )
    not_included = models.JSONField(
        default=list, blank=True,
        help_text="Cases left out at generation: [{case_id, rrn_masked, flag, code, reason, detail}]",
    )
    warnings = models.JSONField(default=list, blank=True)
    remarks = models.TextField(blank=True)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    generated_by_username = models.CharField(max_length=150)
    generated_at = models.DateTimeField()

    class Meta:
        ordering = ["-generated_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["payment_module", "file_date", "sequence"], name="npci_file_seq_per_date"),
            models.UniqueConstraint(fields=["payment_module", "file_name"], name="npci_file_name_unique"),
        ]
        indexes = [models.Index(fields=["payment_module", "file_date"], name="npci_file_module_date_idx")]

    def __str__(self) -> str:
        return f"{self.file_name} ({self.row_count} rows)"


class NpciFileRow(AppendOnlyModel):
    append_only_error = NpciFileImmutableError

    npci_file = models.ForeignKey(NpciFile, on_delete=models.PROTECT, related_name="rows")
    line = models.PositiveIntegerField(help_text="1 = the first line after the header")
    case = models.ForeignKey(ExceptionCase, on_delete=models.PROTECT, related_name="npci_file_rows")
    action = models.ForeignKey(
        ProposedAction, on_delete=models.PROTECT, related_name="npci_file_rows", null=True, blank=True,
        help_text="The Maker's action the reply came from; empty when it was the rule's suggested reply",
    )
    action_type = models.CharField(max_length=40, blank=True, help_text="exception_management ActionType")
    reply_source = models.CharField(max_length=10, choices=ReplySource.choices, blank=True)
    case_business_date = models.DateField()
    rrn = models.CharField(max_length=20)
    flag = models.CharField(max_length=10)
    code = models.CharField(max_length=10)
    shtdat = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    account_masked = models.CharField(max_length=40, help_text="Last 4 characters only; the full value is in content")
    text = models.CharField(max_length=255, help_text="specifyother: the code's description when generated")
    dispute_fact_id = models.BigIntegerField(help_text="The NPCI DRC / Timeout / Adjustment TransactionFact used")

    class Meta:
        ordering = ["npci_file", "line"]
        constraints = [
            models.UniqueConstraint(fields=["case"], name="npci_case_sent_once"),
            models.UniqueConstraint(fields=["npci_file", "line"], name="npci_file_line_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.npci_file_id}:{self.line} {self.flag} {self.code}"
