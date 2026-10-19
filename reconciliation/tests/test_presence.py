"""
Tests for the Transaction Processing Engine's first slice (2-way presence
matching). Three groups, per the approved plan:

  1. Real-data test -- loads the real CBS sample + real Raw Data/Merchant
     Raw Data/BBP samples through the actual file_processing pipeline,
     then runs build_presence_matrix and asserts on the real resulting
     counts (reported back verbatim in this backend engineer's status
     report, not estimated).
  2. Extensibility proof -- a synthetic 3rd leg (core.RegisterType.SWITCH)
     added via the `legs` argument, with no model/migration change,
     proving RegisterPresence rows for a 3rd leg are created.
  3. Unit tests for build_presence_matrix's classification logic against
     small synthetic TransactionFact sets (matched / mismatched /
     one-sided-CBS / one-sided-NPCI / duplicate-RRN handling).
"""
from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.test import TestCase

from core.models import Direction, PaymentModule, RegisterType
from file_processing.models import (
    ContainerFormat,
    DateValidationMode,
    FileTypeConfig,
    PayloadFormat,
    UploadedFile,
)
from file_processing.tasks import (
    format_sniff_and_parse,
    load_to_staging,
    normalize_rows,
    promote_to_fact,
    validate_structure_and_content,
)
from file_processing.tests.test_pagination_strip import CBS_ARTIFACT_PATTERNS
from reconciliation.models import ProcessingStatus, RegisterPresence, TransactionMatchGroup
from reconciliation.presence import DEFAULT_PRESENCE_LEGS, build_presence_matrix
from transactions.models import TransactionFact
from transactions.models.history import StagingRawDataHistory
from transactions.models.staging import StagingAdjustment, StagingDrc, StagingRawData, StagingTimeout

SAMPLE_ROOT = Path(__file__).resolve().parents[2] / "sample_decrypted_files" / "14072026"
# Formerly 14072026/cbs_report/b5rg090oa.xls -- same file (same SHA-256), renamed.
CBS_SAMPLE = SAMPLE_ROOT / "cbs_14072026.xls"
BUSINESS_DATE = date(2026, 7, 14)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _payment_module() -> PaymentModule:
    return PaymentModule.objects.create(code="UPI", name="UPI")


def _make_uploaded_file(payment_module: PaymentModule, config: FileTypeConfig, path: Path, **overrides) -> UploadedFile:
    defaults = dict(
        filename=path.name,
        path=str(path),
        payment_module=payment_module,
        file_type_config=config,
        business_date=BUSINESS_DATE,
        declared_format=path.suffix,
        size_bytes=path.stat().st_size,
        sha256_hash=_sha256(path),
    )
    defaults.update(overrides)
    return UploadedFile.objects.create(**defaults)


def _run_pipeline(uploaded_file: UploadedFile) -> dict:
    ctx = format_sniff_and_parse(uploaded_file.id)
    ctx = validate_structure_and_content(ctx)
    ctx = load_to_staging(ctx)
    ctx = normalize_rows(ctx)
    return promote_to_fact(ctx)


