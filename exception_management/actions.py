"""
The maker's action types and the rule -> pre-filled action table (Phase 3
design v1 section 2, approved 2026-10-10). Both are single tables here so
the mapping is never scattered through the code.

ACTION_SPECS says which response code (flag + numeric code) an action is
sent under in the NPCI portal, so a submission can be checked and its code
filled in. RET is the one action whose code the maker chooses (any RET
reason; default 115 from the rule, owner Q1).

DEFAULT_ACTION_BY_RULE pre-fills the maker's choice. A rule missing from the
table has no default: the maker must choose (R07b/R07x chargebacks --
"accept or represent", no single action in the rule table --, R07c, R13 and
the manual-review rules). The default response code is the rule
result's own code (rule_engine.Rule.response_code), so R05 pre-fills RET 115.
It is a pre-fill only -- nothing is ever submitted or approved automatically
(design D4).
"""
from __future__ import annotations

from dataclasses import dataclass

from django.db import models


class ActionType(models.TextChoices):
    DRC_102 = "DRC_102", "DRC reply 102 (customer reversed online)"
    DRC_103 = "DRC_103", "DRC reply 103 (customer reversed manually)"
    DRC_104 = "DRC_104", "DRC reply 104 (not effected to CBS)"
    TCC_102 = "TCC_102", "TCC 102 (credited online)"
    TCC_103 = "TCC_103", "TCC 103 (credited manually post-reconciliation)"
    RET = "RET", "RET - return to remitter (with reason)"
    MANUAL_CREDIT_RRC_501 = "MANUAL_CREDIT_RRC_501", "Manual credit + RRC 501"
    RRC_501_NO_CREDIT = "RRC_501_NO_CREDIT", "RRC 501 without a new credit (CBS reversal already posted)"
    CBS_ADJUSTMENT_ENTRY = "CBS_ADJUSTMENT_ENTRY", "Adjustment entry in CBS"
    CHARGEBACK_RAISE = "CHARGEBACK_RAISE", "Chargeback raise"
    CHARGEBACK_ACCEPT = "CHARGEBACK_ACCEPT", "Chargeback accept"
    CHARGEBACK_REPRESENT = "CHARGEBACK_REPRESENT", "Chargeback represent"  # Amendment 1 A2
    REFUND_CUSTOMER = "REFUND_CUSTOMER", "Refund customer (reversal)"
    CLOSE_NO_ACTION = "CLOSE_NO_ACTION", "Close without action (with finding)"
    # Phase 4 Slice 2 (S5): the only action of a settlement case. The maker
    # records the explanation of the NTSL difference; the checker accepts it.
    # No money moves; any correction is made by ops outside the system.
    SETTLEMENT_EXPLANATION = "SETTLEMENT_EXPLANATION", "Settlement difference explained (no money moves)"


# Settlement cases take only this action; transaction cases never take it.
SETTLEMENT_ACTIONS = (ActionType.SETTLEMENT_EXPLANATION,)


@dataclass(frozen=True)
class ActionSpec:
    code_flag: str | None = None  # rule_engine.CodeFlag value; None = sent without a response code
    fixed_code: str | None = None  # the numeric code; None with a flag = the maker chooses (RET)
    requires_cbs_reversal_evidence: bool = False  # design D4
    requires_remarks: bool = False  # "close without action (with finding)"
    completes_on_approval: bool = False  # nothing to do outside: no external reference (Amendment 1 A3)


ACTION_SPECS: dict[str, ActionSpec] = {
    ActionType.DRC_102: ActionSpec("DRC", "102"),
    ActionType.DRC_103: ActionSpec("DRC", "103"),
    ActionType.DRC_104: ActionSpec("DRC", "104"),
    ActionType.TCC_102: ActionSpec("TCC", "102"),
    ActionType.TCC_103: ActionSpec("TCC", "103"),
    ActionType.RET: ActionSpec("RET", None),
    ActionType.MANUAL_CREDIT_RRC_501: ActionSpec("RRC", "501"),
    ActionType.RRC_501_NO_CREDIT: ActionSpec("RRC", "501", requires_cbs_reversal_evidence=True),
    ActionType.CBS_ADJUSTMENT_ENTRY: ActionSpec(),
    ActionType.CHARGEBACK_RAISE: ActionSpec(),
    ActionType.CHARGEBACK_ACCEPT: ActionSpec(),
    ActionType.CHARGEBACK_REPRESENT: ActionSpec(),
    ActionType.REFUND_CUSTOMER: ActionSpec(),
    ActionType.CLOSE_NO_ACTION: ActionSpec(requires_remarks=True, completes_on_approval=True),
    # Accepted = done: nothing to carry out in the system, no external reference.
    ActionType.SETTLEMENT_EXPLANATION: ActionSpec(requires_remarks=True, completes_on_approval=True),
}

# Rule code (rule set v3.2, docs/phase2_rule_table_v3_approved.txt) -> the
# pre-filled action. Codes follow each rule's own "Code" line.
DEFAULT_ACTION_BY_RULE: dict[str, str] = {
    "R01": ActionType.DRC_102,  # debited + reversed: DRC 102
    "R02": ActionType.DRC_103,  # reverse manually, then DRC 103
    "R03": ActionType.DRC_104,  # never reached CBS: DRC 104
    "R04a": ActionType.TCC_102,  # credited online: TCC 102
    "R04b": ActionType.TCC_102,  # check the account first, then TCC 102
    "R05": ActionType.RET,  # RET, reason pre-filled 115 (owner Q1)
    "R06": ActionType.MANUAL_CREDIT_RRC_501,
    "R06b": ActionType.RRC_501_NO_CREDIT,  # evidence mandatory (D4); maker may switch to a manual credit
    "R12": ActionType.CBS_ADJUSTMENT_ENTRY,
}


def default_action_for(rule_code: str, rule_response_code_id: int | None) -> tuple[str, int | None]:
    """(action_type or "", response_code_id or None) pre-filled for a rule."""
    action = DEFAULT_ACTION_BY_RULE.get(rule_code, "")
    if not action or ACTION_SPECS[action].code_flag is None:
        return action, None
    return action, rule_response_code_id
