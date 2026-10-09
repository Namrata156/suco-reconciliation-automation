"""Condition vocabulary validator (rule_engine.conditions)."""
from __future__ import annotations

from django.test import SimpleTestCase

from rule_engine.conditions import FACTS, ConditionError, facts_used, is_catch_all, validate_condition
from rule_engine.seed_data import v3_1


def leaf(fact, op, value):
    return {"fact": fact, "op": op, "value": value}


class ValidConditionTests(SimpleTestCase):
    def test_accepts_each_node_shape(self):
        for cond in [
            {"always": True},
            leaf("cbs.present", "eq", True),
            leaf("amount_match", "ne", False),
            leaf("npci.response_code", "eq", "00"),
            leaf("npci.response_code", "not_in", ["00", "RB"]),
            leaf("adjustment.adjtype", "in", ["RET"]),
            leaf("adjustment.adjtype", "icontains_any", ["chargeback"]),
            leaf("adjustment.age_days", "gt", 35),
            leaf("adjustment.age_days", "lte", 0),
            {"all": [leaf("cbs.present", "eq", True), {"any": [leaf("is_on_us", "eq", False)]}]},
        ]:
            with self.subTest(cond=cond):
                validate_condition(cond)

    def test_facts_used_and_catch_all(self):
        cond = {"all": [leaf("cbs.present", "eq", True), {"any": [leaf("drc.ssk_side", "eq", False)]}]}
        self.assertEqual(facts_used(cond), {"cbs.present", "drc.ssk_side"})
        self.assertTrue(is_catch_all({"always": True}))
        self.assertFalse(is_catch_all(cond))


class InvalidConditionTests(SimpleTestCase):
    def assertRejected(self, cond, fragment):
        with self.assertRaises(ConditionError) as ctx:
            validate_condition(cond)
        self.assertIn(fragment, str(ctx.exception))

    def test_unknown_fact(self):
        self.assertRejected(leaf("cbs.amount", "eq", 1), "unknown fact")

    def test_unknown_operator(self):
        self.assertRejected(leaf("cbs.present", "is", True), "unknown operator")

    def test_operator_not_allowed_for_type(self):
        self.assertRejected(leaf("cbs.present", "gt", 1), "not allowed")
        self.assertRejected(leaf("adjustment.age_days", "in", ["1"]), "not allowed")
        self.assertRejected(leaf("npci.response_code", "gt", "00"), "not allowed")

    def test_value_type_mismatch(self):
        self.assertRejected(leaf("cbs.present", "eq", 1), "is bool")
        self.assertRejected(leaf("cbs.present", "eq", "true"), "is bool")
        self.assertRejected(leaf("adjustment.age_days", "gt", True), "is int")
        self.assertRejected(leaf("adjustment.age_days", "gt", "35"), "is int")
        self.assertRejected(leaf("npci.response_code", "eq", 0), "is str")

    def test_list_operators_need_non_empty_string_lists(self):
        self.assertRejected(leaf("adjustment.adjtype", "in", []), "non-empty list")
        self.assertRejected(leaf("adjustment.adjtype", "in", "RET"), "non-empty list")
        self.assertRejected(leaf("adjustment.adjtype", "in", [1]), "list of non-empty strings")
        self.assertRejected(leaf("adjustment.adjtype", "icontains_any", [""]), "list of non-empty strings")

    def test_bad_node_shapes(self):
        self.assertRejected([], "must be an object")
        self.assertRejected({}, "unknown node shape")
        self.assertRejected({"not": leaf("cbs.present", "eq", True)}, "unknown node shape")
        self.assertRejected({"all": []}, "non-empty list")
        self.assertRejected({"any": leaf("cbs.present", "eq", True)}, "non-empty list")
        self.assertRejected({"always": False}, "must be true")
        self.assertRejected({"all": [leaf("cbs.present", "eq", True)], "any": []}, "unknown node shape")
        self.assertRejected({**leaf("cbs.present", "eq", True), "extra": 1}, "unknown node shape")

    def test_error_names_the_path(self):
        self.assertRejected({"all": [leaf("cbs.present", "eq", True), {"any": [leaf("nope", "eq", 1)]}]}, "condition.all[1].any[0]")


class SeededConditionTests(SimpleTestCase):
    """Every seeded condition (rules and R07b's TAT rows) parses and uses only known facts."""

    def test_every_seeded_condition_is_valid(self):
        for spec in v3_1.RULES:
            with self.subTest(rule=spec["code"]):
                validate_condition(spec["condition"])
                self.assertLessEqual(facts_used(spec["condition"]), set(FACTS))
            for row in spec.get("tat_rows", []):
                with self.subTest(rule=spec["code"], tat_row=row[1]):
                    validate_condition(row[2])
                    self.assertEqual(facts_used(row[2]), {"adjustment.adjtype"})

    def test_only_r99_is_catch_all(self):
        self.assertEqual([s["code"] for s in v3_1.RULES if is_catch_all(s["condition"])], ["R99"])