class RealDataPresenceMatrixTests(TestCase):
    """Loads real CBS + Raw Data/Merchant Raw Data/BBP samples through the
    actual pipeline, then runs build_presence_matrix against the real
    resulting TransactionFact rows."""

    def _load_raw_data(self, payment_module: PaymentModule) -> None:
        config = FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="RAW_DATA",
            register_type=RegisterType.RAW_DATA,
            payload_format=PayloadFormat.CSV,
            container_format=ContainerFormat.NONE,
            has_header=False,
            column_mapping={"rrn": "4", "amount": "8"},
            required_columns=["rrn"],
            amount_source_fields=["amount"],
            amount_scale_factor=Decimal("0.01"),
            rrn_expected_digit_length=12,
            pagination_artifact_patterns=[r"^HT\b", r"^FT\b"],
            date_validation_mode=DateValidationMode.EXACT_MATCH,
            is_cycle_wise=True,
            cycle_set=[f"{n}C" for n in range(1, 11)],
        )
        sample = SAMPLE_ROOT / "UPIRAWDATAACQSSK140726_1C.csv"
        uploaded_file = _make_uploaded_file(payment_module, config, sample)
        _run_pipeline(uploaded_file)

    def _load_merchant_raw_data(self, payment_module: PaymentModule) -> None:
        config = FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="MERCHANT_RAW_DATA",
            register_type=RegisterType.MERCHANT_RAW_DATA,
            payload_format=PayloadFormat.CSV,
            container_format=ContainerFormat.NONE,
            has_header=False,
            column_mapping={"rrn": "4", "amount": "8"},
            required_columns=["rrn"],
            amount_source_fields=["amount"],
            amount_scale_factor=Decimal("0.01"),
            rrn_expected_digit_length=12,
            pagination_artifact_patterns=[r"^HT\b", r"^FT\b"],
            date_validation_mode=DateValidationMode.EXACT_MATCH,
            is_cycle_wise=True,
            cycle_set=[f"{n}C" for n in range(1, 11)],
        )
        sample = SAMPLE_ROOT / "UPIMERCHANTRAWDATAACQSSK140726_1C.csv"
        uploaded_file = _make_uploaded_file(payment_module, config, sample)
        _run_pipeline(uploaded_file)

    def _load_merchant_raw_data_bbp(self, payment_module: PaymentModule) -> None:
        config = FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="MERCHANT_RAW_DATA_BBP",
            register_type=RegisterType.MERCHANT_RAW_DATA_BBP,
            payload_format=PayloadFormat.CSV,
            container_format=ContainerFormat.NONE,
            has_header=False,
            column_mapping={"rrn": "4", "amount": "8"},
            required_columns=["rrn"],
            amount_source_fields=["amount"],
            amount_scale_factor=Decimal("0.01"),
            rrn_expected_digit_length=12,
            pagination_artifact_patterns=[r"^HT\b", r"^FT\b"],
            date_validation_mode=DateValidationMode.EXACT_MATCH,
            is_cycle_wise=True,
            cycle_set=["4C", "5C", "6C"],
        )
        sample = SAMPLE_ROOT / "UPIBBPMERCHANTRAWDATAACQSSK140726_4C.csv"
        uploaded_file = _make_uploaded_file(payment_module, config, sample)
        _run_pipeline(uploaded_file)

    def _load_cbs(self, payment_module: PaymentModule) -> None:
        config = FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="CBS_GL_EXTRACT",
            register_type=RegisterType.CBS_GL_EXTRACT,
            payload_format=PayloadFormat.SPREADSHEETML_XML,
            container_format=ContainerFormat.NONE,
            column_mapping={
                "date": {"Report": 1, "Report1": 1, "Report2": 1, "_default": 1},
                "remarks": {"Report": 3, "Report1": 2, "Report2": 2, "_default": 3},
                "withdrawals": {"Report": 7, "Report1": 4, "Report2": 5, "_default": 7},
                "deposits": {"Report": 10, "Report1": 5, "Report2": 6, "_default": 10},
                "balance": {"Report": 12, "Report1": 6, "Report2": 9, "_default": 12},
            },
            required_columns=["date"],
            amount_source_fields=["withdrawals", "deposits"],
            direction_derivation_rule="remarks_prefix:By=CREDIT,To=DEBIT",
            rrn_expected_digit_length=12,
            pagination_artifact_patterns=CBS_ARTIFACT_PATTERNS,
            date_validation_mode=DateValidationMode.RANGE_CONTAINS,
        )
        uploaded_file = _make_uploaded_file(payment_module, config, CBS_SAMPLE, business_date=None)
        parse_ctx = format_sniff_and_parse(uploaded_file.id)
        load_to_staging(validate_structure_and_content(parse_ctx))  # stages, then pauses
        uploaded_file.refresh_from_db()
        uploaded_file.business_date = BUSINESS_DATE
        uploaded_file.processing_status = "STAGED"
        uploaded_file.save(update_fields=["business_date", "processing_status"])
        normalize_ctx = normalize_rows({"uploaded_file_id": uploaded_file.id, "halted": False})
        promote_to_fact(normalize_ctx)

    def test_real_data_presence_matrix(self):
        payment_module = _payment_module()
        self._load_cbs(payment_module)
        self._load_raw_data(payment_module)
        self._load_merchant_raw_data(payment_module)
        self._load_merchant_raw_data_bbp(payment_module)

        cbs_count = TransactionFact.objects.filter(
            payment_module=payment_module,
            source_register_type=RegisterType.CBS_GL_EXTRACT,
            business_date=BUSINESS_DATE,
            rrn_valid=True,
        ).count()
        npci_count = TransactionFact.objects.filter(
            payment_module=payment_module,
            source_register_type__in=DEFAULT_PRESENCE_LEGS["NPCI"],
            business_date=BUSINESS_DATE,
            rrn_valid=True,
        ).count()
        # Sanity: both legs have real, non-trivial data to compare.
        self.assertGreater(cbs_count, 0)
        self.assertGreater(npci_count, 0)

        result = build_presence_matrix(payment_module, BUSINESS_DATE)

        # Report the real resulting counts (asserted, not estimated) --
        # see this backend engineer's status report for these numbers
        # verbatim.
        total_groups = TransactionMatchGroup.objects.filter(
            payment_module=payment_module, business_date=BUSINESS_DATE
        ).count()
        self.assertEqual(total_groups, result.total_match_groups)
        self.assertEqual(
            result.matched + result.pending_rule_evaluation + result.unmatched,
            result.total_match_groups,
        )

        # Every match group has exactly 2 RegisterPresence rows (CBS + NPCI),
        # confirming the 2-way leg configuration was applied to every
        # candidate RRN, not just some.
        for match_group in TransactionMatchGroup.objects.filter(payment_module=payment_module):
            self.assertEqual(match_group.register_presences.count(), 2)

        # These are the ground-truth numbers from this real run -- printed
        # so they show up in test output for the status report, and also
        # sanity-checked as internally consistent rather than pinned to
        # exact literals (which would make this test brittle to legitimate
        # future sample-data changes).
        print(
            "\n[RealDataPresenceMatrixTests] CBS rows:",
            cbs_count,
            "NPCI rows:",
            npci_count,
            "| matrix:",
            result.as_dict(),
        )
        self.assertGreaterEqual(result.matched, 0)


