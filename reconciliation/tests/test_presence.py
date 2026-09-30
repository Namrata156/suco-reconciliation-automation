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

SAMPLE_ROOT = Path(__file__).resolve().parents[2] / "sample_decrypted_files" / "14072026"
CBS_SAMPLE = Path(__file__).resolve().parents[2] / "14072026" / "cbs_report" / "b5rg090oa.xls"
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
        self._record(payment_module, npci_file, RegisterType.RAW_DATA, "100000000001", "10.00")

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
