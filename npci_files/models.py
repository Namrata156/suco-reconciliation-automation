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

  CreditVoucher / CreditVoucherRow / CreditVoucherApproval /
  CreditVoucherCredit  the CBS credit file ("rev" file, owner 2026-11-11):
               the generated file and its rows, the checked file a Checker
               uploaded (= the approval) and each credit it approved. See
               npci_files.credit_voucher.

All tables are append-only (core.append_only): rows may be inserted, never
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


# ---------------------------------------------------------------------------
# CBS credit file ("rev" file), owner 2026-11-11 (npci_files.credit_voucher)
# ---------------------------------------------------------------------------
class CreditVoucherImmutableError(AppendOnlyError):
    """A generated credit file, its rows or its approval was about to change."""


class CreditVoucherStatus(models.TextChoices):
    """Derived, never stored (the tables are append-only): APPROVED once a
    CreditVoucherApproval exists; SUPERSEDED when a later version of the same
    date exists and this one was not approved; else GENERATED."""

    GENERATED = "GENERATED", "Generated, waiting for the checked file"
    APPROVED = "APPROVED", "Approved (checked file uploaded)"
    SUPERSEDED = "SUPERSEDED", "Superseded by a later version"


class CreditVoucher(AppendOnlyModel):
    """One generated CBS credit file for a date (e.g. UPIREV02112026.csv; a
    later version UPIREV02112026_2.csv). A new version supersedes an earlier
    one that was not approved; one that was approved stays approved and the
    new version holds only cases not credited yet."""

    append_only_error = CreditVoucherImmutableError

    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="credit_vouchers")
    voucher_date = models.DateField(help_text="The business date the file is for (the date in the file name)")
    version = models.PositiveSmallIntegerField(help_text="1 = the date's first file; 2, 3... = later versions")
    file_name = models.CharField(max_length=64)
    content = models.TextField(help_text="The exact file text (what the download sends)")
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField()
    debit_account = models.CharField(max_length=40, help_text="The GL account the first row debits")
    narration = models.CharField(max_length=255, help_text="The debit row's narration")
    row_count = models.PositiveIntegerField(help_text="Credit rows (the debit row not counted)")
    total_amount = models.DecimalField(max_digits=18, decimal_places=2)
    rows_to_check = models.PositiveIntegerField(default=0, help_text="Rows with a 'Check' note (account missing...)")
    counts = models.JSONField(default=list, blank=True, help_text="[{rule_code, rows, amount}]")
    not_included = models.JSONField(default=list, blank=True, help_text="Cases of the money rules left out, and why")
    warnings = models.JSONField(default=list, blank=True)
    remarks = models.TextField(blank=True)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    generated_by_username = models.CharField(max_length=150)
    generated_at = models.DateTimeField()

    class Meta:
        ordering = ["-generated_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["payment_module", "voucher_date", "version"],
                                    name="credit_voucher_version_per_date"),
            models.UniqueConstraint(fields=["payment_module", "file_name"], name="credit_voucher_name_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.file_name} ({self.row_count} rows)"


class CreditVoucherRow(AppendOnlyModel):
    """One credit row of a generated file (line 1 = the first credit row)."""

    append_only_error = CreditVoucherImmutableError

    voucher = models.ForeignKey(CreditVoucher, on_delete=models.PROTECT, related_name="rows")
    line = models.PositiveIntegerField(help_text="Sr. No. in the file minus 1 (Sr. No. 1 is the debit row)")
    case = models.ForeignKey(ExceptionCase, on_delete=models.PROTECT, related_name="credit_voucher_rows")
    rule_code = models.CharField(max_length=10)
    action_type = models.CharField(max_length=40, help_text="The action approved on upload (ActionType)")
    rrn = models.CharField(max_length=20)
    txn_date = models.DateField(help_text="The date in the narration (Adjustment Txndate; DRC / Timeout TXN Date)")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    account = models.CharField(max_length=40, blank=True, help_text="As generated; empty = the user fills it in")
    check_note = models.CharField(max_length=255, blank=True, help_text="The 'Check' column")
    dispute_fact_id = models.BigIntegerField(help_text="The NPCI Adjustment / DRC / Timeout TransactionFact used")

    class Meta:
        ordering = ["voucher", "line"]
        constraints = [
            models.UniqueConstraint(fields=["voucher", "line"], name="credit_voucher_line_unique"),
            models.UniqueConstraint(fields=["voucher", "case"], name="credit_voucher_case_once_per_file"),
        ]

    def __str__(self) -> str:
        return f"{self.voucher_id}:{self.line} {self.rrn} {self.amount}"


class CreditVoucherApproval(AppendOnlyModel):
    """The checked file a Checker uploaded for a voucher: the approval of
    every case it lists (owner 2026-11-11). At most one per voucher."""

    append_only_error = CreditVoucherImmutableError

    voucher = models.OneToOneField(CreditVoucher, on_delete=models.PROTECT, related_name="approval")
    uploaded_file_name = models.CharField(max_length=255)
    uploaded_content = models.TextField(help_text="The uploaded file as text, kept as the evidence")
    uploaded_sha256 = models.CharField(max_length=64)
    approved_count = models.PositiveIntegerField()
    removed_count = models.PositiveIntegerField(help_text="Rows of the voucher left out of the uploaded file")
    total_amount = models.DecimalField(max_digits=18, decimal_places=2)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    approved_by_username = models.CharField(max_length=150)
    approved_at = models.DateTimeField()

    def __str__(self) -> str:
        return f"{self.voucher.file_name} approved by {self.approved_by_username}"


class CreditVoucherCredit(AppendOnlyModel):
    """One credit approved through an uploaded file, with the account as
    uploaded. A case, and an RRN, is credited at most once through the
    credit file (DB constraints): no double credit across vouchers or dates."""

    append_only_error = CreditVoucherImmutableError

    approval = models.ForeignKey(CreditVoucherApproval, on_delete=models.PROTECT, related_name="credits")
    voucher_row = models.OneToOneField(CreditVoucherRow, on_delete=models.PROTECT, related_name="credit")
    case = models.OneToOneField(ExceptionCase, on_delete=models.PROTECT, related_name="credit_voucher_credit")
    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="+")
    rrn = models.CharField(max_length=20)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    account = models.CharField(max_length=40, help_text="The account as uploaded (16 digits)")
    account_changed = models.BooleanField(default=False, help_text="Differs from the generated file")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["payment_module", "rrn"], name="credit_voucher_rrn_credited_once"),
        ]

    def __str__(self) -> str:
        return f"{self.rrn} {self.amount}"
