"""
Builds the NPCI upload file for one date (docs/npci_file_design_draft_v1.txt,
approved 2026-10-22/23 with Amendment 1).

Which cases go in (design section 3, owner Q3: only Checker-approved
replies). A transaction case gives a row when ALL hold:
  a) status APPROVED or COMPLETED;
  b) its approved action replies to NPCI with a code (DRC, TCC, RET, RRC --
     exception_management.actions.ACTION_SPECS code_flag); flag and code
     come from that action;
  c) the code has a numeric NPCI code (else left out with a warning and
     picked up by a later file once the code is filled in);
  d) it is not flagged "needs review";
  e) its NPCI dispute row belongs to this file's date: DRC / TCC / RET the
     case's own business date; RRC the Adjustment row's Adjdate (design
     section 2);
  f) it was not included in an earlier file (Amendment 1: a case is never
     included twice; a later file for the same date holds only the new
     rows). The app never sends anything to NPCI (owner 2026-10-23): the
     user downloads the file and uploads it to the NPCI portal by hand.

Values (design section 2): bankadjref and shser = the case RRN; Shtdat = the
business date (RRC: the Adjustment's Txndate); adjamt = the dispute row's
amount (RRC: Adjamount); shcrd = the dispute report's account cell, copied as
it is minus NPCI's leading apostrophe; specifyother = the response code's
description in the repository at generation time (Amendment 1, option B).

Cases that would give a row once decided are listed as "not included" with
the reason; generating while any are listed makes a PARTIAL file and needs an
explicit confirmation (owner Q5).

plan_file(..., rule_defaults=True) is the verification path only (the
check_npci_file command): every case is treated as approved with its rule's
pre-filled action. It never saves anything.
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
from npci_files.formats import (
    ACCOUNT_CELL_BY_LEG,
    ADJUSTMENT_TXN_DATE_CELL,
    ADJUSTMENT_TXN_DATE_FORMAT,
    LEG_BY_FLAG,
    FileLine,
    FormatError,
    format_for,
)
from npci_files.models import NpciFile, NpciFileRow
from reconciliation.models import RegisterPresence
from transactions.models import TransactionFact
from transactions.models.history import HISTORY_MODEL_BY_STAGING_MODEL
from transactions.models.staging import STAGING_MODEL_BY_REGISTER_TYPE

GENERATE_ROLES = (Role.CHECKER,)  # owner Q2: "the checker authorises NPCI uploads"
DOWNLOAD_ROLES = (Role.MAKER, Role.CHECKER, Role.OPERATIONS_USER)  # the people who upload (Q2)

DECIDED = (CaseStatus.APPROVED, CaseStatus.COMPLETED)
# Rows telling NPCI the customer was credited by hand (design section 3, Q3).
MANUAL_CREDIT_ACTIONS = (ActionType.DRC_103, ActionType.MANUAL_CREDIT_RRC_501)


class Reason(models.TextChoices):
    """Why a case that would give a row is not in the file."""

    OPEN = "OPEN", "Not submitted yet (open or in progress)"
    PENDING_APPROVAL = "PENDING_APPROVAL", "Waiting for the Checker's approval"
    NEEDS_REVIEW = "NEEDS_REVIEW", "Flagged for review after a re-run"
    NO_NUMERIC_CODE = "NO_NUMERIC_CODE", "The reply code has no numeric NPCI code yet"
    NO_NPCI_ROW = "NO_NPCI_ROW", "No NPCI dispute row for this reply"
    BAD_SOURCE_DATA = "BAD_SOURCE_DATA", "A value cannot be written into the file"


@dataclass
class PlannedRow:
    case: ExceptionCase
    action: ProposedAction | None  # None only in rule-defaults (verification) mode
    action_type: str
    fact: TransactionFact
    line: FileLine

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
        return dict(Counter(e.reason for e in self.excluded))

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


def _intended(case: ExceptionCase, actions: dict, rule_defaults: bool):
    """(action_type, response_code, action) the case's row would carry."""
    if rule_defaults:
        return case.default_action_type, case.default_response_code, None
    if case.status in DECIDED:
        action = actions.get((case.pk, Decision.APPROVED))
    elif case.status == CaseStatus.PENDING_APPROVAL:
        action = actions.get((case.pk, Decision.PENDING))
    else:
        return case.default_action_type, case.default_response_code, None
    if action is None:  # cannot happen through the workflow services
        return case.default_action_type, case.default_response_code, None
    return action.action_type, action.response_code, action


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

    actions: dict[tuple, ProposedAction] = {}
    for action in (ProposedAction.objects.filter(case_id__in=case_ids, decision__in=(Decision.APPROVED, Decision.PENDING))
                   .select_related("response_code").order_by("decided_at", "submitted_at", "pk")):
        actions[(action.case_id, action.decision)] = action  # the latest approved one wins
    presences: dict[tuple, RegisterPresence] = {}
    for p in (RegisterPresence.objects.filter(match_group_id__in={c.match_group_id for c in cases},
                                              leg__in=set(LEG_BY_FLAG.values()), present=True,
                                              transaction_record__isnull=False)
              .select_related("transaction_record")):
        presences[(p.match_group_id, p.leg)] = p
    sent = {} if rule_defaults else {
        r.case_id: r for r in NpciFileRow.objects.filter(case_id__in=case_ids).select_related("npci_file")}

    placed = []  # (case, action_type, rc, action, flag, fact or None)
    for case in cases:
        action_type, rc, action = _intended(case, actions, rule_defaults)
        flag = ACTION_SPECS[action_type].code_flag if action_type in ACTION_SPECS else None
        if flag not in LEG_BY_FLAG:
            continue  # not a reply to NPCI (chargeback, CBS entry, close without action...)
        presence = presences.get((case.match_group_id, LEG_BY_FLAG[flag]))
        fact = presence.transaction_record if presence else None
        on_date = fact.business_date if (flag == "RRC" and fact is not None) else case.business_date
        if on_date != file_date:
            continue  # belongs to another date's file (design section 2)
        placed.append((case, action_type, rc, action, flag, fact))

    cells = _raw_cells(fact for *_, fact in placed if fact is not None)
    manual_credit_pending = 0
    for case, action_type, rc, action, flag, fact in placed:
        code = rc.code if rc is not None else None
        if not rule_defaults and case.pk in sent:
            plan.already_sent.append((case, sent[case.pk]))
            continue
        if not rule_defaults:
            reason = (Reason.OPEN if case.status in (CaseStatus.OPEN, CaseStatus.IN_PROGRESS)
                      else Reason.PENDING_APPROVAL if case.status == CaseStatus.PENDING_APPROVAL
                      else Reason.NEEDS_REVIEW if case.needs_review else None)
            if reason:
                plan.excluded.append(Excluded(case, flag, code, reason))
                continue
        if rc is None or code is None:
            label = f"{flag} {rc.description}" if rc is not None else flag
            plan.excluded.append(Excluded(case, flag, None, Reason.NO_NUMERIC_CODE, label))
            continue
        if fact is None:
            plan.excluded.append(Excluded(case, flag, code, Reason.NO_NPCI_ROW,
                                          f"No NPCI {LEG_BY_FLAG[flag]} row is attached to this case"))
            continue
        leg = LEG_BY_FLAG[flag]
        raw = cells.get(fact.pk, {})
        try:
            if flag == "RRC":
                txn_text = _clean_cell(raw.get(ADJUSTMENT_TXN_DATE_CELL))
                try:
                    shtdat = datetime.strptime(txn_text, ADJUSTMENT_TXN_DATE_FORMAT).date()
                except ValueError as exc:
                    raise FormatError(f"Adjustment Txndate {txn_text!r} is not dd-mm-yyyy") from exc
            else:
                shtdat = case.business_date
            line = FileLine(rrn=case.rrn, flag=flag, code=code, shtdat=shtdat, amount=fact.amount,
                            account=_clean_cell(raw.get(ACCOUNT_CELL_BY_LEG[leg])), text=rc.description)
            plan.file_format.check_line(line)
        except FormatError as exc:
            plan.excluded.append(Excluded(case, flag, code, Reason.BAD_SOURCE_DATA, str(exc)))
            continue
        plan.rows.append(PlannedRow(case=case, action=action, action_type=action_type, fact=fact, line=line))
        if action_type in MANUAL_CREDIT_ACTIONS and case.status == CaseStatus.APPROVED:
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
    if manual_credit_pending and not rule_defaults:
        plan.warnings.append(
            f"{manual_credit_pending} row(s) tell NPCI the customer was credited by hand (DRC 103 / manual credit "
            "+ RRC 501) and are not marked done yet: make sure the credit is made before uploading."
        )
    reviewed = sum(1 for case, _row in plan.already_sent if case.needs_review)
    if reviewed:
        plan.warnings.append(
            f"{reviewed} case(s) already included in an earlier file were changed by a re-run and are flagged "
            "for review. A file already generated never changes and a case is never included in two files: "
            "any correction is made with NPCI outside the app."
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
    the cases approved and not yet included in a file. Refused (WrongState)
    when nothing is left to include, or when cases are left out and confirm_partial is not set."""
    _require(user, GENERATE_ROLES, "generate the NPCI file")
    require_past_business_date(file_date)  # owner 2026-10-23: on day D ops reconcile D-1
    PaymentModule.objects.select_for_update().get(pk=module.pk)  # one generation at a time per module
    plan = plan_file(module, file_date)
    if not plan.rows:
        errors = {"rows": ["No approved case is waiting to be included in a file for this date."]}
        if plan.excluded:
            errors["not_included"] = [f"{n} case(s): {Reason(r).label}" for r, n in plan.excluded_counts().items()]
        raise WrongState("Nothing to include in an NPCI file for this date", errors)
    if plan.partial and not confirm_partial:
        raise WrongState(
            f"{len(plan.excluded)} case(s) for this date are not included; confirm to generate a partial file",
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
        NpciFileRow(npci_file=npci_file, line=i, case=r.case, action=r.action, case_business_date=r.case.business_date,
                    rrn=r.line.rrn, flag=r.flag, code=r.code, shtdat=r.line.shtdat, amount=r.line.amount,
                    account_masked=mask_account(r.line.account), text=r.line.text, dispute_fact_id=r.fact.pk)
        for i, r in enumerate(plan.rows, start=1)
    ])
    log_operation(OperationType.NPCI_FILE_GENERATED, user=user, payment_module=module, business_date=file_date,
                  source=source, details={
                      "npci_file_id": npci_file.pk, "file_name": file_name, "sequence": sequence,
                      "sha256": npci_file.sha256, "rows": npci_file.row_count,
                      "total_amount": f"{npci_file.total_amount:.2f}", "partial": npci_file.partial,
                      "not_included": plan.excluded_counts(),
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