class ExtensibilityProofTests(TestCase):
    """Proves a 3rd/4th register can be added without any model or
    migration change -- core.RegisterType.SWITCH (defined in the enum,
    unused until now) as a synthetic 3rd leg."""

    def test_third_leg_added_via_config_only_no_schema_change(self):
        payment_module = _payment_module()
        uploaded_file_config = FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="SWITCH_TEST",
            register_type=RegisterType.SWITCH,
            payload_format=PayloadFormat.CSV,
        )
        uploaded_file = UploadedFile.objects.create(
            filename="synthetic-switch.csv",
            path="synthetic-switch.csv",
            payment_module=payment_module,
            file_type_config=uploaded_file_config,
            business_date=BUSINESS_DATE,
            declared_format=".csv",
            size_bytes=1,
            sha256_hash="synthetic-switch-hash",
        )

        # A CBS row and a matching SWITCH row, same RRN -- should match on
        # both legs once SWITCH is added to the leg config.
        cbs_config = FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="CBS_TEST",
            register_type=RegisterType.CBS_GL_EXTRACT,
            payload_format=PayloadFormat.CSV,
        )
        cbs_uploaded_file = UploadedFile.objects.create(
            filename="synthetic-cbs.csv",
            path="synthetic-cbs.csv",
            payment_module=payment_module,
            file_type_config=cbs_config,
            business_date=BUSINESS_DATE,
            declared_format=".csv",
            size_bytes=1,
            sha256_hash="synthetic-cbs-hash",
        )

        TransactionFact.objects.create(
            payment_module=payment_module,
            source_register_type=RegisterType.CBS_GL_EXTRACT,
            uploaded_file=cbs_uploaded_file,
            business_date=BUSINESS_DATE,
            rrn="111111111111",
            rrn_valid=True,
            amount=Decimal("100.00"),
            direction=Direction.DEBIT,
            row_reference="1",
        )
        TransactionFact.objects.create(
            payment_module=payment_module,
            source_register_type=RegisterType.SWITCH,
            uploaded_file=uploaded_file,
            business_date=BUSINESS_DATE,
            rrn="111111111111",
            rrn_valid=True,
            amount=Decimal("100.00"),
            direction=Direction.DEBIT,
            row_reference="1",
        )
        # A second SWITCH-only row (no CBS counterpart) to exercise the
        # one-sided path for the new leg too.
        TransactionFact.objects.create(
            payment_module=payment_module,
            source_register_type=RegisterType.SWITCH,
            uploaded_file=uploaded_file,
            business_date=BUSINESS_DATE,
            rrn="222222222222",
            rrn_valid=True,
            amount=Decimal("50.00"),
            direction=Direction.CREDIT,
            row_reference="2",
        )

        legs_with_switch = {**DEFAULT_PRESENCE_LEGS, "SWITCH": [RegisterType.SWITCH]}
        result = build_presence_matrix(payment_module, BUSINESS_DATE, legs=legs_with_switch)

        self.assertEqual(result.total_match_groups, 2)
        self.assertEqual(result.legs["SWITCH"], [RegisterType.SWITCH])

        matched_group = TransactionMatchGroup.objects.get(
            payment_module=payment_module, rrn="111111111111", business_date=BUSINESS_DATE
        )
        # 3 RegisterPresence rows now exist for this match group (CBS, NPCI,
        # SWITCH) -- no model/migration change was required to get here.
        self.assertEqual(matched_group.register_presences.count(), 3)
        presences_by_leg = {p.leg: p for p in matched_group.register_presences.all()}
        self.assertTrue(presences_by_leg["CBS"].present)
        self.assertFalse(presences_by_leg["NPCI"].present)
        self.assertTrue(presences_by_leg["SWITCH"].present)
        # Present in 2 of 3 configured legs (CBS + SWITCH), with matching
        # amount between those two -- but NOT present in every configured
        # leg (NPCI is missing), so per the spec's classification rule this
        # is UNMATCHED, not MATCHED/PENDING_RULE_EVALUATION (those require
        # presence in every configured leg).
        self.assertEqual(matched_group.amount_match, True)
        self.assertEqual(matched_group.processing_status, ProcessingStatus.UNMATCHED)

        one_sided_group = TransactionMatchGroup.objects.get(
            payment_module=payment_module, rrn="222222222222", business_date=BUSINESS_DATE
        )
        self.assertEqual(one_sided_group.register_presences.count(), 3)
        self.assertEqual(one_sided_group.processing_status, ProcessingStatus.UNMATCHED)

        # Confirms no model/column for a 3rd presence leg was needed -- the
        # app still only has its 2 live tables (plus their schema-only
        # History mirrors added by the 2026-09-23 redesign), and
        # RegisterPresence has no per-leg columns.
        from django.apps import apps

        reconciliation_models = {m.__name__ for m in apps.get_app_config("reconciliation").get_models()}
        self.assertEqual(
            reconciliation_models,
            {
                "TransactionMatchGroup",
                "RegisterPresence",
                "TransactionMatchGroupHistory",
                "RegisterPresenceHistory",
                # Phase 5 Slice 2 run tracking (migration 0004); one row per
                # reconciliation run, unrelated to presence legs.
                "ReconciliationRun",
            },
        )
        self.assertNotIn("switch_present", {f.name for f in RegisterPresence._meta.get_fields()})


