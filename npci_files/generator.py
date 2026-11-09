"""
Builds the NPCI upload file for one date (docs/npci_file_design_draft_v1.txt,
approved 2026-10-22/23 with Amendment 1; Amendment 2, owner 2026-10-24).

Which cases go in (Amendment 2: the file can be generated right after
reconciliation, like ops' manual process, with no Maker/Checker step before
it). Every current transaction case whose reply (flag + numeric code) is DRC,
TCC, RET or RRC gives a row. The reply is:
  - the Checker-approved one (APPROVED / COMPLETED case);
  - else the Maker's submitted one (PENDING_APPROVAL case);
  - else the rule's suggested reply (OPEN / IN_PROGRESS case), the same
    pre-fill the Maker would start from.
A case is left out, listed as "not included" with a warning, when:
  a) it is flagged for review (NEEDS_REVIEW);
  b) its reply legally needs evidence or a person's decision and no Checker
     has approved it yet (NEEDS_APPROVAL; APPROVAL_REQUIRED_RULES below:
     R06b, R07x, R04b, plus any reply that needs the CBS reversal as
     evidence, i.e. RRC 501 without a new credit);
  c) the Checker rejected its last reply and the Maker has not submitted a
     new one (REJECTED): a rejected reply never reaches a file through the
     rule's suggestion;
  d) the reply has no numeric NPCI code yet (picked up by a later file once
     the code is filled in), there is no NPCI dispute row, or a value cannot
     be written into the file.
Placement: DRC / TCC / RET rows go in the file of the case's own business
date; RRC rows in the file of the Adjustment row's Adjdate (design section 2).
A case is never included twice (Amendment 1): a later file for the same date
(_2, _3) holds only cases that appeared or became eligible since the earlier
files (a re-run, an adjustment that arrived later, an approval, a code filled
in). The app never sends anything to NPCI (owner 2026-10-23): the user
downloads the file and uploads it to the NPCI portal by hand.

Values (design section 2): bankadjref and shser = the case RRN; Shtdat = the
business date (RRC: the Adjustment's Txndate); adjamt = the dispute row's
amount (RRC: Adjamount); shcrd = the dispute report's account cell, copied as
it is minus NPCI's leading apostrophe; specifyother = the response code's
description in the repository at generation time (Amendment 1, option B).

Generating while any case is left out makes a PARTIAL file and needs an
explicit confirmation (owner Q5).

Owner 2026-11-09 (overrides Amendment 2 B3 for these rules only): a case whose
no-money reply goes into the file (R01 DRC 102, R03 DRC 104, R04a TCC 102;
npci_files.auto_close.AUTO_CLOSE_REPLIES) is closed by the system in the same
transaction as the file. The preview marks those rows (PlannedRow.closes_case).

plan_file(..., rule_defaults=True) is the verification path only (the
check_npci_file command): every case is taken with its rule's reply as if a
Checker had approved it, ignoring the files already generated. It never
saves anything.
"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from django.db import models, transaction
from django.db.models import Max, Q
from django.utils import timezone

from core.models import OperationType, PaymentModule
from core.operations import InvalidInput, RoleRequired, WrongState, log_operation, require_past_business_date
from exception_management.actions import ACTION_SPECS, ActionType
from exception_management.models import CaseStatus, CaseType, Decision, ExceptionCase, ProposedAction
from exception_management.roles import Role, user_roles
from npci_files import auto_close
from npci_files.formats import (
    ACCOUNT_CELL_BY_LEG,
    ADJUSTMENT_TXN_DATE_CELL,
    ADJUSTMENT_TXN_DATE_FORMAT,
    LEG_BY_FLAG,
    FileLine,
    FormatError,
    format_for,
)
from npci_files.models import NpciFile, NpciFileRow, ReplySource
from reconciliation.models import RegisterPresence
from rule_engine.models import ResponseCode
from transactions.models import TransactionFact
from transactions.models.history import HISTORY_MODEL_BY_STAGING_MODEL
from transactions.models.staging import STAGING_MODEL_BY_REGISTER_TYPE

GENERATE_ROLES = (Role.CHECKER,)  # owner Q2: "the checker authorises NPCI uploads"
DOWNLOAD_ROLES = (Role.MAKER, Role.CHECKER, Role.OPERATIONS_USER)  # the people who upload (Q2)

DECIDED = (CaseStatus.APPROVED, CaseStatus.COMPLETED)
NOT_SUBMITTED = (CaseStatus.OPEN, CaseStatus.IN_PROGRESS)
# Rows telling NPCI the customer was credited by hand (design section 3, Q3).
MANUAL_CREDIT_ACTIONS = (ActionType.DRC_103, ActionType.MANUAL_CREDIT_RRC_501)

# Amendment 2: replies that legally need evidence or a person's decision stay
# out of a file until a Checker approves them (the rule's suggestion, or a
# Maker's submission still waiting, is not enough). Besides these rules, any
# reply whose action needs the CBS reversal as evidence (ACTION_SPECS
# requires_cbs_reversal_evidence: RRC 501 without a new credit), whatever the
# rule.
APPROVAL_REQUIRED_RULES: dict[str, str] = {
    "R06b": "RRC 501 without a new credit may be approved only with the CBS reversal attached as evidence "
            "(owner 2026-10-10)",
    "R07x": "chargeback older than 35 days: a person decides whether the response deadline has passed",
    "R04b": "credited AND reversed in CBS: the customer's account is checked before any TCC reply",
}
EVIDENCE_REASON = "the reply needs the CBS reversal attached as evidence"


class Reason(models.TextChoices):
    """Why a case that would give a row is not in the file."""

    NEEDS_REVIEW = "NEEDS_REVIEW", "Flagged for review after a re-run"
    NEEDS_APPROVAL = "NEEDS_APPROVAL", "Needs a Checker's approval first (evidence or a person's decision)"
    REJECTED = "REJECTED", "The Checker rejected the last reply; waiting for the Maker's new reply"
    NO_NUMERIC_CODE = "NO_NUMERIC_CODE", "The reply code has no numeric NPCI code yet"
    NO_NPCI_ROW = "NO_NPCI_ROW", "No NPCI dispute row for this reply"
    BAD_SOURCE_DATA = "BAD_SOURCE_DATA", "A value cannot be written into the file"


@dataclass
class Reply:
    action_type: str
    response_code: ResponseCode | None
    action: ProposedAction | None  # None = the rule's suggested reply
    source: str  # ReplySource

    @property
    def flag(self) -> str | None:
        return ACTION_SPECS[self.action_type].code_flag if self.action_type in ACTION_SPECS else None

    @property
    def code(self) -> str | None:
        return self.response_code.code if self.response_code is not None else None


@dataclass
class PlannedRow:
    case: ExceptionCase
    action: ProposedAction | None  # None = the rule's suggested reply
    action_type: str
    fact: TransactionFact
    line: FileLine
    source: str = ReplySource.RULE
    closes_case: bool = False  # generating the file closes the case (no-money reply, owner 2026-11-09)

    @property
    def flag(self) -> str:
        return self.line.flag

    @property
    def code(self) -> str:
        return self.line.code


@dataclass
class Excluded:
    case: ExceptionCase
    flag: str
    code: str | None
    reason: str
    detail: str = ""


@dataclass
class FilePlan:
    module: PaymentModule
    file_date: date
    file_format: object
    rows: list[PlannedRow] = field(default_factory=list)
    excluded: list[Excluded] = field(default_factory=list)
    already_sent: list[tuple[ExceptionCase, NpciFileRow]] = field(default_factory=list)
    # Cases already in a file of this date that changed since: flagged for
    # review, now carrying another reply, or closed by a re-run.
    changed_since_included: list[tuple[ExceptionCase, NpciFileRow]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        return bool(self.excluded)

    def counts(self) -> list[dict]:
        out: dict[tuple, dict] = {}
        for row in self.rows:
            key = (row.flag, row.code, row.line.text)
            c = out.setdefault(key, {"flag": row.flag, "code": row.code, "text": row.line.text, "rows": 0,
                                     "amount": Decimal("0")})
            c["rows"] += 1
            c["amount"] += row.line.amount
        return list(out.values())

    def excluded_counts(self) -> dict[str, int]:
        return dict(Counter(str(e.reason) for e in self.excluded))

    def source_counts(self) -> dict[str, int]:
        return dict(Counter(str(r.source) for r in self.rows))

    @property
    def total_amount(self) -> Decimal:
        return sum((r.line.amount for r in self.rows), Decimal("0"))

    def next_sequence(self) -> int:
        current = NpciFile.objects.filter(payment_module=self.module, file_date=self.file_date).aggregate(
            m=Max("sequence"))["m"]
        return (current or 0) + 1

    def render(self, file_name: str) -> str:
        return self.file_format.render([r.line for r in self.rows], file_name)


def mask_account(value: str) -> str:
    value = value or ""
    return "*" * max(0, len(value) - 4) + value[-4:]


def _clean_cell(value) -> str:
    """Cell text minus whitespace and NPCI's leading "force text" apostrophe
    (the same cleaning as reconciliation.presence)."""
    return str(value if value is not None else "").strip().removeprefix("'").strip()


def _raw_cells(facts) -> dict[int, dict]:
    """{fact pk: raw_cells} from each fact's staging row, or its staging
    History copy once archived (same lineage as TransactionFact.staging_row)."""
    by_type: dict[str, list[TransactionFact]] = defaultdict(list)
    for fact in facts:
        if fact.staging_row_id and fact.source_register_type in STAGING_MODEL_BY_REGISTER_TYPE:
            by_type[fact.source_register_type].append(fact)
    out: dict[int, dict] = {}
    for register_type, group in by_type.items():
        staging_model = STAGING_MODEL_BY_REGISTER_TYPE[register_type]
        ids = [f.staging_row_id for f in group]
        cells = dict(staging_model.objects.filter(id__in=ids).values_list("id", "raw_cells"))
        missing = [i for i in ids if i not in cells]
        if missing:
            history = HISTORY_MODEL_BY_STAGING_MODEL[staging_model]
            for original_id, raw in history.objects.filter(original_id__in=missing).order_by("archived_at") \
                    .values_list("original_id", "raw_cells"):
                cells[original_id] = raw
        for fact in group:
            out[fact.pk] = cells.get(fact.staging_row_id) or {}
    return out


def _reply(case: ExceptionCase, actions: dict, rule_defaults: bool) -> Reply:
    """The reply the case's row would carry now (Amendment 2)."""
    rule_reply = Reply(case.default_action_type, case.default_response_code, None, ReplySource.RULE)
    if rule_defaults:
        return rule_reply
    if case.status in DECIDED:
        action, source = actions.get((case.pk, Decision.APPROVED)), ReplySource.APPROVED
    elif case.status == CaseStatus.PENDING_APPROVAL:
        action, source = actions.get((case.pk, Decision.PENDING)), ReplySource.SUBMITTED
    else:
        return rule_reply
    if action is None:  # cannot happen through the workflow services
        return rule_reply
    return Reply(action.action_type, action.response_code, action, source)


