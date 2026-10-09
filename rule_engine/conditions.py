"""
Declarative rule-condition format for the Business Rule Engine (Phase 2
Step 3). This module defines the vocabulary and validates it. It does NOT
evaluate conditions: the evaluator is Step 4, and it must follow the
semantics written here.

FORMAT
------
A condition is a JSON object, one of four node shapes:

    {"all": [<node>, ...]}                      every child true (non-empty list)
    {"any": [<node>, ...]}                      at least one child true (non-empty list)
    {"fact": "<name>", "op": "<op>", "value": <v>}   a leaf comparison
    {"always": true}                            true for every transaction (catch-all, R99)

A node with any other key, or with extra keys, is rejected. There is no
"not" node on purpose: the matrix has nullable facts, and negating a
comparison on a null fact is where SQL's three-valued logic and Python's
two-valued logic disagree. Write the negation into the leaf instead
(`"op": "eq", "value": false`, or `"op": "ne"`).

Example (R06b):

    {"all": [
        {"fact": "adjustment.ssk_side", "op": "eq", "value": true},
        {"fact": "adjustment.adjtype", "op": "in",
         "value": ["Credit Adjustment", "Online Refund", "RET"]},
        {"fact": "cbs.present", "op": "eq", "value": true},
        {"fact": "cbs_reversed", "op": "eq", "value": true}
    ]}

NULL SEMANTICS (the Step 4 evaluator must follow these)
---------------------------------------------------------
A leaf whose fact is null (absent overlay row, one-sided amount_match,
Adjustment with no Txndate, ...) is FALSE, whatever the operator, including
"ne" and "not_in". This is the same as SQL's WHERE/CASE treating UNKNOWN
as not-matched, which is what docs/phase2_rule_table_v3_1_reference.sql
relies on. Example: {"fact": "timeout.ssk_side", "op": "eq", "value": false}
is true only when a Timeout row exists and SSK is the other party; it is
false when the RRN has no Timeout row at all.

Rules are checked in Rule.evaluation_order; the first rule whose condition
is true wins.

FACTS
-----
See FACTS below for each name, its type, whether it can be null, and where
it comes from in the presence matrix (reconciliation.models).

OPERATORS by fact type
----------------------
    bool : eq, ne                      value must be true/false
    str  : eq, ne                      value must be a string (exact, case-sensitive)
           in, not_in                  value: non-empty list of strings (exact, case-sensitive)
           icontains_any               value: non-empty list of non-empty strings; true when
                                       the fact contains ANY of them, case-insensitive
                                       (SQL: fact ILIKE '%x%' OR ...)
    int  : eq, ne, gt, gte, lt, lte    value must be an integer (not a bool)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ConditionError(ValueError):
    """Raised when a condition does not follow the format above."""


@dataclass(frozen=True)
class Fact:
    type: type
    nullable: bool
    source: str


# Matrix facts. Names are "<leg>.<attr>" for per-leg facts and bare names for
# TransactionMatchGroup columns.
FACTS: dict[str, Fact] = {
    # --- CBS / NPCI legs (RegisterPresence rows, always present per group) ---
    "cbs.present": Fact(bool, False, "RegisterPresence(leg='CBS').present"),
    "cbs.pair_missing": Fact(
        bool,
        False,
        "RegisterPresence(leg='CBS').paired_transaction_record IS NULL -- the second CBS "
        "row of an on-us pair is missing. Only meaningful together with is_on_us.",
    ),
    "npci.present": Fact(bool, False, "RegisterPresence(leg='NPCI').present"),
    "npci.response_code": Fact(
        str,
        False,
        "RegisterPresence(leg='NPCI').response_code ('00' = success). Blank string, not "
        "null, when NPCI is absent.",
    ),
    # --- TransactionMatchGroup columns ---
    "cbs_reversed": Fact(bool, False, "TransactionMatchGroup.cbs_reversed"),
    "is_on_us": Fact(bool, False, "TransactionMatchGroup.is_on_us"),
    "amount_match": Fact(bool, True, "TransactionMatchGroup.amount_match (null when one-sided)"),
    "direction_match": Fact(bool, True, "TransactionMatchGroup.direction_match (null when one-sided)"),
    # --- Dispute overlays (RegisterPresence rows exist only where the file has the RRN) ---
    "drc.present": Fact(bool, False, "a RegisterPresence(leg='DRC') row exists"),
    "drc.ssk_side": Fact(
        bool,
        True,
        "RegisterPresence(leg='DRC').ssk_side: True = SSK is the Remitter, False = SSK is the "
        "OTHER party, null = no DRC row",
    ),
    "timeout.present": Fact(bool, False, "a RegisterPresence(leg='TIMEOUT') row exists"),
    "timeout.ssk_side": Fact(
        bool,
        True,
        "RegisterPresence(leg='TIMEOUT').ssk_side: True = SSK is the Beneficiary, False = SSK is "
        "the OTHER party (the remitter), null = no Timeout row",
    ),
    "adjustment.present": Fact(bool, False, "a RegisterPresence(leg='ADJUSTMENT') row exists"),
    "adjustment.ssk_side": Fact(
        bool,
        True,
        "RegisterPresence(leg='ADJUSTMENT').ssk_side: True = SSK is the Remitter, False = SSK is "
        "the OTHER party, null = no Adjustment row",
    ),
    "adjustment.adjtype": Fact(
        str, True, "RegisterPresence(leg='ADJUSTMENT').dispute_type (the file's Adjtype)"
    ),
    "adjustment.age_days": Fact(
        int,
        True,
        "Adjdate - Txndate in days: the Adjustment fact's business_date minus its staging row's "
        "txn_date_raw (DD-MM-YYYY; staging or staging history). Null when Txndate is unknown.",
    ),
}

OPERATORS_BY_TYPE: dict[type, frozenset[str]] = {
    bool: frozenset({"eq", "ne"}),
    str: frozenset({"eq", "ne", "in", "not_in", "icontains_any"}),
    int: frozenset({"eq", "ne", "gt", "gte", "lt", "lte"}),
}
ALL_OPERATORS = frozenset().union(*OPERATORS_BY_TYPE.values())
_LIST_OPERATORS = frozenset({"in", "not_in", "icontains_any"})


def _check_value(fact_name: str, fact: Fact, op: str, value: Any, path: str) -> None:
    if op in _LIST_OPERATORS:
        if not isinstance(value, list) or not value:
            raise ConditionError(f"{path}: '{op}' on {fact_name} needs a non-empty list")
        for item in value:
            if not isinstance(item, str) or (op == "icontains_any" and not item):
                raise ConditionError(f"{path}: '{op}' on {fact_name} needs a list of non-empty strings")
        return
    if fact.type is bool:
        ok = isinstance(value, bool)
    elif fact.type is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, str)
    if not ok:
        raise ConditionError(
            f"{path}: {fact_name} is {fact.type.__name__}, got {type(value).__name__} {value!r}"
        )


def validate_condition(node: Any, path: str = "condition") -> None:
    """Raise ConditionError unless `node` follows the module docstring's format."""
    if not isinstance(node, dict):
        raise ConditionError(f"{path}: must be an object, got {type(node).__name__}")
    keys = set(node)
    if keys in ({"all"}, {"any"}):
        (key,) = keys
        children = node[key]
        if not isinstance(children, list) or not children:
            raise ConditionError(f"{path}.{key}: must be a non-empty list")
        for i, child in enumerate(children):
            validate_condition(child, f"{path}.{key}[{i}]")
        return
    if keys == {"always"}:
        if node["always"] is not True:
            raise ConditionError(f"{path}.always: must be true")
        return
    if keys == {"fact", "op", "value"}:
        name, op = node["fact"], node["op"]
        if name not in FACTS:
            raise ConditionError(f"{path}: unknown fact {name!r}")
        fact = FACTS[name]
        if op not in ALL_OPERATORS:
            raise ConditionError(f"{path}: unknown operator {op!r}")
        if op not in OPERATORS_BY_TYPE[fact.type]:
            raise ConditionError(f"{path}: operator {op!r} not allowed on {fact.type.__name__} fact {name}")
        _check_value(name, fact, op, node["value"], path)
        return
    raise ConditionError(
        f"{path}: unknown node shape with keys {sorted(keys)}; expected all / any / always / "
        "fact+op+value"
    )


def facts_used(node: dict) -> set[str]:
    """Fact names referenced by a (valid) condition."""
    if "all" in node or "any" in node:
        return set().union(*(facts_used(c) for c in node.get("all", node.get("any"))))
    if "fact" in node:
        return {node["fact"]}
    return set()


def is_catch_all(node: dict) -> bool:
    return node == {"always": True}
