"""
Seeds real `FileTypeConfig` rows for the six RRN-bearing UPI file types
the pipeline (`file_processing/tasks.py`: `load_to_staging` ->
`normalize_rows` -> `promote_to_fact`) loads into the per-file-type staging
tables and `transactions.TransactionFact` -- CBS GL Extract, Raw Data (ACQ/ISS),
Merchant Raw Data (ACQ/ISS), Merchant Raw Data BBP, DRC, Timeout, Adjustment
(9 configs across those 6 underlying file types) -- plus NTSL (added
2026-09-27), which is staged into `StagingNtsl` and promoted to
`transactions.NtslSettlementLine` by `promote_to_settlement_summary`
instead (settlement summary, no RRN -- see doubts.md). 10 configs total.

Deliberately a management command, not a data migration: `TestCase`-based
tests in this codebase create their own throwaway `PaymentModule(code="UPI")`
row per test (see `file_processing/tests/test_date_selection.py`'s
`_build_payment_module()` convention) and rely on the test database
starting with an empty `core_paymentmodule` table -- a data migration's
inserts run once against the test database's baseline schema and are NOT
rolled back between tests (only genuine per-test transactions are), so
they would collide with every test that creates its own "UPI" row. A
management command run explicitly (e.g. once per real deployment, or ad
hoc for manual pipeline verification against sample data) avoids that
collision entirely; test suites keep using their own local
`_build_*_config()` helpers, unaffected by this command's existence.

Idempotent: re-running only touches rows that don't already exist
(`get_or_create` on `(payment_module, file_type_code, version)`, the
same natural key `FileTypeConfig.Meta.constraints` enforces).

Raw Data and Merchant Raw Data are each split into two versioned rows
(same `file_type_code`/`register_type`, `version` 1=ACQ/2=ISS) rather than
one combined config -- resolved 2026-09-12 (see doubts.md's "Resolved"
section): direction depends on which file (ACQ vs. ISS) a row came from,
per the bank's SOP ("Issuer transactions are credit... Acquirer
transactions are debit"), so one shared config with a combined `(ACQ|ISS)`
filename pattern could never distinguish them. This is a config *value*
difference (narrower `outer_filename_pattern` + a different
`direction_derivation_rule`), not a new classification -- unlike Merchant
Raw Data BBP, which did need its own `RegisterType` (see that entry below).

Values below were validated directly against the real sample files under
`sample_decrypted_files/`/`14072026/` (see doubts.md's "Resolved" section
and the inline comments below for what each mapping is based on). Where a
mapping choice wasn't already resolved in doubts.md/TRACKER.md (Adjustment's
"which amount column"), that's called out explicitly rather than silently
invented -- see this backend engineer's handoff report for the full list of
flagged assumptions.

`outer_filename_pattern` values document the real observed filenames for
future use (e.g. a real upload API/UI auto-selecting FileTypeConfig by
filename) -- no code currently matches on these fields; UploadedFile.
file_type_config is still chosen manually (admin/tests) since the upload
API is a Phase 5 stub.
"""
from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand

from core.models import PaymentModule
from file_processing.models import FileTypeConfig

# Canonical reference list for CBS's pagination_artifact_patterns -- kept in
# sync with file_processing/tests/test_pagination_strip.py's
# CBS_ARTIFACT_PATTERNS (that module is the audited source of truth).
_CBS_ARTIFACT_PATTERNS = [
    r"^Suco Souharda Sahakari Bank Ltd",
    r"^Head Office",
    r"^Statement Of Office Account",
    r"^From\s+\d",
    r"^GL No\.",
    r"^Account NBR",
    r"^Opening Balance",
    r"^Date\s+Remarks",
    r"^Summation of Debits and Credit",
    r"^Printed By",
    r"^Pages\s*:",
]

# Raw Data / Merchant Raw Data / Merchant Raw Data BBP are headerless: row 1
# is an "HT" metadata line, the trailing row is an "FT" footer -- both
# stripped via these patterns (matched against the row's space-joined cell
# text, see pagination_strip.py, hence "\b" rather than a literal comma).
_HT_FT_ARTIFACT_PATTERNS = [r"^HT\b", r"^FT\b"]