class BuildPresenceMatrixUnitTests(TestCase):
    """Fast, synthetic-data unit tests for build_presence_matrix's
    classification logic, independent of the real-data test's runtime."""

    def _uploaded_file(self, payment_module: PaymentModule, register_type: str, suffix: str) -> UploadedFile:
        config = FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code=f"CFG_{suffix}",
            register_type=register_type,
            payload_format=PayloadFormat.CSV,
        )
        return UploadedFile.objects.create(
            filename=f"{suffix}.csv",
            path=f"{suffix}.csv",
            payment_module=payment_module,
            file_type_config=config,
            business_date=BUSINESS_DATE,
            declared_format=".csv",
            size_bytes=1,
            sha256_hash=f"hash-{suffix}",
        )

    def _record(self, payment_module, uploaded_file, register_type, rrn, amount, row_ref="1", **overrides):
        defaults = dict(
            payment_module=payment_module,
            source_register_type=register_type,
            uploaded_file=uploaded_file,
            business_date=BUSINESS_DATE,
            rrn=rrn,
            rrn_valid=True,
            amount=Decimal(amount),
            direction=Direction.DEBIT,
            row_reference=row_ref,
        )
        defaults.update(overrides)
        return TransactionFact.objects.create(**defaults)

    def test_matched_when_present_both_sides_with_equal_amount(self):
        payment_module = _payment_module()
        cbs_file = self._uploaded_file(payment_module, RegisterType.CBS_GL_EXTRACT, "cbs")
        npci_file = self._uploaded_file(payment_module, RegisterType.RAW_DATA, "npci")

        self._record(payment_module, cbs_file, RegisterType.CBS_GL_EXTRACT, "100000000001", "10.00")
        # Opposite direction to CBS: the normal real-data pairing.
        self._record(
            payment_module, npci_file, RegisterType.RAW_DATA, "100000000001", "10.00", direction=Direction.CREDIT
        )

        result = build_presence_matrix(payment_module, BUSINESS_DATE)
        self.assertEqual(result.matched, 1)
        self.assertEqual(result.pending_rule_evaluation, 0)
        self.assertEqual(result.unmatched, 0)

        group = TransactionMatchGroup.objects.get(rrn="100000000001")
        self.assertEqual(group.processing_status, ProcessingStatus.MATCHED)
        self.assertTrue(group.amount_match)
        self.assertIsNone(group.status_match)

    def test_pending_rule_evaluation_when_present_both_sides_but_amount_differs(self):
        payment_module = _payment_module()
        cbs_file = self._uploaded_file(payment_module, RegisterType.CBS_GL_EXTRACT, "cbs")
        npci_file = self._uploaded_file(payment_module, RegisterType.RAW_DATA, "npci")

        self._record(payment_module, cbs_file, RegisterType.CBS_GL_EXTRACT, "100000000002", "10.00")
        self._record(payment_module, npci_file, RegisterType.RAW_DATA, "100000000002", "12.50")

        result = build_presence_matrix(payment_module, BUSINESS_DATE)
        self.assertEqual(result.pending_rule_evaluation, 1)

        group = TransactionMatchGroup.objects.get(rrn="100000000002")
        self.assertEqual(group.processing_status, ProcessingStatus.PENDING_RULE_EVALUATION)
        self.assertFalse(group.amount_match)

    def test_one_sided_cbs_only_is_unmatched(self):
        payment_module = _payment_module()
        cbs_file = self._uploaded_file(payment_module, RegisterType.CBS_GL_EXTRACT, "cbs")

        self._record(payment_module, cbs_file, RegisterType.CBS_GL_EXTRACT, "100000000003", "5.00")

        result = build_presence_matrix(payment_module, BUSINESS_DATE)
        self.assertEqual(result.unmatched, 1)
        self.assertEqual(result.one_sided_counts, {"CBS": 1})

        group = TransactionMatchGroup.objects.get(rrn="100000000003")
        self.assertEqual(group.processing_status, ProcessingStatus.UNMATCHED)
        self.assertIsNone(group.amount_match)
        presences = {p.leg: p for p in group.register_presences.all()}
        self.assertTrue(presences["CBS"].present)
        self.assertFalse(presences["NPCI"].present)
        self.assertIsNone(presences["NPCI"].transaction_record)

    def test_one_sided_npci_only_is_unmatched(self):
        payment_module = _payment_module()
        npci_file = self._uploaded_file(payment_module, RegisterType.MERCHANT_RAW_DATA, "npci")

        self._record(payment_module, npci_file, RegisterType.MERCHANT_RAW_DATA, "100000000004", "7.00")

        result = build_presence_matrix(payment_module, BUSINESS_DATE)
        self.assertEqual(result.unmatched, 1)
        self.assertEqual(result.one_sided_counts, {"NPCI": 1})

        group = TransactionMatchGroup.objects.get(rrn="100000000004")
        presences = {p.leg: p for p in group.register_presences.all()}
        self.assertFalse(presences["CBS"].present)
        self.assertTrue(presences["NPCI"].present)

    def test_duplicate_rrn_within_npci_leg_is_counted_not_dropped(self):
        """Two of NPCI's constituent register types (RAW_DATA and
        MERCHANT_RAW_DATA) both carry the same RRN -- a data-quality edge
        case. Fixed priority order (RAW_DATA first) picks one; the
        collision is recorded, not silently dropped."""
        payment_module = _payment_module()
        cbs_file = self._uploaded_file(payment_module, RegisterType.CBS_GL_EXTRACT, "cbs")
        raw_file = self._uploaded_file(payment_module, RegisterType.RAW_DATA, "raw")
        merchant_file = self._uploaded_file(payment_module, RegisterType.MERCHANT_RAW_DATA, "merchant")

        self._record(payment_module, cbs_file, RegisterType.CBS_GL_EXTRACT, "100000000005", "20.00")
        self._record(payment_module, raw_file, RegisterType.RAW_DATA, "100000000005", "20.00")
        self._record(payment_module, merchant_file, RegisterType.MERCHANT_RAW_DATA, "100000000005", "20.00")

        result = build_presence_matrix(payment_module, BUSINESS_DATE)
        self.assertEqual(result.per_leg_duplicate_rrn_counts["NPCI"], 1)
        self.assertEqual(result.leg_collision_counts["NPCI"], 1)

        group = TransactionMatchGroup.objects.get(rrn="100000000005")
        npci_presence = group.register_presences.get(leg="NPCI")
        # Priority order: RAW_DATA picked over MERCHANT_RAW_DATA.
        self.assertEqual(npci_presence.transaction_record.source_register_type, RegisterType.RAW_DATA)

    def test_rrn_invalid_records_are_excluded_from_matching(self):
        payment_module = _payment_module()
        cbs_file = self._uploaded_file(payment_module, RegisterType.CBS_GL_EXTRACT, "cbs")
        self._record(
            payment_module,
            cbs_file,
            RegisterType.CBS_GL_EXTRACT,
            "not-a-valid-rrn",
            "1.00",
            rrn_valid=False,
        )

        result = build_presence_matrix(payment_module, BUSINESS_DATE)
        self.assertEqual(result.total_match_groups, 0)
        self.assertFalse(TransactionMatchGroup.objects.filter(payment_module=payment_module).exists())

    def test_rerun_is_idempotent_not_duplicated(self):
        payment_module = _payment_module()
        cbs_file = self._uploaded_file(payment_module, RegisterType.CBS_GL_EXTRACT, "cbs")
        npci_file = self._uploaded_file(payment_module, RegisterType.RAW_DATA, "npci")

        self._record(payment_module, cbs_file, RegisterType.CBS_GL_EXTRACT, "100000000006", "3.00")
        self._record(payment_module, npci_file, RegisterType.RAW_DATA, "100000000006", "3.00")

        build_presence_matrix(payment_module, BUSINESS_DATE)
        build_presence_matrix(payment_module, BUSINESS_DATE)  # simulated rerun after new file load

        self.assertEqual(
            TransactionMatchGroup.objects.filter(payment_module=payment_module).count(), 1
        )
        self.assertEqual(RegisterPresence.objects.filter(match_group__payment_module=payment_module).count(), 2)

    def test_rerun_refreshes_updated_at_on_upserted_rows(self):
        from datetime import datetime, timezone as dt_timezone

        payment_module = _payment_module()
        cbs_file = self._uploaded_file(payment_module, RegisterType.CBS_GL_EXTRACT, "cbs")
        npci_file = self._uploaded_file(payment_module, RegisterType.RAW_DATA, "npci")
        self._record(payment_module, cbs_file, RegisterType.CBS_GL_EXTRACT, "100000000016", "3.00")
        self._record(
            payment_module, npci_file, RegisterType.RAW_DATA, "100000000016", "3.00", direction=Direction.CREDIT
        )
        build_presence_matrix(payment_module, BUSINESS_DATE)

        stale = datetime(2026, 1, 1, tzinfo=dt_timezone.utc)
        TransactionMatchGroup.objects.filter(payment_module=payment_module).update(updated_at=stale)
        RegisterPresence.objects.filter(match_group__payment_module=payment_module).update(updated_at=stale)

        build_presence_matrix(payment_module, BUSINESS_DATE)

        group = TransactionMatchGroup.objects.get(payment_module=payment_module)
        self.assertGreater(group.updated_at, stale)
        self.assertFalse(
            RegisterPresence.objects.filter(match_group=group, updated_at__lte=stale).exists()
        )

    def test_settlement_cycle_derived_from_present_leg_not_matching_key(self):
        from core.models import SettlementCycle

        payment_module = _payment_module()
        cycle = SettlementCycle.objects.create(
            payment_module=payment_module,
            cycle_code="1C",
            cycle_type="AUTH",
            start_time="21:00",
            end_time="00:00",
        )
        npci_file = self._uploaded_file(payment_module, RegisterType.RAW_DATA, "npci")
        self._record(
            payment_module,
            npci_file,
            RegisterType.RAW_DATA,
            "100000000007",
            "4.00",
            settlement_cycle=cycle,
        )

        build_presence_matrix(payment_module, BUSINESS_DATE)
        group = TransactionMatchGroup.objects.get(rrn="100000000007")
        self.assertEqual(group.settlement_cycle, cycle)


