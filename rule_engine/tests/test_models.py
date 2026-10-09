"""RuleSet versioning and immutability, Rule / RuleTat / ResponseCode guards."""
from __future__ import annotations

from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import PaymentModule
from rule_engine.models import (
    AgeingBucket,
    ResponseCode,
    Rule,
    RuleSet,
    RuleSetImmutableError,
    RuleSetStatus,
    RuleTat,
    validate_ageing_buckets,
)

ALWAYS = {"always": True}
CBS = {"fact": "cbs.present", "op": "eq", "value": True}


def make_set(module, label="v1", **kw):
    return RuleSet.objects.create(
        payment_module=module, version_label=label, effective_from=date(2026, 10, 9),
        source_document="test", created_by="test", **kw,
    )


def make_rule(rule_set, code, order, condition, **kw):
    fields = dict(
        name=code, status_category="PENDING_INVESTIGATION", action="Manual review",
        priority="MEDIUM", tat_type="DAYS", tat_days=1, tat_day_basis="WORKING", source="test",
    )
    fields.update(kw)
    return Rule.objects.create(rule_set=rule_set, code=code, evaluation_order=order, condition=condition, **fields)


def make_valid_set(module, label="v1"):
    rs = make_set(module, label)
    make_rule(rs, "R01", 1, CBS)
    make_rule(rs, "R99", 2, ALWAYS)
    return rs


class RuleSetLifecycleTests(TestCase):
    def setUp(self):
        self.upi = PaymentModule.objects.create(code="UPI", name="UPI")

    def test_new_set_must_start_as_draft(self):
        with self.assertRaises(RuleSetImmutableError):
            make_set(self.upi, status=RuleSetStatus.ACTIVE)

    def test_draft_is_editable_and_deletable(self):
        rs = make_valid_set(self.upi)
        rs.notes = "edited"
        rs.save()
        rule = rs.rules.get(code="R01")
        rule.name = "renamed"
        rule.save()
        rule.delete()
        rs.delete()
        self.assertFalse(RuleSet.objects.exists())

    def test_draft_cannot_be_made_active_by_save(self):
        rs = make_valid_set(self.upi)
        rs.status = RuleSetStatus.ACTIVE
        with self.assertRaises(RuleSetImmutableError):
            rs.save()

    def test_activate_then_new_version_retires_old(self):
        v1 = make_valid_set(self.upi, "v1")
        v1.activate()
        v1.refresh_from_db()
        self.assertEqual(v1.status, RuleSetStatus.ACTIVE)
        self.assertIsNotNone(v1.activated_at)

        v2 = make_valid_set(self.upi, "v2")
        v2.activate()
        v1.refresh_from_db()
        v2.refresh_from_db()
        self.assertEqual((v1.status, v2.status), (RuleSetStatus.RETIRED, RuleSetStatus.ACTIVE))
        self.assertIsNotNone(v1.retired_at)
        self.assertEqual(RuleSet.objects.filter(payment_module=self.upi, status="ACTIVE").count(), 1)

    def test_one_active_per_module_is_enforced_by_the_db(self):
        make_valid_set(self.upi, "v1").activate()
        v2 = make_valid_set(self.upi, "v2")
        with self.assertRaises(IntegrityError), transaction.atomic():
            # _base_manager is a plain Manager: bypasses the model guards, to
            # prove the DB constraint itself holds
            RuleSet._base_manager.filter(pk=v2.pk).update(status="ACTIVE")

    def test_other_module_has_its_own_active_set(self):
        imps = PaymentModule.objects.create(code="IMPS", name="IMPS")
        make_valid_set(self.upi).activate()
        make_valid_set(imps).activate()
        self.assertEqual(RuleSet.objects.filter(status="ACTIVE").count(), 2)

    def test_activation_needs_rules_and_a_final_catch_all(self):
        empty = make_set(self.upi, "empty")
        with self.assertRaises(ValidationError):
            empty.activate()
        no_catch_all = make_set(self.upi, "nca")
        make_rule(no_catch_all, "R01", 1, CBS)
        with self.assertRaises(ValidationError):
            no_catch_all.activate()
        catch_all_not_last = make_set(self.upi, "cnl")
        make_rule(catch_all_not_last, "R99", 1, ALWAYS)
        make_rule(catch_all_not_last, "R01", 2, CBS)
        with self.assertRaises(ValidationError):
            catch_all_not_last.activate()
        self.assertFalse(RuleSet.objects.filter(status="ACTIVE").exists())

    def test_by_adjtype_rule_needs_tat_rows(self):
        rs = make_set(self.upi)
        make_rule(rs, "R07b", 1, CBS, tat_type="BY_ADJTYPE", tat_days=None, tat_day_basis="")
        make_rule(rs, "R99", 2, ALWAYS)
        with self.assertRaises(ValidationError):
            rs.activate()

    def test_cannot_activate_twice_or_reactivate_retired(self):
        v1 = make_valid_set(self.upi, "v1")
        v1.activate()
        with self.assertRaises(RuleSetImmutableError):
            v1.activate()
        make_valid_set(self.upi, "v2").activate()
        v1.refresh_from_db()
        with self.assertRaises(RuleSetImmutableError):
            v1.activate()