def _rejected(case: ExceptionCase, latest: dict) -> bool:
    """Back with the Maker after the Checker rejected the reply proposed under
    the case's current rule, with nothing submitted since."""
    action = latest.get(case.pk)
    return (case.status in NOT_SUBMITTED and action is not None and action.decision == Decision.REJECTED
            and action.rule_code == case.rule_code)


def approval_required(case: ExceptionCase, reply: Reply) -> str | None:
    """Why this reply may go in only once a Checker has approved it, or None."""
    if case.rule_code in APPROVAL_REQUIRED_RULES:
        return f"{case.rule_code}: {APPROVAL_REQUIRED_RULES[case.rule_code]}"
    spec = ACTION_SPECS.get(reply.action_type)
    if spec is not None and spec.requires_cbs_reversal_evidence:
        return f"{reply.action_type}: {EVIDENCE_REASON}"
    return None


def load_actions(case_ids) -> tuple[dict[tuple, ProposedAction], dict[int, ProposedAction]]:
    """({(case id, APPROVED|PENDING): the latest such action}, {case id: the
    newest submission}) for _reply / hold_reason, in one query."""
    actions: dict[tuple, ProposedAction] = {}
    latest: dict[int, ProposedAction] = {}
    for action in (ProposedAction.objects.filter(case_id__in=list(case_ids)).select_related("response_code")
                   .order_by("submitted_at", "pk")):
        latest[action.case_id] = action  # the newest submission
        if action.decision in (Decision.APPROVED, Decision.PENDING):
            actions[(action.case_id, action.decision)] = action  # the latest approved one wins
    return actions, latest