_CYCLES_1_TO_10 = [f"{n}C" for n in range(1, 11)]

_CONFIGS = [
    dict(
        file_type_code="CBS_GL_EXTRACT",
        register_type="CBS_GL_EXTRACT",
        payload_format="SPREADSHEETML_XML",
        container_format="NONE",
        # Per-worksheet column_mapping (resolved by
        # file_processing.tasks._resolve_column_mapping): CBS's real
        # ~154,661-row report is split across 3 worksheets ("Report",
        # "Report1", "Report2") purely by Excel's row-count limit -- one
        # continuous report, not 3 separate reports -- but each worksheet
        # has its own stable ss:Index column layout. Confirmed by scanning
        # every row's ss:Index shape across the complete real file (not a
        # sample), cross-checked against which amount column is populated
        # on credit "By UPI" vs debit "To UPI" remarks rows:
        #   Report  (65,535 rows, dominant shape (1,3,6,7,10,12,13), 65,528
        #            occurrences): date=1, remarks=3, withdrawals=7,
        #            deposits=10, balance=12.
        #   Report1 (65,535 rows, dominant shape (1,2,3,4,5,6,7), 100%):
        #            date=1, remarks=2, withdrawals=4, deposits=5, balance=6.
        #   Report2 (23,591 rows, dominant shape (1,2,4,5,6,9,12), 23,589
        #            occurrences): date=1, remarks=2, withdrawals=5,
        #            deposits=6, balance=9.
        # "_default" falls back to Report's layout (the one this codebase's
        # pre-existing tests/config used before this per-worksheet form
        # existed) for any future worksheet name not among these three (e.g.
        # a hypothetical "Report3" from a still-larger report) -- not
        # otherwise confirmed against real data since only 3 worksheets
        # exist in the current sample.
        column_mapping={
            "date": {"Report": 1, "Report1": 1, "Report2": 1, "_default": 1},
            "remarks": {"Report": 3, "Report1": 2, "Report2": 2, "_default": 3},
            "withdrawals": {"Report": 7, "Report1": 4, "Report2": 5, "_default": 7},
            "deposits": {"Report": 10, "Report1": 5, "Report2": 6, "_default": 10},
            "balance": {"Report": 12, "Report1": 6, "Report2": 9, "_default": 12},
        },
        required_columns=["date"],
        amount_source_fields=["withdrawals", "deposits"],
        amount_scale_factor=Decimal("1"),
        direction_derivation_rule="remarks_prefix:By=CREDIT,To=DEBIT",
        rrn_expected_digit_length=12,
        pagination_artifact_patterns=_CBS_ARTIFACT_PATTERNS,
        date_validation_mode="RANGE_CONTAINS",
        is_cycle_wise=False,
    ),
    dict(
        file_type_code="RAW_DATA",
        register_type="RAW_DATA",
        version=1,
        payload_format="CSV",
        container_format="NONE",
        has_header=False,
        # Headerless TX rows: RRN at fixed field 4, amount (paise) at field 8 --
        # confirmed identical across the ACQ and ISS variants. Both variants
        # still fold into this one RegisterType.RAW_DATA classification (only
        # BBP gets its own RegisterType) -- but ACQ/ISS now get their own
        # FileTypeConfig row (version 1/2) so direction can be derived from
        # which file a row came from (resolved 2026-09-12, see doubts.md).
        column_mapping={"rrn": 4, "amount": 8},
        required_columns=["rrn"],
        amount_source_fields=["amount"],
        amount_scale_factor=Decimal("0.01"),
        direction_derivation_rule="constant:DEBIT",
        rrn_expected_digit_length=12,
        pagination_artifact_patterns=_HT_FT_ARTIFACT_PATTERNS,
        date_validation_mode="EXACT_MATCH",
        is_cycle_wise=True,
        cycle_set=_CYCLES_1_TO_10,
        outer_filename_pattern=r"^UPIRAWDATAACQSSK\d{6}_(?:[1-9]|10)C\.csv$",
    ),
    dict(
        file_type_code="RAW_DATA",
        register_type="RAW_DATA",
        version=2,
        payload_format="CSV",
        container_format="NONE",
        has_header=False,
        # Issuer variant of the same file type/RegisterType above -- same row
        # shape, narrowed filename pattern, CREDIT per the SOP ("Issuer
        # transactions are credit transaction to the bank customer").
        column_mapping={"rrn": 4, "amount": 8},
        required_columns=["rrn"],
        amount_source_fields=["amount"],
        amount_scale_factor=Decimal("0.01"),
        direction_derivation_rule="constant:CREDIT",
        rrn_expected_digit_length=12,
        pagination_artifact_patterns=_HT_FT_ARTIFACT_PATTERNS,
        date_validation_mode="EXACT_MATCH",
        is_cycle_wise=True,
        cycle_set=_CYCLES_1_TO_10,
        outer_filename_pattern=r"^UPIRAWDATAISSSSK\d{6}_(?:[1-9]|10)C\.csv$",
    ),
    dict(
        file_type_code="MERCHANT_RAW_DATA",
        register_type="MERCHANT_RAW_DATA",
        version=1,
        payload_format="CSV",
        container_format="NONE",
        has_header=False,
        # Same row shape as Raw Data (confirmed directly against samples).
        # Acquirer variant -- see RAW_DATA's version=1/2 comment above for
        # why this is split by version rather than one combined (ACQ|ISS)
        # pattern.
        column_mapping={"rrn": 4, "amount": 8},
        required_columns=["rrn"],
        amount_source_fields=["amount"],
        amount_scale_factor=Decimal("0.01"),
        direction_derivation_rule="constant:DEBIT",
        rrn_expected_digit_length=12,
        pagination_artifact_patterns=_HT_FT_ARTIFACT_PATTERNS,
        date_validation_mode="EXACT_MATCH",
        is_cycle_wise=True,
        cycle_set=_CYCLES_1_TO_10,
        outer_filename_pattern=r"^UPIMERCHANTRAWDATAACQSSK\d{6}_(?:[1-9]|10)C\.csv$",
    ),
    dict(
        file_type_code="MERCHANT_RAW_DATA",
        register_type="MERCHANT_RAW_DATA",
        version=2,
        payload_format="CSV",
        container_format="NONE",
        has_header=False,
        # Issuer variant -- see RAW_DATA's version=1/2 comment above.
        column_mapping={"rrn": 4, "amount": 8},
        required_columns=["rrn"],
        amount_source_fields=["amount"],
        amount_scale_factor=Decimal("0.01"),
        direction_derivation_rule="constant:CREDIT",
        rrn_expected_digit_length=12,
        pagination_artifact_patterns=_HT_FT_ARTIFACT_PATTERNS,
        date_validation_mode="EXACT_MATCH",
        is_cycle_wise=True,
        cycle_set=_CYCLES_1_TO_10,
        outer_filename_pattern=r"^UPIMERCHANTRAWDATAISSSSK\d{6}_(?:[1-9]|10)C\.csv$",
    ),
    dict(
        file_type_code="MERCHANT_RAW_DATA_BBP",
        register_type="MERCHANT_RAW_DATA_BBP",
        payload_format="CSV",
        container_format="NONE",
        has_header=False,
        # Project owner decision 2026-09-10 (see doubts.md): own classification,
        # identical row shape/parsing to standard Merchant Raw Data ACQ. ACQ-
        # only (no ISS variant exists for BBP), so a constant DEBIT rule needs
        # no filename split -- updated 2026-09-12 for consistency with the
        # ACQ/ISS `constant:` rule above (was unset/UNKNOWN before).
        column_mapping={"rrn": 4, "amount": 8},
        required_columns=["rrn"],
        amount_source_fields=["amount"],
        amount_scale_factor=Decimal("0.01"),
        direction_derivation_rule="constant:DEBIT",
        rrn_expected_digit_length=12,
        pagination_artifact_patterns=_HT_FT_ARTIFACT_PATTERNS,
        date_validation_mode="EXACT_MATCH",
        is_cycle_wise=True,
        # Project owner confirmed 2026-09-27: BBP can arrive in any of the 10
        # AUTH cycles (1C-10C). Previously limited to 4C/5C/6C, which was only
        # what the 2026-07-14 sample data happened to contain.
        cycle_set=_CYCLES_1_TO_10,
        outer_filename_pattern=r"^UPIBBPMERCHANTRAWDATAACQSSK\d{6}_(?:[1-9]|10)C\.csv$",
    ),
    dict(
        file_type_code="DRC",
        register_type="DRC",
        payload_format="CSV",
        container_format="NONE",
        has_header=True,
        # Named header row: RRN col 7, "TXN Date" col 3 (DD/MM/YYYY, confirmed
        # constant per file), Amount col 13 (plain rupee decimal, not paise).
        # Direction: confirmed by project owner (2026-09-16, direct chat
        # confirmation quoting the SOP): "DRC dispute file contains the
        # debit response failed transaction[s]... whether the transaction
        # is debited from customer and reversed online / need to reverse
        # it manually or else online transaction response failed" -- DRC
        # is inherently about debit-side disputes, so constant DEBIT.
        # NOTE: the online-reversed/needs-manual-reversal/response-failed
        # *status* itself (determined by comparing against CBS) is
        # exception-classification logic, not a direction rule -- not
        # implemented here; see doubts.md.
        column_mapping={"rrn": 7, "date": 3, "amount": 13},
        required_columns=["rrn"],
        amount_source_fields=["amount"],
        amount_scale_factor=Decimal("1"),
        direction_derivation_rule="constant:DEBIT",
        rrn_expected_digit_length=12,
        pagination_artifact_patterns=[],
        date_validation_mode="EXACT_MATCH",
        is_cycle_wise=False,
        outer_filename_pattern=r"^Debit Reversal Confirmation Report__SSK_\d{4}-\d{2}-\d{2}\.csv$",
    ),
    dict(
        file_type_code="TIMEOUT",
        register_type="TIMEOUT",
        payload_format="CSV",
        container_format="NONE",
        has_header=True,
        # Identical column layout to DRC (confirmed).
        # Direction: confirmed by project owner (2026-09-16, direct chat
        # confirmation quoting the SOP): "TIME OUT TRANSACTIONS [are]
        # credit dispute transactions[;] we need to check whether the
        # transaction is credit to customer" -- Timeout is inherently
        # about credit-side disputes, so constant CREDIT. NOTE: whether
        # the credit actually reached the customer is a separate
        # verification/exception-classification check, not implemented
        # here; see doubts.md.
        column_mapping={"rrn": 7, "date": 3, "amount": 13},
        required_columns=["rrn"],
        amount_source_fields=["amount"],
        amount_scale_factor=Decimal("1"),
        direction_derivation_rule="constant:CREDIT",
        rrn_expected_digit_length=12,
        pagination_artifact_patterns=[],
        date_validation_mode="EXACT_MATCH",
        is_cycle_wise=False,
        outer_filename_pattern=r"^UPI Time Out Cases Report_SSK_\d{4}-\d{2}-\d{2}\.csv$",
    ),
    dict(
        file_type_code="ADJUSTMENT",
        register_type="ADJUSTMENT",
        payload_format="CSV",
        container_format="NONE",
        has_header=True,
        # Adjdate (col 3, DD-MM-YYYY) is the canonical business date (resolved
        # 2026-09-09, see doubts.md). RRN col 10. "amount" = Adjamount (col
        # 17), this adjustment's own amount -- confirmed by the project owner
        # 2026-09-17 (see doubts.md). "txn_amount" = Txnamount (col 16, the
        # original transaction's amount) and "txn_date" = Txndate (col 8) are
        # contextual only: they fill StagingAdjustment.txn_amount_raw/
        # txn_date_raw and TransactionFact.txn_amount, never business_date or
        # amount. Added with the project owner's OK, 2026-09-27.
        # Direction: confirmed by project owner (2026-09-16, direct chat
        # confirmation quoting the SOP): "in this report all the returns
        # and adjustment records will be there[,] hence by default we
        # need to give credit to the customer" -- constant CREDIT.
        column_mapping={"rrn": 10, "date": 3, "amount": 17, "txn_amount": 16, "txn_date": 8},
        required_columns=["rrn", "date"],
        amount_source_fields=["amount"],
        direction_derivation_rule="constant:CREDIT",
        amount_scale_factor=Decimal("1"),
        rrn_expected_digit_length=12,
        pagination_artifact_patterns=[],
        date_validation_mode="EXACT_MATCH",
        is_cycle_wise=False,
        outer_filename_pattern=r"^UPI Adjustment Report_SSK_\d{4}-\d{2}-\d{2}\.csv$",
    ),
    dict(
        file_type_code="NTSL",
        register_type="NTSL",
        # NTSL's ".xls" is a genuine OOXML zip (see parsers/sniff.py) --
        # format_sniff_and_parse compares sniffed.payload.value against this.
        payload_format="OOXML_ZIP",
        container_format="NONE",
        has_header=False,
        # Settlement summary, NOT an RRN register (see doubts.md). Columns
        # confirmed against all 12 real files in 14072026/NTSL/: Description
        # | No of Txns | Debit | Credit (col 5 always empty). Added
        # 2026-09-27 (plan approved by the project owner that day). Staged
        # into StagingNtsl, then promoted to NtslSettlementLine by
        # promote_to_settlement_summary -- never normalize_rows/promote_to_fact,
        # so no RRN/amount/direction fields apply.
        column_mapping={"description": 1, "txn_count": 2, "debit": 3, "credit": 4},
        required_columns=[],
        amount_source_fields=[],
        amount_scale_factor=Decimal("1"),
        direction_derivation_rule="",
        # Staging keeps every parsed row (titles/header/blank rows included);
        # promote_to_settlement_summary decides which are category lines.
        pagination_artifact_patterns=[],
        date_validation_mode="EXACT_MATCH",
        is_cycle_wise=True,
        cycle_set=[*_CYCLES_1_TO_10, "DC1", "DC2"],
        outer_filename_pattern=r"^UPI_NTSLSSK\d{6}_(?:(?:[1-9]|10)C|DC[12])\.xls$",
    ),
]