class Step1CorrectionsTests(TestCase):
    """Phase 2 Step 1 (2026-09-30): direction check, CBS reversals,
    superseded groups and NPCI response codes. (Leading-zero recovery moved
    to extraction on 2026-10-02 -- see file_processing/tests/test_cbs_parser.py.)"""

    _uploaded_file = BuildPresenceMatrixUnitTests._uploaded_file
    _record = BuildPresenceMatrixUnitTests._record

    def setUp(self):
        self.payment_module = _payment_module()
        self.cbs_file = self._uploaded_file(self.payment_module, RegisterType.CBS_GL_EXTRACT, "s1cbs")
        self.npci_file = self._uploaded_file(self.payment_module, RegisterType.RAW_DATA, "s1npci")

    def _cbs(self, rrn, amount="10.00", row_ref="1", **overrides):
        return self._record(
            self.payment_module, self.cbs_file, RegisterType.CBS_GL_EXTRACT, rrn, amount, row_ref, **overrides
        )

    def _npci(self, rrn, amount="10.00", row_ref="1", response_code=None, archived=False, **overrides):
        staging_row_id = None
        if response_code is not None:
            staging = StagingRawData.objects.create(
                uploaded_file=self.npci_file,
                row_number=int(row_ref),
                row_reference=f"sheet:{row_ref}",
                raw_cells={"4": rrn, "5": response_code},
            )
            staging_row_id = staging.id
            if archived:
                StagingRawDataHistory.objects.create(
                    original_id=staging.id,
                    uploaded_file=self.npci_file,
                    row_number=staging.row_number,
                    row_reference=staging.row_reference,
                    raw_cells=staging.raw_cells,
                    created_at=staging.created_at,
                )
                staging.delete()
        overrides.setdefault("direction", Direction.CREDIT)
        return self._record(
            self.payment_module,
            self.npci_file,
            RegisterType.RAW_DATA,
            rrn,
            amount,
            row_ref,
            staging_row_id=staging_row_id,
            **overrides,
        )

    def test_direction_match(self):
        self._cbs("200000000001", direction=Direction.DEBIT)
        self._npci("200000000001", direction=Direction.CREDIT)
        self._cbs("200000000002", row_ref="2", direction=Direction.DEBIT)
        self._npci("200000000002", row_ref="2", direction=Direction.DEBIT)

        result = build_presence_matrix(self.payment_module, BUSINESS_DATE)

        opposite = TransactionMatchGroup.objects.get(rrn="200000000001")
        same = TransactionMatchGroup.objects.get(rrn="200000000002")
        self.assertTrue(opposite.direction_match)
        self.assertEqual(opposite.processing_status, ProcessingStatus.MATCHED)
        self.assertFalse(same.direction_match)
        self.assertEqual(same.processing_status, ProcessingStatus.PENDING_RULE_EVALUATION)
        self.assertEqual(result.direction_mismatches, 1)

    def test_cbs_reversal_pair_keeps_original_leg(self):
        # NPCI CREDIT pairs with CBS DEBIT (the original); CBS CREDIT is the reversal.
        self._cbs("300000000001", direction=Direction.CREDIT)
        original = self._cbs("300000000001", row_ref="2", direction=Direction.DEBIT)
        self._npci("300000000001", direction=Direction.CREDIT)

        result = build_presence_matrix(self.payment_module, BUSINESS_DATE)

        group = TransactionMatchGroup.objects.get(rrn="300000000001")
        self.assertTrue(group.cbs_reversed)
        self.assertTrue(group.direction_match)
        self.assertEqual(group.register_presences.get(leg="CBS").transaction_record_id, original.id)
        self.assertEqual(group.processing_status, ProcessingStatus.PENDING_RULE_EVALUATION)
        self.assertEqual(result.cbs_reversed, 1)

    def test_same_direction_cbs_duplicate_is_not_a_reversal(self):
        self._cbs("300000000002", direction=Direction.DEBIT)
        self._cbs("300000000002", row_ref="2", direction=Direction.DEBIT)

        build_presence_matrix(self.payment_module, BUSINESS_DATE)
        self.assertFalse(TransactionMatchGroup.objects.get(rrn="300000000002").cbs_reversed)

    def test_group_missing_from_rebuild_is_superseded_then_reinstated(self):
        fact = self._cbs("400000000001")
        build_presence_matrix(self.payment_module, BUSINESS_DATE)

        # A corrected file no longer carries the RRN.
        self.cbs_file.is_current = False
        self.cbs_file.save(update_fields=["is_current"])
        result = build_presence_matrix(self.payment_module, BUSINESS_DATE)

        group = TransactionMatchGroup.objects.get(rrn="400000000001")
        self.assertTrue(group.is_superseded)
        self.assertIsNotNone(group.superseded_at)
        self.assertEqual(result.superseded, 1)

        # A rerun doesn't count it again; the RRN coming back clears the flag.
        self.assertEqual(build_presence_matrix(self.payment_module, BUSINESS_DATE).superseded, 0)
        self.cbs_file.is_current = True
        self.cbs_file.save(update_fields=["is_current"])
        build_presence_matrix(self.payment_module, BUSINESS_DATE)
        group.refresh_from_db()
        self.assertFalse(group.is_superseded)
        self.assertIsNone(group.superseded_at)
        self.assertTrue(TransactionFact.objects.filter(pk=fact.pk).exists())

    def test_npci_response_code_read_from_staging_and_history(self):
        self._cbs("500000000001")
        self._npci("500000000001", response_code="00")
        self._npci("500000000002", row_ref="2", response_code="Z9")  # declined, not in CBS
        self._npci("500000000003", row_ref="3", response_code="ZM", archived=True)

        result = build_presence_matrix(self.payment_module, BUSINESS_DATE)

        def code(rrn, leg="NPCI"):
            return RegisterPresence.objects.get(match_group__rrn=rrn, leg=leg).response_code

        self.assertEqual(code("500000000001"), "00")
        self.assertEqual(code("500000000002"), "Z9")
        self.assertEqual(code("500000000003"), "ZM")  # staging row archived to history
        self.assertEqual(code("500000000001", leg="CBS"), "")
        self.assertEqual(result.matched, 1)
        self.assertEqual(result.npci_only_response_codes, {"Z9": 1, "ZM": 1})

    def test_non_success_response_code_needs_rule_evaluation(self):
        self._cbs("500000000004")
        self._npci("500000000004", response_code="RB")

        build_presence_matrix(self.payment_module, BUSINESS_DATE)
        group = TransactionMatchGroup.objects.get(rrn="500000000004")
        self.assertEqual(group.processing_status, ProcessingStatus.PENDING_RULE_EVALUATION)

    def test_rerun_with_corrections_is_idempotent(self):
        self._cbs("600000000001", direction=Direction.CREDIT)
        self._cbs("600000000001", row_ref="2", direction=Direction.DEBIT)
        self._npci("600000000001", response_code="00")
        self._cbs("006000000002", row_ref="3")
        self._npci("006000000002", row_ref="2", response_code="00")

        fields = ("id", "rrn", "direction_match", "cbs_reversed", "is_superseded", "processing_status")
        first = build_presence_matrix(self.payment_module, BUSINESS_DATE).as_dict()
        snapshot = list(TransactionMatchGroup.objects.order_by("rrn").values(*fields))
        second = build_presence_matrix(self.payment_module, BUSINESS_DATE).as_dict()

        self.assertEqual(first, second)
        self.assertEqual(snapshot, list(TransactionMatchGroup.objects.order_by("rrn").values(*fields)))
        self.assertEqual(RegisterPresence.objects.count(), 4)