def current_reply(case: ExceptionCase, actions: dict) -> Reply:
    """The reply the case's row would carry now (Amendment 2); `actions` from load_actions."""
    return _reply(case, actions, False)


def hold_reason(case: ExceptionCase, reply: Reply, latest: dict) -> tuple[str, str] | None:
    """(Reason, detail) when the case's reply waits for a person before it may
    go into a file (a)-(c) of the module docstring, else None."""
    if case.needs_review:
        return Reason.NEEDS_REVIEW, case.needs_review_reason or ""
    if _rejected(case, latest):
        return Reason.REJECTED, latest[case.pk].decision_remarks or ""
    if case.status not in DECIDED and (why := approval_required(case, reply)):
        return Reason.NEEDS_APPROVAL, why
    return None


def build_line(file_format, case: ExceptionCase, flag: str, code: str, rc: ResponseCode, fact: TransactionFact,
               raw: dict) -> FileLine:
    """The file line for a case's reply (design section 2). Raises FormatError
    when a value cannot be written."""
    if flag == "RRC":
        txn_text = _clean_cell(raw.get(ADJUSTMENT_TXN_DATE_CELL))
        try:
            shtdat = datetime.strptime(txn_text, ADJUSTMENT_TXN_DATE_FORMAT).date()
        except ValueError as exc:
            raise FormatError(f"Adjustment Txndate {txn_text!r} is not dd-mm-yyyy") from exc
    else:
        shtdat = case.business_date
    line = FileLine(rrn=case.rrn, flag=flag, code=code, shtdat=shtdat, amount=fact.amount,
                    account=_clean_cell(raw.get(ACCOUNT_CELL_BY_LEG[LEG_BY_FLAG[flag]])), text=rc.description)
    file_format.check_line(line)
    return line