class Command(BaseCommand):
    help = (
        "Seeds the 10 FileTypeConfig rows for the in-scope UPI file types: "
        "CBS GL Extract, Raw Data ACQ/ISS, Merchant Raw Data ACQ/ISS, "
        "Merchant Raw Data BBP, DRC, Timeout, Adjustment (-> TransactionFact) "
        "and NTSL (-> NtslSettlementLine). Idempotent -- safe to re-run; "
        "existing rows are never modified."
    )

    def handle(self, *args, **options):
        upi, _ = PaymentModule.objects.get_or_create(code="UPI", defaults={"name": "UPI"})

        created, existing = 0, 0
        for values in _CONFIGS:
            # "version" defaults to 1 (a plain single-config file type); Raw
            # Data / Merchant Raw Data set it explicitly (1=ACQ, 2=ISS) so
            # both variants coexist under the same file_type_code/register_type
            # -- see FileTypeConfig.Meta's unique_file_type_config_version
            # constraint (payment_module, file_type_code, version).
            version = values.get("version", 1)
            _, was_created = FileTypeConfig.objects.get_or_create(
                payment_module=upi,
                file_type_code=values["file_type_code"],
                version=version,
                defaults=values,
            )
            if was_created:
                created += 1
            else:
                existing += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"FileTypeConfig seed complete: {created} created, {existing} already present."
            )
        )