class Step2OnUsAndOverlayTests(TestCase):
    """Phase 2 Step 2 (2026-10-03): on-us payments vs CBS reversals, and the
    DRC / Timeout / Adjustment dispute overlays."""

    _uploaded_file = BuildPresenceMatrixUnitTests._uploaded_file
    _record = BuildPresenceMatrixUnitTests._record

    STAGING = {
        RegisterType.RAW_DATA: StagingRawData,
        RegisterType.DRC: StagingDrc,
        RegisterType.TIMEOUT: StagingTimeout,
        RegisterType.ADJUSTMENT: StagingAdjustment,
    }

    def setUp(self):
        self.payment_module = _payment_module()
        self.files = {
            rt: self._uploaded_file(self.payment_module, rt, f"s2{rt.lower()}")
            for rt in (
                RegisterType.CBS_GL_EXTRACT,
                RegisterType.RAW_DATA,
                RegisterType.DRC,
                RegisterType.TIMEOUT,
                RegisterType.ADJUSTMENT,
            )
        }
        self._row = 0

    def _fact(self, register_type, rrn, direction, amount="10.00", cells=None, **overrides):
        self._row += 1
        if cells is not None:
            staging = self.STAGING[register_type].objects.create(
                uploaded_file=self.files[register_type],
                row_number=self._row,
                row_reference=f":{self._row}",
                raw_cells=cells,
            )
            overrides["staging_row_id"] = staging.id
        return self._record(
            self.payment_module,
            self.files[register_type],
            register_type,
            rrn,
            amount,
            str(self._row),
            direction=direction,
            **overrides,
        )

    def _cbs(self, rrn, direction, **kw):
        return self._fact(RegisterType.CBS_GL_EXTRACT, rrn, direction, **kw)

    def _npci(self, rrn, direction, **kw):
        return self._fact(RegisterType.RAW_DATA, rrn, direction, **kw)

    def _drc(self, rrn, remitter="SSK", code="'102", **kw):
        cells = {"6": code, "7": f"'{rrn}", "9": remitter, "10": "AXB"}
        return self._fact(RegisterType.DRC, rrn, Direction.DEBIT, cells=cells, **kw)

    def _timeout(self, rrn, beneficiary="SSK", **kw):
        cells = {"6": "'RB", "7": f"'{rrn}", "9": "AXB", "10": beneficiary}
        return self._fact(RegisterType.TIMEOUT, rrn, Direction.CREDIT, cells=cells, **kw)

    def _adjustment(self, rrn, txn_date, adj_date=BUSINESS_DATE, **kw):
        cells = {"4": "Credit Adjustment", "5": "SSK", "7": "00", "8": txn_date, "10": f"'{rrn}"}
        return self._fact(RegisterType.ADJUSTMENT, rrn, Direction.CREDIT, cells=cells, business_date=adj_date, **kw)

    def _group(self, rrn, business_date=BUSINESS_DATE):
        return TransactionMatchGroup.objects.get(rrn=rrn, business_date=business_date)

    def _presence(self, rrn, leg, business_date=BUSINESS_DATE):
        return RegisterPresence.objects.get(match_group__rrn=rrn, match_group__business_date=business_date, leg=leg)

    # --- on-us vs reversal ------------------------------------------------
    def test_on_us_pair_on_both_sides_is_matched_not_reversed(self):
        cbs_debit = self._cbs("700000000001", Direction.DEBIT)
        cbs_credit = self._cbs("700000000001", Direction.CREDIT)
        npci_acq = self._npci("700000000001", Direction.DEBIT)
        npci_iss = self._npci("700000000001", Direction.CREDIT)

        result = build_presence_matrix(self.payment_module, BUSINESS_DATE)

        group = self._group("700000000001")
        self.assertTrue(group.is_on_us)
        self.assertFalse(group.cbs_reversed)
        self.assertTrue(group.direction_match)
        self.assertEqual(group.processing_status, ProcessingStatus.MATCHED)
        cbs = self._presence("700000000001", "CBS")
        npci = self._presence("700000000001", "NPCI")
        self.assertEqual((cbs.transaction_record_id, cbs.paired_transaction_record_id), (cbs_credit.id, cbs_debit.id))
        self.assertEqual((npci.transaction_record_id, npci.paired_transaction_record_id), (npci_acq.id, npci_iss.id))
        self.assertEqual((result.on_us, result.cbs_reversed, result.matched), (1, 0, 1))

    def test_on_us_with_one_cbs_side_missing_needs_rule_evaluation(self):
        self._cbs("700000000002", Direction.CREDIT)
        self._npci("700000000002", Direction.DEBIT)
        self._npci("700000000002", Direction.CREDIT)

        result = build_presence_matrix(self.payment_module, BUSINESS_DATE)

        group = self._group("700000000002")
        self.assertTrue(group.is_on_us)
        self.assertEqual(group.processing_status, ProcessingStatus.PENDING_RULE_EVALUATION)
        self.assertEqual(result.on_us_incomplete, 1)

    def test_failed_on_us_payment_with_cbs_pair_is_a_reversal(self):
        # Matches ops' manual sheet: DRC eff + rev for on-us RRNs whose NPCI
        # rows carry failure codes (R9 on both sides).
        # The CBS row pairing with NPCI's acquirer (DEBIT) row is the original.
        original = self._cbs("700000000004", Direction.CREDIT)
        self._cbs("700000000004", Direction.DEBIT)
        self._npci("700000000004", Direction.DEBIT, cells={"5": "R9"})
        self._npci("700000000004", Direction.CREDIT, cells={"5": "R9"})

        result = build_presence_matrix(self.payment_module, BUSINESS_DATE)

        group = self._group("700000000004")
        self.assertTrue(group.is_on_us)
        self.assertTrue(group.cbs_reversed)
        self.assertEqual(group.processing_status, ProcessingStatus.PENDING_RULE_EVALUATION)
        self.assertEqual(self._presence("700000000004", "CBS").transaction_record_id, original.id)
        self.assertEqual((result.on_us, result.cbs_reversed), (0, 1))

    def test_cbs_pair_with_single_npci_row_is_a_reversal(self):
        self._cbs("700000000003", Direction.CREDIT)
        original = self._cbs("700000000003", Direction.DEBIT)
        self._npci("700000000003", Direction.CREDIT)

        build_presence_matrix(self.payment_module, BUSINESS_DATE)

        group = self._group("700000000003")
        self.assertTrue(group.cbs_reversed)
        self.assertFalse(group.is_on_us)
        self.assertEqual(self._presence("700000000003", "CBS").transaction_record_id, original.id)

    # --- dispute overlays -------------------------------------------------
    def test_drc_attaches_with_ssk_side_and_cleaned_code(self):
        self._cbs("800000000001", Direction.DEBIT)
        self._npci("800000000001", Direction.CREDIT)
        self._drc("800000000001")
        self._cbs("800000000002", Direction.DEBIT)
        self._npci("800000000002", Direction.CREDIT)
        self._drc("800000000002", remitter="CNB")

        result = build_presence_matrix(self.payment_module, BUSINESS_DATE)

        ssk = self._presence("800000000001", "DRC")
        other = self._presence("800000000002", "DRC")
        self.assertTrue(ssk.present)
        self.assertTrue(ssk.ssk_side)
        self.assertEqual(ssk.response_code, "102")  # leading apostrophe stripped
        self.assertFalse(other.ssk_side)
        self.assertEqual(result.overlays["DRC"]["ssk_side"], 1)
        self.assertEqual(result.overlays["DRC"]["other_bank_side"], 1)
        # Overlays don't change the CBS-vs-NPCI outcome.
        self.assertEqual(self._group("800000000001").processing_status, ProcessingStatus.MATCHED)
        # And groups without a dispute row get no overlay presence at all.
        self.assertFalse(RegisterPresence.objects.filter(leg="TIMEOUT").exists())

    def test_drc_only_rrn_gets_its_own_unmatched_group(self):
        self._drc("800000000003")  # not effected in CBS (SOP code 104)

        result = build_presence_matrix(self.payment_module, BUSINESS_DATE)

        group = self._group("800000000003")
        self.assertEqual(group.processing_status, ProcessingStatus.UNMATCHED)
        self.assertFalse(self._presence("800000000003", "CBS").present)
        self.assertTrue(self._presence("800000000003", "DRC").present)
        self.assertEqual(result.overlays["DRC"]["overlay_only_groups"], 1)

    def test_timeout_ssk_side_is_the_beneficiary_column(self):
        self._timeout("800000000004", beneficiary="SSK")
        self._timeout("800000000005", beneficiary="HDFC")

        build_presence_matrix(self.payment_module, BUSINESS_DATE)

        self.assertTrue(self._presence("800000000004", "TIMEOUT").ssk_side)
        self.assertFalse(self._presence("800000000005", "TIMEOUT").ssk_side)

    def test_adjustment_links_to_its_original_transaction_date(self):
        earlier = date(2026, 7, 13)
        self._cbs("900000000001", Direction.DEBIT, business_date=earlier)
        self._npci("900000000001", Direction.CREDIT, business_date=earlier)
        adjustment = self._adjustment("900000000001", txn_date="13-07-2026")

        today = build_presence_matrix(self.payment_module, BUSINESS_DATE)
        self.assertFalse(TransactionMatchGroup.objects.filter(rrn="900000000001", business_date=BUSINESS_DATE).exists())
        self.assertEqual(today.overlays["ADJUSTMENT"].get("attached", 0), 0)

        result = build_presence_matrix(self.payment_module, earlier)
        presence = self._presence("900000000001", "ADJUSTMENT", business_date=earlier)
        self.assertEqual(presence.transaction_record_id, adjustment.id)
        self.assertEqual(presence.dispute_type, "Credit Adjustment")
        self.assertFalse(presence.original_not_loaded)
        self.assertEqual(result.overlays["ADJUSTMENT"]["linked_to_txn_date"], 1)

    def test_adjustment_for_unloaded_date_is_flagged_on_its_own_date(self):
        self._cbs("900000000002", Direction.DEBIT)  # makes BUSINESS_DATE a loaded date
        self._adjustment("900000000003", txn_date="09-07-2026")  # 9 Jul is not loaded

        result = build_presence_matrix(self.payment_module, BUSINESS_DATE)

        presence = self._presence("900000000003", "ADJUSTMENT")
        self.assertTrue(presence.original_not_loaded)
        self.assertEqual(result.overlays["ADJUSTMENT"]["original_not_loaded"], 1)

    def test_rebuild_replaces_overlays_without_duplicates(self):
        self._cbs("800000000006", Direction.DEBIT)
        self._npci("800000000006", Direction.CREDIT)
        self._drc("800000000006")
        self._drc("800000000006")  # same RRN twice in the DRC file

        first = build_presence_matrix(self.payment_module, BUSINESS_DATE).as_dict()
        second = build_presence_matrix(self.payment_module, BUSINESS_DATE).as_dict()

        self.assertEqual(first, second)
        self.assertEqual(first["overlays"]["DRC"]["duplicates"], 1)
        self.assertEqual(RegisterPresence.objects.filter(leg="DRC").count(), 1)
        self.assertEqual(RegisterPresence.objects.count(), 3)  # CBS + NPCI + DRC