def _candidates(module: PaymentModule, file_date: date):
    adjustment_groups = RegisterPresence.objects.filter(
        leg=LEG_BY_FLAG["RRC"], present=True, match_group__payment_module=module,
        transaction_record__business_date=file_date,
    ).values("match_group_id")
    return (ExceptionCase.objects.filter(payment_module=module, case_type=CaseType.TRANSACTION)
            .exclude(status=CaseStatus.CLOSED_SUPERSEDED)
            .filter(Q(business_date=file_date) | Q(match_group_id__in=adjustment_groups))
            .select_related("default_response_code")
            .order_by("business_date", "rrn"))


def plan_file(module: PaymentModule, file_date: date, *, rule_defaults: bool = False) -> FilePlan:
    """What the next file for `file_date` would hold now. Saves nothing."""
    fmt = format_for(module)
    if fmt is None:
        raise InvalidInput(f"No NPCI file format is defined for {module.code}")
    plan = FilePlan(module=module, file_date=file_date, file_format=fmt)
    cases = list(_candidates(module, file_date))
    case_ids = [c.pk for c in cases]

    actions, latest = ({}, {}) if rule_defaults else load_actions(case_ids)
    presences: dict[tuple, RegisterPresence] = {}
    for p in (RegisterPresence.objects.filter(match_group_id__in={c.match_group_id for c in cases},
                                              leg__in=set(LEG_BY_FLAG.values()), present=True,
                                              transaction_record__isnull=False)
              .select_related("transaction_record")):
        presences[(p.match_group_id, p.leg)] = p
    sent = {} if rule_defaults else {
        r.case_id: r for r in NpciFileRow.objects.filter(case_id__in=case_ids).select_related("npci_file")}

    placed = []  # (case, reply, flag, fact or None)
    for case in cases:
        reply = _reply(case, actions, rule_defaults)
        flag = reply.flag
        row = sent.get(case.pk)
        if row is not None:
            if row.npci_file.file_date == file_date:
                plan.already_sent.append((case, row))
                if case.needs_review or (flag, reply.code) != (row.flag, row.code):
                    plan.changed_since_included.append((case, row))
            continue  # a case is never included twice (Amendment 1)
        if flag not in LEG_BY_FLAG:
            continue  # not a reply to NPCI (chargeback, CBS entry, close without action...)
        presence = presences.get((case.match_group_id, LEG_BY_FLAG[flag]))
        fact = presence.transaction_record if presence else None
        on_date = fact.business_date if (flag == "RRC" and fact is not None) else case.business_date
        if on_date != file_date:
            continue  # belongs to another date's file (design section 2)
        placed.append((case, reply, flag, fact))

    if not rule_defaults:  # included earlier, then closed by a re-run (not among the candidates)
        for row in (NpciFileRow.objects.filter(npci_file__payment_module=module, npci_file__file_date=file_date,
                                               case__status=CaseStatus.CLOSED_SUPERSEDED)
                    .select_related("case", "npci_file")):
            plan.changed_since_included.append((row.case, row))

    cells = _raw_cells(fact for *_, fact in placed if fact is not None)
    manual_credit_pending = 0
    for case, reply, flag, fact in placed:
        rc, code = reply.response_code, reply.code
        if not rule_defaults:
            held = hold_reason(case, reply, latest)
            if held:
                plan.excluded.append(Excluded(case, flag, code, held[0], held[1]))
                continue
        if rc is None or code is None:
            label = f"{flag} {rc.description}" if rc is not None else flag
            plan.excluded.append(Excluded(case, flag, None, Reason.NO_NUMERIC_CODE, label))
            continue
        if fact is None:
            plan.excluded.append(Excluded(case, flag, code, Reason.NO_NPCI_ROW,
                                          f"No NPCI {LEG_BY_FLAG[flag]} row is attached to this case"))
            continue
        try:
            line = build_line(plan.file_format, case, flag, code, rc, fact, cells.get(fact.pk, {}))
        except FormatError as exc:
            plan.excluded.append(Excluded(case, flag, code, Reason.BAD_SOURCE_DATA, str(exc)))
            continue
        # held cases never get here (needs review, rejected...); rule_defaults never closes anything
        closes = (not rule_defaults and case.status in auto_close.SYSTEM_CLOSABLE_STATUSES
                  and auto_close.reply_closes_case(case, flag, code))
        plan.rows.append(PlannedRow(case=case, action=reply.action, action_type=reply.action_type, fact=fact,
                                    line=line, source=reply.source, closes_case=closes))
        if reply.action_type in MANUAL_CREDIT_ACTIONS and case.status != CaseStatus.COMPLETED:
            manual_credit_pending += 1

    plan.rows.sort(key=lambda r: plan.file_format.sort_key(r.flag, r.code, r.fact.pk))
    _add_warnings(plan, manual_credit_pending, rule_defaults)
    return plan


