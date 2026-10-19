"""
`file_processing` app models: the File Configuration Repository
(`FileTypeConfig`), file intake/lifecycle tracking (`UploadedFile`), and the
spec's "File Validation Exception Queue" (`FileValidationException`).

Field lists per the approved plan (Phase 1 §2). Transaction storage lives
in the separate `transactions` app (per-file-type staging tables ->
`TransactionFact` -> history tables, schema redesign approved 2026-09-23 --
see `transactions/models/`), populated by `file_processing.tasks`'
`load_to_staging` and `promote_to_fact` stages.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models import PaymentModule, RegisterType, SettlementCycle


class ContainerFormat(models.TextChoices):
    """The outer wrapper (if any) a source file must be unwrapped from
    before its real payload format can be parsed."""

    NONE = "NONE", "None"
    ZIP = "ZIP", "Zip"


class PayloadFormat(models.TextChoices):
    """
    The real format of a file's payload once unwrapped -- independent of
    its declared extension. Confirmed directly against sample data: CBS's
    `.xls` is genuine SpreadsheetML XML, and NTSL's `.xls` is a genuine
    OOXML zip -- extensions cannot be trusted (see parsers/sniff.py).
    """

    SPREADSHEETML_XML = "SPREADSHEETML_XML", "SpreadsheetML XML"
    OOXML_ZIP = "OOXML_ZIP", "OOXML Zip"
    CSV = "CSV", "CSV"
    UNKNOWN = "UNKNOWN", "Unknown"


class DateValidationMode(models.TextChoices):
    """
    EXACT_MATCH: the whole file corresponds to exactly one business date
    (DRC/Timeout/Adjustment/Raw Data/Merchant Raw Data/NTSL).

    RANGE_CONTAINS: the file's own business-date field is a range (CBS GL
    Extract: "From 13/07 To 15/07" for a file tied to business date 14/07)
    -- for these, row-level filtering at normalize_rows keeps only rows
    whose own transaction date equals the selected business_date; rows
    elsewhere in the range are in-scope-file but out-of-scope-date, and are
    excluded from the load rather than treated as errors (resolved with the
    project owner -- see approved plan).
    """

    EXACT_MATCH = "EXACT_MATCH", "Exact match"
    RANGE_CONTAINS = "RANGE_CONTAINS", "Range contains (drives row-level date filtering)"


class FileTypeConfig(models.Model):
    """
    File Configuration Repository: a DB table (admin-editable/versioned,
    mirroring the spec's Rule Repository pattern for the Business Rule
    Engine) describing how to recognize, unwrap, parse, validate, and
    normalize each source file type.
    """

    payment_module = models.ForeignKey(
        PaymentModule, on_delete=models.PROTECT, related_name="file_type_configs"
    )
    file_type_code = models.CharField(
        max_length=50,
        help_text="e.g. CBS_GL_EXTRACT, RAW_DATA, MERCHANT_RAW_DATA, NTSL, DRC, TIMEOUT, ADJUSTMENT",
    )
    register_type = models.CharField(max_length=30, choices=RegisterType.choices)

    is_cycle_wise = models.BooleanField(default=False)
    cycle_set = models.JSONField(
        blank=True,
        default=list,
        help_text=(
            "Applicable SettlementCycle.cycle_code values when is_cycle_wise=True, "
            "e.g. ['1C', ..., '10C', 'DC1', 'DC2']"
        ),
    )

    container_format = models.CharField(
        max_length=20, choices=ContainerFormat.choices, default=ContainerFormat.NONE
    )
    inner_file_pattern = models.CharField(
        max_length=255,
        blank=True,
        help_text="Glob/regex identifying which member to extract from a zip container",
    )
    payload_format = models.CharField(max_length=30, choices=PayloadFormat.choices)

    # Date format varies per nesting level, not just per file type -- e.g.
    # the outer zip filename uses YYMMDD while the inner payload filename
    # inside that same zip uses DDMMYY (confirmed finding, approved plan).
    outer_filename_pattern = models.CharField(max_length=255, blank=True)
    outer_date_format = models.CharField(
        max_length=30, blank=True, help_text="strptime format for the outer filename's date, e.g. %y%m%d"
    )
    inner_filename_pattern = models.CharField(max_length=255, blank=True)
    inner_date_format = models.CharField(
        max_length=30, blank=True, help_text="strptime format for the inner filename's date, e.g. %d%m%y"
    )

    delimiter = models.CharField(max_length=5, blank=True, default=",")
    encoding = models.CharField(max_length=20, default="utf-8")
    has_header = models.BooleanField(
        default=True,
        help_text=(
            "CSV-payload types only (ignored by the SpreadsheetML/OOXML parsers): whether "
            "row 1 is a real column-header row to skip (DRC/Timeout/Adjustment) vs. a "
            "headerless file whose first row is actual data (Raw Data/Merchant Raw Data/"
            "Merchant Raw Data BBP -- their 'HT' metadata row and trailing 'FT' footer row "
            "are stripped via pagination_artifact_patterns instead, not treated as a header)"
        ),
    )

    column_mapping = models.JSONField(
        default=dict,
        blank=True,
        help_text="Canonical field name -> source column identifier (header name or cell index)",
    )
    required_columns = models.JSONField(
        default=list, blank=True, help_text="Canonical field names that must resolve on every row"
    )

    # RRN extraction rule.
    rrn_source_field = models.CharField(max_length=100, blank=True)
    rrn_extraction_regex = models.CharField(
        max_length=255,
        blank=True,
        help_text=r"e.g. CBS remarks pattern ^(By|To)\s+UPI/([^/]+)/",
    )
    rrn_position = models.PositiveIntegerField(null=True, blank=True)
    rrn_length = models.PositiveIntegerField(null=True, blank=True)
    rrn_expected_digit_length = models.PositiveIntegerField(default=12)

    amount_source_fields = models.JSONField(
        default=list,
        blank=True,
        help_text="e.g. ['Withdrawals', 'Deposits'] for CBS, which needs two amount columns",
    )
    amount_scale_factor = models.DecimalField(max_digits=12, decimal_places=6, default=1)
    direction_derivation_rule = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Free-form rule descriptor, e.g. "
            "'remarks_prefix:By=CREDIT,To=DEBIT' -- interpreted by the code that "
            "implements this specific rule (see file_processing/tasks.py normalize_rows); "
            "not a generic rule DSL"
        ),
    )

    pagination_artifact_patterns = models.JSONField(
        default=list,
        blank=True,
        help_text="Regexes matched against a row's joined cell text to strip header/footer/pagination rows",
    )

    date_validation_mode = models.CharField(
        max_length=20, choices=DateValidationMode.choices, default=DateValidationMode.EXACT_MATCH
    )

    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["payment_module", "file_type_code", "version"],
                name="unique_file_type_config_version",
            )
        ]
        ordering = ["payment_module", "file_type_code", "-version"]

    def __str__(self) -> str:
        return f"{self.payment_module.code}:{self.file_type_code} v{self.version}"


class ProcessingStatus(models.TextChoices):
    """Overall UploadedFile lifecycle status, one value per pipeline stage
    (approved plan §5)."""

    RECEIVED = "RECEIVED", "Received"
    PARSED = "PARSED", "Parsed"
    VALIDATED = "VALIDATED", "Validated"
    VALIDATION_FAILED = "VALIDATION_FAILED", "Validation Failed"
    STAGED = "STAGED", "Staged (raw rows written to the per-file-type staging table)"
    AWAITING_DATE_SELECTION = (
        "AWAITING_DATE_SELECTION",
        "Awaiting business date selection (RANGE_CONTAINS file types only)",
    )
    NORMALIZED = "NORMALIZED", "Normalized"
    LOADED = "LOADED", "Loaded"
    FAILED = "FAILED", "Failed"


class ParserStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    FORMAT_MISMATCH = "FORMAT_MISMATCH", "Detected format does not match FileTypeConfig.payload_format"
    PARSED_OK = "PARSED_OK", "Parsed"
    PARSE_FAILED = "PARSE_FAILED", "Parse Failed"


class UploadedFile(models.Model):
    """
    One record per received source file (including each reprocessed
    version -- see `is_reprocess`/`reprocess_of` below).
    """

    filename = models.CharField(max_length=255)
    path = models.CharField(max_length=1024, help_text="Storage path/URI of the current file")
    source_system = models.CharField(
        max_length=100, blank=True, help_text="e.g. 'NPCI SFTP', 'manual web upload'"
    )

    payment_module = models.ForeignKey(
        PaymentModule, on_delete=models.PROTECT, related_name="uploaded_files"
    )
    file_type_config = models.ForeignKey(
        FileTypeConfig, on_delete=models.PROTECT, related_name="uploaded_files"
    )

    business_date = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "Required before normalize_rows can run. For EXACT_MATCH file types this is "
            "typed in at creation as before. For RANGE_CONTAINS file types (e.g. CBS GL "
            "Extract) it is legitimately unknown until validate_structure_and_content has "
            "parsed the file and an admin picks one of `detected_dates` (see the "
            "'Select business date' admin action) -- not a blind guess entered up front."
        ),
    )
    detected_dates = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "RANGE_CONTAINS file types only: distinct row dates (ISO strings) found in the "
            "file, computed once by validate_structure_and_content so the admin can render "
            "choices without re-parsing. Unused for EXACT_MATCH file types."
        ),
    )
    batch_business_date = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "The business date the uploader chose for the upload batch (Phase 5 Amendment 1). "
            "EXACT_MATCH files get it as business_date at upload. RANGE_CONTAINS files (CBS) keep "
            "business_date empty until the date is confirmed; this is the date pre-selected then."
        ),
    )
    pending_pipeline_context = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Holds validate_structure_and_content's stage output (kept_rows, etc.) while "
            "processing is paused at AWAITING_DATE_SELECTION. Rows normally only travel "
            "between Celery stages as in-flight task return values (Redis-backed, not "
            "durable) -- not safe to rely on across a human-paced pause that could last "
            "minutes to days, hence persisting it here instead."
        ),
    )
    settlement_cycle = models.ForeignKey(
        SettlementCycle,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="uploaded_files",
        help_text="Null for non-cycle-wise file types",
    )

    declared_format = models.CharField(
        max_length=30, blank=True, help_text="Format implied by the file's extension/name, e.g. '.xls'"
    )
    detected_format = models.CharField(
        max_length=30,
        choices=PayloadFormat.choices,
        blank=True,
        help_text="Format actually sniffed from file content -- see parsers/sniff.py",
    )

    size_bytes = models.BigIntegerField()
    sha256_hash = models.CharField(max_length=64, db_index=True)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="uploaded_files",
        help_text=(
            "Django's built-in auth.User for now -- Maker-Checker roles "
            "(Administrator/Maker/Checker/Auditor) are an out-of-scope human "
            "design decision (Phase 3), not modeled here."
        ),
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    processing_status = models.CharField(
        max_length=30, choices=ProcessingStatus.choices, default=ProcessingStatus.RECEIVED
    )
    parser_status = models.CharField(
        max_length=30, choices=ParserStatus.choices, default=ParserStatus.PENDING
    )

    valid_record_count = models.PositiveIntegerField(default=0)
    rejected_record_count = models.PositiveIntegerField(default=0)

    file_version = models.PositiveIntegerField(default=1)
    is_reprocess = models.BooleanField(default=False)
    reprocess_of = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="reprocessed_by"
    )
    reprocess_reason = models.TextField(blank=True)
    is_current = models.BooleanField(
        default=True,
        help_text="Flips to False only once a reprocessing run reaches LOADED (per approved plan §5)",
    )

    class Meta:
        indexes = [
            models.Index(fields=["sha256_hash"]),
            models.Index(fields=["payment_module", "file_type_config", "business_date"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["sha256_hash"],
                condition=models.Q(is_reprocess=False),
                name="unique_sha256_hash_for_non_reprocess_uploads",
            ),
        ]
        ordering = ["-uploaded_at"]

    def __str__(self) -> str:
        return f"{self.filename} ({self.processing_status})"


class ValidationSeverity(models.TextChoices):
    INFO = "INFO", "Info"
    WARNING = "WARNING", "Warning"
    ERROR = "ERROR", "Error"


class FileValidationException(models.Model):
    """The spec's "File Validation Exception Queue"."""

    uploaded_file = models.ForeignKey(
        UploadedFile, on_delete=models.CASCADE, related_name="validation_exceptions"
    )
    rule_code = models.CharField(max_length=100)
    severity = models.CharField(
        max_length=10, choices=ValidationSeverity.choices, default=ValidationSeverity.ERROR
    )
    message = models.TextField()
    row_reference = models.CharField(
        max_length=100,
        blank=True,
        help_text="Row number / natural key within the source file this exception applies to, if any",
    )
    detected_at = models.DateTimeField(auto_now_add=True)
    resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-detected_at"]

    def __str__(self) -> str:
        return f"{self.rule_code} on UploadedFile#{self.uploaded_file_id}"