class ImmutabilityTests(TestCase):
    def setUp(self):
        self.upi = PaymentModule.objects.create(code="UPI", name="UPI")
        self.code = ResponseCode.objects.create(
            payment_module=self.upi, flag="RET", code="115", description="Account doesn't exist", source="t"
        )
        self.unknown = ResponseCode.objects.create(
            payment_module=self.upi, flag="RET", code=None, description="Account closed", source="t"
        )
        self.rs = make_set(self.upi)
        self.rule = make_rule(self.rs, "R05", 1, CBS, response_code=self.code)
        self.rule.alternative_codes.add(self.unknown)
        self.by_type = make_rule(self.rs, "R07b", 2, CBS, tat_type="BY_ADJTYPE", tat_days=None, tat_day_basis="")
        self.tat = RuleTat.objects.create(
            rule=self.by_type, evaluation_order=1, label="x",
            condition={"fact": "adjustment.adjtype", "op": "icontains_any", "value": ["chargeback"]},
            tat_days=7, tat_day_basis="CALENDAR", source="t",
        )
        make_rule(self.rs, "R99", 3, ALWAYS)
        self.rs.activate()

    def _assert_blocked(self, fn):
        # own savepoint: the m2m guard raises inside Django's atomic(savepoint=False)
        with self.assertRaises(RuleSetImmutableError), transaction.atomic():
            fn()

    def test_active_set_fields_are_frozen(self):
        self.rs.notes = "changed"
        self._assert_blocked(self.rs.save)
        self._assert_blocked(lambda: RuleSet.objects.filter(pk=self.rs.pk).update(notes="x"))
        self._assert_blocked(self.rs.delete)
        self._assert_blocked(lambda: RuleSet.objects.filter(pk=self.rs.pk).delete())

    def test_retired_set_is_frozen_too(self):
        make_valid_set(self.upi, "v2").activate()
        self.rs.refresh_from_db()
        self.assertEqual(self.rs.status, RuleSetStatus.RETIRED)
        self.rs.notes = "changed"
        self._assert_blocked(self.rs.save)
        self.rs.refresh_from_db()
        self.rs.status = RuleSetStatus.ACTIVE
        self._assert_blocked(self.rs.save)
        self._assert_blocked(self.rs.delete)

    def test_retire_cannot_smuggle_other_changes(self):
        self.rs.version_label = "v9"
        self.rs.status = RuleSetStatus.RETIRED
        self.rs.retired_at = None
        self.rs._transition = True
        self._assert_blocked(self.rs.save)

    def test_rules_of_active_set_are_frozen(self):
        self.rule.priority = "LOW"
        self._assert_blocked(self.rule.save)
        self._assert_blocked(self.rule.delete)
        self._assert_blocked(lambda: Rule.objects.filter(pk=self.rule.pk).update(priority="LOW"))
        self._assert_blocked(lambda: Rule.objects.filter(pk=self.rule.pk).delete())
        self._assert_blocked(lambda: make_rule(self.rs, "R50", 50, CBS))
        self._assert_blocked(
            lambda: Rule.objects.bulk_create([Rule(rule_set=self.rs, code="R51", evaluation_order=51, condition=CBS)])
        )

    def test_rule_cannot_be_moved_out_of_an_active_set(self):
        draft = make_set(self.upi, "draft")
        self.rule.rule_set = draft
        self._assert_blocked(self.rule.save)
        self._assert_blocked(lambda: Rule.objects.filter(rule_set=draft).update(rule_set=self.rs))

    def test_tat_rows_of_active_set_are_frozen(self):
        self.tat.tat_days = 8
        self._assert_blocked(self.tat.save)
        self._assert_blocked(self.tat.delete)
        self._assert_blocked(lambda: RuleTat.objects.filter(pk=self.tat.pk).update(tat_days=9))
        self._assert_blocked(
            lambda: RuleTat.objects.create(
                rule=self.by_type, evaluation_order=2, label="y",
                condition={"always": True}, tat_days=1, tat_day_basis="WORKING", source="t",
            )
        )

    def test_alternative_codes_of_active_set_are_frozen(self):
        other = ResponseCode.objects.create(payment_module=self.upi, flag="RET", description="NRI account", source="t")
        self._assert_blocked(lambda: self.rule.alternative_codes.add(other))
        self._assert_blocked(lambda: self.rule.alternative_codes.remove(self.unknown))
        self._assert_blocked(lambda: self.rule.alternative_codes.clear())
        self._assert_blocked(lambda: other.rules_as_alternative.add(self.rule))
        self._assert_blocked(lambda: self.unknown.rules_as_alternative.clear())

    def test_codes_used_by_active_set_are_frozen(self):
        self.code.description = "changed"
        self._assert_blocked(self.code.save)
        self._assert_blocked(self.code.delete)
        self._assert_blocked(self.unknown.delete)
        self._assert_blocked(lambda: ResponseCode.objects.filter(pk=self.code.pk).update(source="x"))
        self._assert_blocked(lambda: ResponseCode.objects.filter(pk=self.unknown.pk).delete())

    def test_unknown_code_may_be_filled_in_once(self):
        self.unknown.code = "999"  # test value only
        self.unknown.save()
        self.unknown.refresh_from_db()
        self.assertEqual(self.unknown.code, "999")
        self.unknown.code = "998"
        self._assert_blocked(self.unknown.save)

    def test_unused_code_stays_editable(self):
        free = ResponseCode.objects.create(payment_module=self.upi, flag="TCC", code="103", description="d", source="t")
        free.description = "edited"
        free.save()
        free.delete()

    def test_new_version_is_the_way_to_change_a_rule(self):
        v2 = make_set(self.upi, "v2")
        make_rule(v2, "R05", 1, CBS, response_code=self.code, priority="LOW")
        make_rule(v2, "R99", 2, ALWAYS)
        v2.activate()
        self.rule.refresh_from_db()
        self.assertEqual(self.rule.priority, "MEDIUM")  # old version untouched
        self.assertEqual(v2.rules.get(code="R05").priority, "LOW")