def _add_warnings(plan: FilePlan, manual_credit_pending: int, rule_defaults: bool) -> None:
    no_code = Counter(e.detail for e in plan.excluded if e.reason == Reason.NO_NUMERIC_CODE)
    if no_code:
        listed = ", ".join(f"{label}: {n}" for label, n in sorted(no_code.items()))
        plan.warnings.append(
            f"{sum(no_code.values())} case(s) left out because their reply code has no numeric NPCI code yet "
            f"({listed}). They go into a later file automatically once the code is filled in."
        )
    needs_approval = Counter(e.case.rule_code for e in plan.excluded if e.reason == Reason.NEEDS_APPROVAL)
    if needs_approval:
        listed = ", ".join(f"{rule}: {n}" for rule, n in sorted(needs_approval.items()))
        plan.warnings.append(
            f"{sum(needs_approval.values())} case(s) left out until a Checker approves their reply ({listed}). "
            "These replies need evidence or a person's decision first: RRC 501 without a new credit (R06b) needs "
            "the CBS reversal attached, R07x is past the 35-day chargeback limit, R04b needs the customer's "
            "account checked. They go into a later file of this date once approved."
        )
    rejected = sum(1 for e in plan.excluded if e.reason == Reason.REJECTED)
    if rejected:
        plan.warnings.append(
            f"{rejected} case(s) left out because the Checker rejected their last reply. They go into a later "
            "file once the Maker submits a new reply."
        )
    review = sum(1 for e in plan.excluded if e.reason == Reason.NEEDS_REVIEW)
    if review:
        plan.warnings.append(
            f"{review} case(s) flagged for review after a re-run are left out until a Checker clears the review."
        )
    if manual_credit_pending and not rule_defaults:
        plan.warnings.append(
            f"{manual_credit_pending} row(s) tell NPCI the customer was credited by hand (DRC 103 / manual credit "
            "+ RRC 501) and are not marked done yet: make sure the credit is made before uploading."
        )
    changed = len({case.pk for case, _row in plan.changed_since_included})
    if changed:
        plan.warnings.append(
            f"{changed} case(s) already included in an earlier file have changed since (flagged for review, "
            "another reply chosen, or closed by a re-run). A file already generated never changes and a case is "
            "never included in two files: any correction is made with NPCI outside the app."
        )
    closing = Counter(r.case.rule_code for r in plan.rows if r.closes_case)
    if closing:
        listed = ", ".join(f"{rule}: {n}" for rule, n in sorted(closing.items()))
        plan.warnings.append(
            f"{sum(closing.values())} case(s) with a no-money reply ({listed}) are closed by the system when "
            "this file is generated: no money moves at the bank, so no Maker or Checker step (owner 2026-11-09)."
        )


# ---------------------------------------------------------------------------
# generate / download
# ---------------------------------------------------------------------------
def _require(user, roles, what: str) -> None:
    if not user_roles(user).intersection(roles):
        raise RoleRequired(f"Only {' / '.join(roles)} may {what}")


