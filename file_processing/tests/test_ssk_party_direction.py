"""
The per-row `ssk_party:` direction rule (2026-10-08, project owner approval
"option 1"): DRC and Timeout rows take their direction from the file's own
Remitter / Beneficiary columns instead of a whole-file constant. Extended
the same day (owner approval relayed by the coordinator) to Adjustment with
the side -> direction mapping mirrored. The real
sample-file counts are asserted in test_staging_and_fact.py's DrcLoadTests /
TimeoutLoadTests; here, synthetic rows cover each branch, plus the data
migration and the seed command agreeing on the rule.
"""
from __future__ import annotations

import importlib
from collections import Counter
from datetime import date
from decimal import Decimal

from django.apps import apps as django_apps
from django.test import SimpleTestCase, TestCase

from core.models import Direction, PaymentModule, RegisterType
from file_processing.management.commands.seed_file_type_configs import (
    _CONFIGS,
    ADJUSTMENT_SSK_PARTY_RULE,
    DISPUTE_SSK_PARTY_RULE,
)
from file_processing.models import (
    ContainerFormat,
    DateValidationMode,
    FileTypeConfig,
    FileValidationException,
    PayloadFormat,
    UploadedFile,
    ValidationSeverity,
)
from file_processing.tasks import _direction_from_ssk_party, _parse_ssk_party_rule, normalize_rows
from transactions.models import StagingDrc

migration_0007 = importlib.import_module("file_processing.migrations.0007_drc_timeout_ssk_party_direction")
migration_0008 = importlib.import_module("file_processing.migrations.0008_adjustment_ssk_party_direction")

_PARAMS = {
    "remitter_col": "9", "beneficiary_col": "10", "bank_code": "SSK",
    "remitter": Direction.DEBIT, "beneficiary": Direction.CREDIT,
}
_ADJ_PARAMS = {
    "remitter_col": "5", "beneficiary_col": "6", "bank_code": "SSK",
    "remitter": Direction.CREDIT, "beneficiary": Direction.DEBIT,
}


class ParseSskPartyRuleTests(SimpleTestCase):
    def test_parses_the_seeded_rule(self):
        self.assertEqual(_parse_ssk_party_rule(DISPUTE_SSK_PARTY_RULE), _PARAMS)

    def test_parses_the_mirrored_adjustment_rule(self):
        self.assertEqual(_parse_ssk_party_rule(ADJUSTMENT_SSK_PARTY_RULE), _ADJ_PARAMS)

    def test_mapping_keys_are_optional_and_case_insensitive(self):
        self.assertEqual(
            _parse_ssk_party_rule(
                "ssk_party:remitter_col=9,beneficiary_col=10,bank_code=SSK,remitter=debit,beneficiary=credit"
            ),
            _PARAMS,
        )

    def test_tolerates_whitespace(self):
        self.assertEqual(
            _parse_ssk_party_rule("ssk_party: remitter_col = 9, beneficiary_col=10 ,bank_code=SSK"), _PARAMS
        )

    def test_malformed_rules_raise(self):
        for rule in (
            "constant:DEBIT",
            "ssk_party:",
            "ssk_party:remitter_col=9,beneficiary_col=10",  # bank_code missing
            "ssk_party:remitter_col=9,beneficiary_col=10,bank_code=SSK,extra=1",
            "ssk_party:remitter_col9,beneficiary_col=10,bank_code=SSK",
            "ssk_party:remitter_col=,beneficiary_col=10,bank_code=SSK",
            "ssk_party:remitter_col=5,beneficiary_col=6,bank_code=SSK,remitter=REFUND",
            "ssk_party:remitter_col=5,beneficiary_col=6,bank_code=SSK,remitter=CREDIT",  # both CREDIT
            "ssk_party:remitter_col=5,beneficiary_col=6,bank_code=SSK,remitter=DEBIT,beneficiary=DEBIT",
        ):
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                _parse_ssk_party_rule(rule)