class RuleValidationTests(TestCase):
    def setUp(self):
        self.upi = PaymentModule.objects.create(code="UPI", name="UPI")
        self.rs = make_set(self.upi)

    def test_invalid_condition_rejected_at_save(self):
        with self.assertRaises(ValidationError):
            make_rule(self.rs, "R01", 1, {"fact": "made.up", "op": "eq", "value": True})
        with self.assertRaises(ValidationError):
            make_rule(self.rs, "R01", 1, {"all": [CBS, {"fact": "cbs.present", "op": "like", "value": True}]})
        self.assertFalse(Rule.objects.exists())

    def test_invalid_tat_row_condition_rejected(self):
        rule = make_rule(self.rs, "R07b", 1, CBS, tat_type="BY_ADJTYPE", tat_days=None, tat_day_basis="")
        with self.assertRaises(ValidationError):
            RuleTat.objects.create(rule=rule, evaluation_order=1, label="x", condition={"bad": 1},
                                   tat_days=1, tat_day_basis="WORKING", source="t")

    def test_tat_fields_must_agree_with_tat_type(self):
        with self.assertRaises(ValidationError):
            make_rule(self.rs, "R01", 1, CBS, tat_type="DAYS", tat_days=None, tat_day_basis="")
        with self.assertRaises(ValidationError):
            make_rule(self.rs, "R02", 2, CBS, tat_type="NONE", tat_days=1, tat_day_basis="WORKING")
        rule = make_rule(self.rs, "R03", 3, CBS)  # DAYS, no TAT rows allowed
        with self.assertRaises(ValidationError):
            RuleTat.objects.create(rule=rule, evaluation_order=1, label="x", condition=ALWAYS,
                                   tat_days=1, tat_day_basis="WORKING", source="t")

    def test_tat_check_constraint_in_db(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Rule.objects.filter(pk=make_rule(self.rs, "R01", 1, CBS).pk).update(tat_days=None)

    def test_reason_template_placeholders_are_checked(self):
        make_rule(self.rs, "R07x", 1, CBS, reason_template="Dispute is {age_days} days old")
        with self.assertRaises(ValidationError):
            make_rule(self.rs, "R08", 2, CBS, reason_template="{unknown}")

    def test_response_code_must_be_same_module(self):
        imps = PaymentModule.objects.create(code="IMPS", name="IMPS")
        code = ResponseCode.objects.create(payment_module=imps, flag="DRC", code="102", description="d", source="t")
        with self.assertRaises(ValidationError):
            make_rule(self.rs, "R01", 1, CBS, response_code=code)

    def test_code_and_order_unique_per_set(self):
        make_rule(self.rs, "R01", 1, CBS)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_rule(self.rs, "R01", 2, CBS)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_rule(self.rs, "R02", 1, CBS)


class ResponseCodeTableTests(TestCase):
    def setUp(self):
        self.upi = PaymentModule.objects.create(code="UPI", name="UPI")

    def test_known_code_unique_per_flag_but_unknown_codes_may_repeat(self):
        ResponseCode.objects.create(payment_module=self.upi, flag="DRC", code="102", description="a", source="t")
        ResponseCode.objects.create(payment_module=self.upi, flag="TCC", code="102", description="a", source="t")
        ResponseCode.objects.create(payment_module=self.upi, flag="RET", code=None, description="x", source="t")
        ResponseCode.objects.create(payment_module=self.upi, flag="RET", code=None, description="y", source="t")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ResponseCode.objects.create(payment_module=self.upi, flag="DRC", code="102", description="b", source="t")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ResponseCode.objects.create(payment_module=self.upi, flag="RET", code=None, description="x", source="t")


class AgeingBucketTests(TestCase):
    def setUp(self):
        self.upi = PaymentModule.objects.create(code="UPI", name="UPI")

    def _bucket(self, order, lo, hi):
        return AgeingBucket.objects.create(payment_module=self.upi, order=order, label=f"b{order}",
                                           min_days=lo, max_days=hi, boundary_reason="r", source="t")

    def test_gap_or_closed_end_rejected(self):
        self._bucket(1, 0, 1)
        self._bucket(2, 3, None)
        with self.assertRaises(ValidationError):
            validate_ageing_buckets(self.upi)

    def test_max_below_min_rejected_by_db(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._bucket(1, 5, 2)