@transaction.atomic
def generate_file(user, module: PaymentModule, file_date: date, *, confirm_partial: bool = False,
                  remarks: str = "", source: str = "") -> NpciFile:
    """Creates the next file for the date (sequence 1, then _2, _3...) from
    the cases with a reply that are not yet included in a file (Amendment 2:
    no approval needed first, except for APPROVAL_REQUIRED_RULES / evidence
    replies). Refused (WrongState) when nothing is left to include, or when
    cases are left out and confirm_partial is not set."""
    _require(user, GENERATE_ROLES, "generate the NPCI file")
    require_past_business_date(file_date)  # owner 2026-10-23: on day D ops reconcile D-1
    PaymentModule.objects.select_for_update().get(pk=module.pk)  # one generation at a time per module
    plan = plan_file(module, file_date)
    if not plan.rows:
        errors = {"rows": ["No reply is waiting to be included in a file for this date."]}
        if plan.excluded:
            errors["not_included"] = [f"{n} case(s): {Reason(r).label}" for r, n in plan.excluded_counts().items()]
        raise WrongState("Nothing to include in an NPCI file for this date", errors)
    if plan.partial and not confirm_partial:
        raise WrongState(
            f"{len(plan.excluded)} case(s) for this date are left out; confirm to generate a partial file",
            {"confirm_partial": ["Confirm that the file is partial to continue."]},
        )
    sequence = plan.next_sequence()
    file_name = plan.file_format.file_name(file_date, sequence)
    content = plan.render(file_name)
    data = content.encode("ascii")
    now = timezone.now()
    npci_file = NpciFile.objects.create(
        payment_module=module, file_date=file_date, sequence=sequence, file_format=plan.file_format.code,
        file_name=file_name, content=content, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
        row_count=len(plan.rows), total_amount=plan.total_amount,
        counts=[{**c, "amount": f"{c['amount']:.2f}"} for c in plan.counts()],
        partial=plan.partial, not_included=[excluded_payload(e) for e in plan.excluded],
        warnings=plan.warnings, remarks=(remarks or "").strip(), generated_by=user,
        generated_by_username=user.get_username(), generated_at=now,
    )
    NpciFileRow.objects.bulk_create([
        NpciFileRow(npci_file=npci_file, line=i, case=r.case, action=r.action, action_type=r.action_type,
                    reply_source=r.source, case_business_date=r.case.business_date, rrn=r.line.rrn, flag=r.flag,
                    code=r.code, shtdat=r.line.shtdat, amount=r.line.amount,
                    account_masked=mask_account(r.line.account), text=r.line.text, dispute_fact_id=r.fact.pk)
        for i, r in enumerate(plan.rows, start=1)
    ])
    # Owner 2026-11-09: the no-money cases of this file close now, in this transaction.
    closure = auto_close.close_included_cases(npci_file)
    log_operation(OperationType.NPCI_FILE_GENERATED, user=user, payment_module=module, business_date=file_date,
                  source=source, details={
                      "npci_file_id": npci_file.pk, "file_name": file_name, "sequence": sequence,
                      "sha256": npci_file.sha256, "rows": npci_file.row_count,
                      "total_amount": f"{npci_file.total_amount:.2f}", "partial": npci_file.partial,
                      "not_included": plan.excluded_counts(), "reply_sources": plan.source_counts(),
                      "closed_by_system": len(closure.closed),
                      "closed_by_system_rules": closure.closed_by("rule_code"),
                  })
    return npci_file


def excluded_payload(e: Excluded) -> dict:
    return {"case_id": e.case.pk, "case_business_date": e.case.business_date.isoformat(), "rrn": e.case.rrn,
            "rule_code": e.case.rule_code, "status": e.case.status, "flag": e.flag, "code": e.code,
            "reason": e.reason, "reason_label": Reason(e.reason).label, "detail": e.detail}


def can_download(user) -> bool:
    return bool(user_roles(user).intersection(DOWNLOAD_ROLES))


def download_file(user, npci_file: NpciFile, *, source: str = "") -> bytes:
    """The stored bytes; every download is logged (the file holds account numbers)."""
    _require(user, DOWNLOAD_ROLES, "download NPCI files")
    log_operation(OperationType.NPCI_FILE_DOWNLOADED, user=user, payment_module=npci_file.payment_module,
                  business_date=npci_file.file_date, source=source,
                  details={"npci_file_id": npci_file.pk, "file_name": npci_file.file_name, "sha256": npci_file.sha256})
    return npci_file.content.encode("ascii")