class DirectionFromSskPartyTests(SimpleTestCase):
    def test_remitter_ssk_is_debit(self):
        self.assertEqual(_direction_from_ssk_party({"9": "SSK", "10": "HDF"}, _PARAMS), Direction.DEBIT)

    def test_beneficiary_ssk_is_credit(self):
        self.assertEqual(_direction_from_ssk_party({"9": "ICI", "10": "SSK"}, _PARAMS), Direction.CREDIT)

    def test_neither_is_unknown(self):
        self.assertEqual(_direction_from_ssk_party({"9": "ICI", "10": "HDF"}, _PARAMS), Direction.UNKNOWN)
        self.assertEqual(_direction_from_ssk_party({}, _PARAMS), Direction.UNKNOWN)

    def test_remitter_wins_when_both_are_ssk(self):
        # On-us dispute rows: SSK's customer paid, so the remitter check runs first.
        self.assertEqual(_direction_from_ssk_party({"9": "SSK", "10": "SSK"}, _PARAMS), Direction.DEBIT)

    def test_cells_cleaned_like_presence_ssk_side(self):
        self.assertEqual(_direction_from_ssk_party({"9": " 'SSK ", "10": ""}, _PARAMS), Direction.DEBIT)
        self.assertEqual(_direction_from_ssk_party({"9": None, "10": "'SSK"}, _PARAMS), Direction.CREDIT)

    def test_match_is_exact_not_substring_or_case_folded(self):
        self.assertEqual(_direction_from_ssk_party({"9": "SSKB", "10": "ssk"}, _PARAMS), Direction.UNKNOWN)


class MirroredAdjustmentDirectionTests(SimpleTestCase):
    """Adjustment: money moves back to the payer, so the mapping is mirrored."""

    def test_remitter_ssk_is_credit(self):
        self.assertEqual(_direction_from_ssk_party({"5": "SSK", "6": "HDF"}, _ADJ_PARAMS), Direction.CREDIT)

    def test_beneficiary_ssk_is_debit(self):
        self.assertEqual(_direction_from_ssk_party({"5": "ICI", "6": "'SSK"}, _ADJ_PARAMS), Direction.DEBIT)

    def test_neither_is_unknown(self):
        self.assertEqual(_direction_from_ssk_party({"5": "ICI", "6": "HDF"}, _ADJ_PARAMS), Direction.UNKNOWN)

    def test_on_us_takes_remitter_direction(self):
        self.assertEqual(_direction_from_ssk_party({"5": "SSK", "6": "SSK"}, _ADJ_PARAMS), Direction.CREDIT)


class NormalizeRowsSskPartyTests(TestCase):
    def setUp(self):
        self.payment_module = PaymentModule.objects.create(code="UPI", name="UPI")
        self.config = FileTypeConfig.objects.create(
            payment_module=self.payment_module,
            file_type_code="DRC",
            register_type=RegisterType.DRC,
            payload_format=PayloadFormat.CSV,
            container_format=ContainerFormat.NONE,
            has_header=True,
            column_mapping={"rrn": "7", "date": "3", "amount": "13"},
            required_columns=["rrn"],
            amount_source_fields=["amount"],
            amount_scale_factor=Decimal("1"),
            direction_derivation_rule=DISPUTE_SSK_PARTY_RULE,
            rrn_expected_digit_length=12,
            date_validation_mode=DateValidationMode.EXACT_MATCH,
        )
        self.uploaded_file = UploadedFile.objects.create(
            filename="drc.csv",
            path="drc.csv",
            payment_module=self.payment_module,
            file_type_config=self.config,
            business_date=date(2026, 7, 14),
            declared_format=".csv",
            size_bytes=1,
            sha256_hash="ssk-party-hash",
        )

    def _stage(self, n, remitter, beneficiary):
        StagingDrc.objects.create(
            uploaded_file=self.uploaded_file,
            row_number=n,
            row_reference=f":{n}",
            raw_cells={"3": "14/07/2026", "7": f"'94911431739{n}", "9": remitter, "10": beneficiary, "13": "1.00"},
        )

    def test_each_row_gets_its_own_direction_and_unresolved_rows_are_logged(self):
        self._stage(1, "SSK", "HDF")
        self._stage(2, "ICI", "SSK")
        self._stage(3, "ICI", "HDF")

        result = normalize_rows({"uploaded_file_id": self.uploaded_file.id, "halted": False})

        by_ref = {row["row_reference"]: row for row in result["rows"]}
        self.assertEqual(by_ref[":1"]["direction"], Direction.DEBIT)
        self.assertEqual(by_ref[":2"]["direction"], Direction.CREDIT)
        self.assertEqual(by_ref[":3"]["direction"], Direction.UNKNOWN)
        self.assertEqual(by_ref[":1"]["rrn"], "949114317391")  # RRN still extracted generically
        self.assertTrue(by_ref[":1"]["rrn_valid"])
        self.assertEqual(result["direction_unresolved_count"], 1)

        exceptions = FileValidationException.objects.filter(
            uploaded_file=self.uploaded_file, rule_code="DIRECTION_UNRESOLVED"
        )
        self.assertEqual([e.row_reference for e in exceptions], [":3"])
        self.assertEqual(exceptions[0].severity, ValidationSeverity.WARNING)

    def test_malformed_rule_fails_loudly(self):
        FileTypeConfig.objects.filter(pk=self.config.pk).update(direction_derivation_rule="ssk_party:remitter_col=9")
        self._stage(1, "SSK", "HDF")
        with self.assertRaises(ValueError):
            normalize_rows({"uploaded_file_id": self.uploaded_file.id, "halted": False})


class SeedAndMigrationAgreeTests(TestCase):
    def test_seed_uses_ssk_party_for_drc_timeout_and_adjustment_only(self):
        rules = Counter(
            (c["file_type_code"], c["direction_derivation_rule"]) for c in _CONFIGS
        )
        self.assertEqual(rules[("DRC", DISPUTE_SSK_PARTY_RULE)], 1)
        self.assertEqual(rules[("TIMEOUT", DISPUTE_SSK_PARTY_RULE)], 1)
        self.assertEqual(rules[("ADJUSTMENT", ADJUSTMENT_SSK_PARTY_RULE)], 1)
        self.assertEqual(sum(n for (_code, rule), n in rules.items() if rule.startswith("ssk_party:")), 3)

    def test_migration_rules_match_seed(self):
        self.assertEqual(migration_0007.NEW_RULE, DISPUTE_SSK_PARTY_RULE)
        self.assertEqual(migration_0008.NEW_RULE, ADJUSTMENT_SSK_PARTY_RULE)

    def test_0008_updates_adjustment_only_while_still_constant_credit(self):
        payment_module = PaymentModule.objects.create(code="UPI", name="UPI")

        def config(code, rule, version=1):
            return FileTypeConfig.objects.create(
                payment_module=payment_module, file_type_code=code, register_type=code,
                payload_format=PayloadFormat.CSV, direction_derivation_rule=rule, version=version,
            )

        adjustment = config("ADJUSTMENT", "constant:CREDIT")
        hand_edited = config("ADJUSTMENT", "constant:DEBIT", version=2)
        timeout = config("TIMEOUT", "constant:CREDIT")

        migration_0008.forwards(django_apps, None)
        for obj in (adjustment, hand_edited, timeout):
            obj.refresh_from_db()
        self.assertEqual(adjustment.direction_derivation_rule, ADJUSTMENT_SSK_PARTY_RULE)
        self.assertEqual(hand_edited.direction_derivation_rule, "constant:DEBIT")
        self.assertEqual(timeout.direction_derivation_rule, "constant:CREDIT")  # 0007's job, not 0008's

        migration_0008.backwards(django_apps, None)
        adjustment.refresh_from_db()
        self.assertEqual(adjustment.direction_derivation_rule, "constant:CREDIT")

    def test_migration_updates_only_rows_still_on_the_old_constant(self):
        payment_module = PaymentModule.objects.create(code="UPI", name="UPI")

        def config(code, rule, version=1):
            return FileTypeConfig.objects.create(
                payment_module=payment_module, file_type_code=code, register_type=code,
                payload_format=PayloadFormat.CSV, direction_derivation_rule=rule, version=version,
            )

        drc = config("DRC", "constant:DEBIT")
        timeout = config("TIMEOUT", "constant:CREDIT")
        adjustment = config("ADJUSTMENT", "constant:CREDIT")
        hand_edited = config("DRC", "constant:CREDIT", version=2)

        migration_0007.forwards(django_apps, None)
        for obj in (drc, timeout, adjustment, hand_edited):
            obj.refresh_from_db()
        self.assertEqual(drc.direction_derivation_rule, DISPUTE_SSK_PARTY_RULE)
        self.assertEqual(timeout.direction_derivation_rule, DISPUTE_SSK_PARTY_RULE)
        self.assertEqual(adjustment.direction_derivation_rule, "constant:CREDIT")
        self.assertEqual(hand_edited.direction_derivation_rule, "constant:CREDIT")

        migration_0007.backwards(django_apps, None)
        drc.refresh_from_db()
        timeout.refresh_from_db()
        self.assertEqual(drc.direction_derivation_rule, "constant:DEBIT")
        self.assertEqual(timeout.direction_derivation_rule, "constant:CREDIT")
