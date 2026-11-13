"""
The CBS credit file ("rev" file) of a business date, and its approval by
upload (owner decisions 2026-11-11, plan "generate the CBS rev credit file,
approve it in one step, then the SSKUPI file"; ops' sample
`upi rev 02112026.xls`).

  (Owner 2026-11-12: anyone who can sign in may do every step below,
  including uploading a file they generated; the role limits named here
  apply only with settings.ROLES_ENFORCED = True.)
  1. A Maker or Operations User generates the file for a date
     (generate_voucher) and downloads it (download_voucher, logged).
  2. They check it in Excel: they may delete rows and fill in or correct
     account numbers, nothing else.
  3. A Checker who did not generate it uploads the checked file
     (approve_upload). All or nothing: every row is checked first; then, in
     one transaction, every case the file still lists is approved and marked
     done with the file name as its CBS reference
     (exception_management.services.approve_by_credit_file). Rows deleted
     from the file leave their cases open, for a later version.
  4. Ops re-run the date and generate the SSKUPI file as before: R02 / R04b
     cases now carry an approved DRC 103 / TCC 103 and go into it; R07r has no
     SSKUPI reply.

Which cases (MONEY_ACTIONS): current transaction cases (OPEN / IN_PROGRESS /
PENDING_APPROVAL / APPROVED) of the date whose rule is
  R07r  Refund Reversal Confirmation, SSK = Remitter (rule set v3.4): credit
        through the file, action REFUND_CUSTOMER (no NPCI reply);
  R02   DRC, debited and not reversed: manual credit, DRC 103;
  R04b  Timeout, credited and reversed in CBS: TCC 103 (manual credit), but
        ONLY once a Checker has approved TCC 103 for it -- R04b is Pending
        Investigation, and the account must be checked first;
and whose reply is that credit action: the rule's suggestion (OPEN /
IN_PROGRESS), the Maker's submission or the approved one
(generator.current_reply). Never: R06b, R06 (closed by the system once its
RRC 501 is in an SSKUPI file), a case flagged for review, one whose last
reply the Checker rejected, or a Pending Investigation case nobody decided.
A case, and an RRN, already credited through an APPROVED file of any date is
never listed again (CreditVoucherCredit, DB constraints).

Placement: like the SSKUPI file, R02 / R04b rows go in the file of the case's
business date; R07r rows in the file of the Adjustment row's date (Adjdate).

File layout (ops' sheet, CSV): Sr. No., Account NBR, Debit Amount, Credit
Amount, Instrument Number, Instrument Date, Narration, plus a last column
Check, filled only on rows that need the user's attention.
  - Sr. No. 1 debits the GL account (settings.CREDIT_VOUCHER_DEBIT_ACCOUNT,
    GL 1535) with the total, narration
    "UPI TO RRC DRC TCC REVERSAL DATED ON NOV 02" (the file's business
    date, owner 2026-11-12).
  - Then one credit row per case, narration
    "UPI reversal entry RRN/<rrn>  Dated  <dmmyyyy>" (two spaces around
    Dated; the day without a leading zero, e.g. 1112026). The date is the
    transaction date: the Adjustment report's Txndate (R07r), the DRC /
    Timeout report's TXN Date (R02 / R04b).
  - Accounts are written with a leading apostrophe, as NPCI writes its CSVs,
    so Excel keeps all 16 digits; the apostrophe is removed on upload.
  - R07r: the Adjustment report's "Remitter Account number". The DRC and
    Timeout reports carry no CBS account: the cell is left blank and Check
    asks for it. An account that is missing or not 16 digits (e.g. an old
    12-digit number) is written as it is and flagged in Check; no conversion
    is attempted (open item for the owner).
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import models, transaction
from django.db.models import Max, Q
from django.utils import timezone

from core.models import OperationType, PaymentModule
from core.operations import InvalidInput, RoleRequired, WrongState, log_operation, require_past_business_date
from exception_management import services
from exception_management.actions import ActionType
from exception_management.models import ACTIVE_STATUSES, CaseStatus, CaseType, ExceptionCase
from exception_management.roles import Role, roles_enforced, user_roles
from npci_files import generator
from npci_files.formats import ADJUSTMENT_TXN_DATE_CELL, ADJUSTMENT_TXN_DATE_FORMAT
from npci_files.models import (
    CreditVoucher,
    CreditVoucherApproval,
    CreditVoucherCredit,
    CreditVoucherRow,
    CreditVoucherStatus,
)
from reconciliation.models import RegisterPresence

# Owner 2026-11-12: anyone who can sign in may generate, download and upload, including the
# user who generated the file. These limits apply only with settings.ROLES_ENFORCED = True
# (exception_management.roles: switched off, every user holds every role).
GENERATE_ROLES = (Role.MAKER, Role.OPERATIONS_USER)
DOWNLOAD_ROLES = (Role.MAKER, Role.OPERATIONS_USER, Role.CHECKER)  # the file holds account numbers
UPLOAD_ROLES = (Role.CHECKER,)

# rule code -> the credit action approved on upload; the NPCI report the row comes from
MONEY_ACTIONS: dict[str, str] = {
    "R07r": ActionType.REFUND_CUSTOMER,
    "R02": ActionType.DRC_103,
    "R04b": ActionType.TCC_103,
}
LEG_BY_RULE = {"R07r": "ADJUSTMENT", "R02": "DRC", "R04b": "TIMEOUT"}
# R04b is Pending Investigation: listed only once a Checker approved TCC 103.
DECISION_FIRST_RULES = ("R04b",)

# Adjustment report column 40 "Remitter Account number" (header of the 2 Nov 2026
# file); DRC / Timeout column 3 "TXN Date" (dd/mm/yyyy).
ADJUSTMENT_REMITTER_ACCOUNT_CELL = "40"
DISPUTE_TXN_DATE_CELL = "3"
DISPUTE_TXN_DATE_FORMAT = "%d/%m/%Y"
ACCOUNT_DIGITS = 16

COLUMNS = ("Sr. No.", "Account NBR", "Debit Amount", "Credit Amount", "Instrument Number", "Instrument Date",
           "Narration")
CHECK_COLUMN = "Check"
HEADER = COLUMNS + (CHECK_COLUMN,)
DEBIT_NARRATION_PREFIX = "UPI TO RRC DRC TCC REVERSAL DATED ON"
CREDIT_NARRATION_RE = re.compile(r"RRN/(\d+)")
MAX_UPLOAD_BYTES = 2 * 1024 * 1024

CHECK_NO_ACCOUNT_REPORT = "No CBS account in the NPCI report: enter the customer's 16-digit account"
CHECK_NO_ACCOUNT = "Account missing in the Adjustment report: enter the customer's 16-digit account"
CHECK_BAD_ACCOUNT = "Account in the Adjustment report is not 16 digits (old format?): enter the 16-digit CBS account"


class Reason(models.TextChoices):
    """Why a case of a credit rule is not in the file."""

    NEEDS_REVIEW = "NEEDS_REVIEW", "Flagged for review after a re-run"
    REJECTED = "REJECTED", "The Checker rejected the last reply; waiting for the Maker"
    NEEDS_DECISION = "NEEDS_DECISION", "Pending investigation: a Checker must approve TCC 103 (manual credit) first"
    OTHER_REPLY = "OTHER_REPLY", "The case carries another reply, not the credit"
    ALREADY_CREDITED = "ALREADY_CREDITED", "This RRN was already credited through an approved credit file"
    NO_NPCI_ROW = "NO_NPCI_ROW", "No NPCI dispute row for this case"
    BAD_SOURCE_DATA = "BAD_SOURCE_DATA", "The transaction date cannot be read"
    DUPLICATE_RRN = "DUPLICATE_RRN", "Another case of this file has the same RRN"


@dataclass
class PlannedCredit:
    case: ExceptionCase
    action_type: str
    fact_id: int
    rrn: str
    txn_date: date
    amount: Decimal
    account: str
    check_note: str = ""

    @property
    def narration(self) -> str:
        return credit_narration(self.rrn, self.txn_date)


@dataclass
class Excluded:
    case: ExceptionCase
    reason: str
    detail: str = ""

    def payload(self) -> dict:
        c = self.case
        return {"case_id": c.pk, "case_business_date": c.business_date.isoformat(), "rrn": c.rrn,
                "rule_code": c.rule_code, "status": c.status, "reason": self.reason,
                "reason_label": Reason(self.reason).label, "detail": self.detail}


@dataclass
class VoucherPlan:
    module: PaymentModule
    voucher_date: date
    debit_account: str
    rows: list[PlannedCredit] = field(default_factory=list)
    excluded: list[Excluded] = field(default_factory=list)

    @property
    def total_amount(self) -> Decimal:
        return sum((r.amount for r in self.rows), Decimal("0"))

    @property
    def narration(self) -> str:
        return debit_narration(self.voucher_date)

    @property
    def rows_to_check(self) -> int:
        return sum(1 for r in self.rows if r.check_note)

    def counts(self) -> list[dict]:
        out: dict[str, dict] = {}
        for r in self.rows:
            c = out.setdefault(r.case.rule_code, {"rule_code": r.case.rule_code, "rows": 0, "amount": Decimal("0")})
            c["rows"] += 1
            c["amount"] += r.amount
        return [{**c, "amount": f"{c['amount']:.2f}"} for c in out.values()]

    def excluded_counts(self) -> dict[str, int]:
        return dict(Counter(str(e.reason) for e in self.excluded))

    def warnings(self) -> list[str]:
        out = []
        if self.rows_to_check:
            out.append(f"{self.rows_to_check} row(s) need an account number filled in or corrected (see the Check "
                       "column) before the file is uploaded.")
        if self.excluded:
            listed = ", ".join(f"{Reason(r).label}: {n}" for r, n in sorted(self.excluded_counts().items()))
            out.append(f"{len(self.excluded)} case(s) of the credit rules are not in the file ({listed}).")
        return out

    def next_version(self) -> int:
        current = CreditVoucher.objects.filter(payment_module=self.module, voucher_date=self.voucher_date).aggregate(
            m=Max("version"))["m"]
        return (current or 0) + 1

    def render(self) -> str:
        return render_file(self.debit_account, self.narration, [
            (r.account, r.amount, r.narration, r.check_note) for r in self.rows])


# ---------------------------------------------------------------------------
# text of the file
# ---------------------------------------------------------------------------
def file_name(voucher_date: date, version: int) -> str:
    base = f"upi rev {voucher_date:%d%m%Y}"  # ops' own name, e.g. "upi rev 02112026.xls"
    return f"{base}.csv" if version == 1 else f"{base}_{version}.csv"


def dmmyyyy(d: date) -> str:
    """The narration date: day without a leading zero, e.g. 1112026, 24072026."""
    return f"{d.day}{d:%m%Y}"


def credit_narration(rrn: str, txn_date: date) -> str:
    return f"UPI reversal entry RRN/{rrn}  Dated  {dmmyyyy(txn_date)}"


def debit_narration(voucher_date: date) -> str:
    """"UPI TO RRC DRC TCC REVERSAL DATED ON NOV 02": the reconciliation
    (business) date of the file (owner 2026-11-12)."""
    return f"{DEBIT_NARRATION_PREFIX} {voucher_date:%b %d}".upper()


def _account_cell(account: str) -> str:
    return f"'{account}" if account else ""


def render_file(debit_account: str, narration: str, credits) -> str:
    """`credits`: [(account, amount, narration, check note)] in file order."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(HEADER)
    total = sum((Decimal(a) for _acct, a, _n, _c in credits), Decimal("0"))
    w.writerow([1, _account_cell(debit_account), f"{total:.2f}", "", "", "", narration, ""])
    for i, (account, amount, text, note) in enumerate(credits, start=2):
        w.writerow([i, _account_cell(account), "", f"{Decimal(amount):.2f}", "", "", text, note])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# which cases
# ---------------------------------------------------------------------------
def clean_account(value) -> str:
    """Cell text minus whitespace and the leading "force text" apostrophe."""
    return str(value if value is not None else "").strip().removeprefix("'").strip()


def account_ok(account: str) -> bool:
    return len(account) == ACCOUNT_DIGITS and account.isdigit()


def credited_rrns(module) -> dict[str, str]:
    """{rrn: file name} credited through an APPROVED credit file, any date."""
    return dict(CreditVoucherCredit.objects.filter(payment_module=module)
                .values_list("rrn", "voucher_row__voucher__file_name"))


def _candidates(module, d: date):
    adjustment_groups = RegisterPresence.objects.filter(
        leg="ADJUSTMENT", present=True, match_group__payment_module=module, transaction_record__business_date=d,
    ).values("match_group_id")
    return (ExceptionCase.objects.filter(payment_module=module, case_type=CaseType.TRANSACTION,
                                         status__in=ACTIVE_STATUSES, rule_code__in=list(MONEY_ACTIONS))
            .filter(Q(business_date=d) | Q(rule_code="R07r", match_group_id__in=adjustment_groups))
            .select_related("payment_module", "default_response_code")
            .order_by("business_date", "rrn", "pk"))


def _txn_date(rule_code: str, fact, raw: dict) -> date | None:
    if rule_code == "R07r":
        text, fmt = clean_account(raw.get(ADJUSTMENT_TXN_DATE_CELL)), ADJUSTMENT_TXN_DATE_FORMAT
    else:
        text, fmt = clean_account(raw.get(DISPUTE_TXN_DATE_CELL)), DISPUTE_TXN_DATE_FORMAT
        if not text:
            return fact.business_date  # DRC / Timeout rows belong to their own transaction date
    try:
        return datetime.strptime(text, fmt).date()
    except ValueError:
        return None


def check_case(case, reply, latest, fact) -> tuple[str, str] | None:
    """(Reason, detail) when an eligible-looking case may not be credited
    now, else None. Used when planning and again, on the locked case, when
    the checked file is uploaded."""
    wanted = MONEY_ACTIONS.get(case.rule_code)
    if case.status not in ACTIVE_STATUSES or wanted is None:
        return Reason.OTHER_REPLY, f"{case.rule_code} {case.status}"
    if case.needs_review:
        return Reason.NEEDS_REVIEW, case.needs_review_reason or ""
    if generator._rejected(case, latest):
        return Reason.REJECTED, latest[case.pk].decision_remarks or ""
    if case.rule_code in DECISION_FIRST_RULES and case.status != CaseStatus.APPROVED:
        return Reason.NEEDS_DECISION, f"now {reply.action_type or 'no reply'} ({case.status})"
    if reply.action_type != wanted:
        return Reason.OTHER_REPLY, f"{reply.action_type or 'no reply'}, not {wanted}"
    if fact is None:
        return Reason.NO_NPCI_ROW, f"No NPCI {LEG_BY_RULE[case.rule_code]} row is attached to this case"
    return None


def _facts(cases) -> dict[int, object]:
    """{case pk: the NPCI dispute fact of its rule's leg}."""
    by_group = {}
    for p in (RegisterPresence.objects.filter(match_group_id__in={c.match_group_id for c in cases},
                                              leg__in=set(LEG_BY_RULE.values()), present=True,
                                              transaction_record__isnull=False)
              .select_related("transaction_record")):
        by_group[(p.match_group_id, p.leg)] = p.transaction_record
    return {c.pk: by_group.get((c.match_group_id, LEG_BY_RULE.get(c.rule_code))) for c in cases}


def plan_voucher(module: PaymentModule, d: date) -> VoucherPlan:
    """What the next credit file for `d` would hold now. Saves nothing."""
    plan = VoucherPlan(module=module, voucher_date=d, debit_account=settings.CREDIT_VOUCHER_DEBIT_ACCOUNT)
    cases = list(_candidates(module, d))
    facts = _facts(cases)
    # R07r rows belong to the file of the Adjustment row's date, the others to the case's date.
    cases = [c for c in cases
             if (facts[c.pk].business_date if (c.rule_code == "R07r" and facts[c.pk] is not None)
                 else c.business_date) == d]
    actions, latest = generator.load_actions([c.pk for c in cases])
    cells = generator._raw_cells(f for f in (facts[c.pk] for c in cases) if f is not None)
    credited = credited_rrns(module)
    seen: set[str] = set()
    for case in cases:
        reply = generator.current_reply(case, actions)
        fact = facts[case.pk]
        why = check_case(case, reply, latest, fact)
        if why is None and case.rrn in credited:
            why = Reason.ALREADY_CREDITED, credited[case.rrn]
        if why is not None:
            if why[0] == Reason.OTHER_REPLY and case.status == CaseStatus.APPROVED:
                continue  # a person decided on another reply: no credit, nothing to report
            plan.excluded.append(Excluded(case, *why))
            continue
        raw = cells.get(fact.pk, {})
        txn_date = _txn_date(case.rule_code, fact, raw)
        if txn_date is None:
            plan.excluded.append(Excluded(case, Reason.BAD_SOURCE_DATA, "transaction date not readable"))
            continue
        if case.rrn in seen:
            plan.excluded.append(Excluded(case, Reason.DUPLICATE_RRN, case.rrn))
            continue
        seen.add(case.rrn)
        if case.rule_code == "R07r":
            account = clean_account(raw.get(ADJUSTMENT_REMITTER_ACCOUNT_CELL))
            note = "" if account_ok(account) else CHECK_BAD_ACCOUNT if account else CHECK_NO_ACCOUNT
        else:
            account, note = "", CHECK_NO_ACCOUNT_REPORT
        plan.rows.append(PlannedCredit(case=case, action_type=MONEY_ACTIONS[case.rule_code], fact_id=fact.pk,
                                       rrn=case.rrn, txn_date=txn_date, amount=fact.amount, account=account,
                                       check_note=note))
    # File order: R07r first (the confirmations), then DRC, then Timeout; by date and RRN inside.
    order = {code: i for i, code in enumerate(MONEY_ACTIONS)}
    plan.rows.sort(key=lambda r: (order[r.case.rule_code], r.txn_date, r.rrn))
    return plan


# ---------------------------------------------------------------------------
# status, generate, download
# ---------------------------------------------------------------------------
def _require(user, roles, what: str) -> None:
    if not user_roles(user).intersection(roles):
        raise RoleRequired(f"Only {' / '.join(roles)} may {what}")


def can(user, roles) -> bool:
    return bool(user_roles(user).intersection(roles))


def voucher_status(v: CreditVoucher) -> str:
    if CreditVoucherApproval.objects.filter(voucher=v).exists():
        return CreditVoucherStatus.APPROVED
    if CreditVoucher.objects.filter(payment_module_id=v.payment_module_id, voucher_date=v.voucher_date,
                                    version__gt=v.version).exists():
        return CreditVoucherStatus.SUPERSEDED
    return CreditVoucherStatus.GENERATED


@transaction.atomic
def generate_voucher(user, module: PaymentModule, d: date, *, remarks: str = "", source: str = "") -> CreditVoucher:
    """Creates the next version of the date's credit file. An earlier version
    that was not approved becomes SUPERSEDED (it can no longer be uploaded).
    Refused (WrongState) when no case is waiting for a credit."""
    _require(user, GENERATE_ROLES, "generate the CBS credit file")
    require_past_business_date(d)
    PaymentModule.objects.select_for_update().get(pk=module.pk)  # one generation / approval at a time
    plan = plan_voucher(module, d)
    if not plan.rows:
        errors = {"rows": ["No case of this date is waiting for a CBS credit."]}
        if plan.excluded:
            errors["not_included"] = [f"{n} case(s): {Reason(r).label}" for r, n in plan.excluded_counts().items()]
        raise WrongState("Nothing to include in a CBS credit file for this date", errors)
    version = plan.next_version()
    name = file_name(d, version)
    content = plan.render()
    data = content.encode("utf-8")
    superseded = [v.file_name for v in CreditVoucher.objects.filter(payment_module=module, voucher_date=d)
                  if voucher_status(v) == CreditVoucherStatus.GENERATED]
    voucher = CreditVoucher.objects.create(
        payment_module=module, voucher_date=d, version=version, file_name=name, content=content,
        sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), debit_account=plan.debit_account,
        narration=plan.narration, row_count=len(plan.rows), total_amount=plan.total_amount,
        rows_to_check=plan.rows_to_check, counts=plan.counts(),
        not_included=[e.payload() for e in plan.excluded], warnings=plan.warnings(),
        remarks=(remarks or "").strip(), generated_by=user, generated_by_username=user.get_username(),
        generated_at=timezone.now(),
    )
    CreditVoucherRow.objects.bulk_create([
        CreditVoucherRow(voucher=voucher, line=i, case=r.case, rule_code=r.case.rule_code, action_type=r.action_type,
                         rrn=r.rrn, txn_date=r.txn_date, amount=r.amount, account=r.account,
                         check_note=r.check_note, dispute_fact_id=r.fact_id)
        for i, r in enumerate(plan.rows, start=1)
    ])
    log_operation(OperationType.CREDIT_VOUCHER_GENERATED, user=user, payment_module=module, business_date=d,
                  source=source, details={
                      "credit_voucher_id": voucher.pk, "file_name": name, "version": version,
                      "sha256": voucher.sha256, "rows": voucher.row_count,
                      "total_amount": f"{voucher.total_amount:.2f}", "rows_to_check": voucher.rows_to_check,
                      "by_rule": {c["rule_code"]: c["rows"] for c in voucher.counts},
                      "not_included": plan.excluded_counts(), "superseded": superseded,
                  })
    return voucher


def download_voucher(user, voucher: CreditVoucher, *, source: str = "") -> bytes:
    """The stored bytes; every download is logged (the file holds account numbers)."""
    _require(user, DOWNLOAD_ROLES, "download the CBS credit file")
    log_operation(OperationType.CREDIT_VOUCHER_DOWNLOADED, user=user, payment_module=voucher.payment_module,
                  business_date=voucher.voucher_date, source=source,
                  details={"credit_voucher_id": voucher.pk, "file_name": voucher.file_name,
                           "sha256": voucher.sha256})
    return voucher.content.encode("utf-8")


# ---------------------------------------------------------------------------
# upload of the checked file = approval
# ---------------------------------------------------------------------------
@dataclass
class UploadedRow:
    line: int  # line in the uploaded file (1 = the header)
    sr_no: str
    account: str
    debit: Decimal | None
    credit: Decimal | None
    narration: str


@dataclass
class ApprovalResult:
    approval: CreditVoucherApproval
    approved: list[CreditVoucherRow]
    left_open: list[CreditVoucherRow]


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _norm(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _amount(text: str) -> tuple[Decimal | None, bool]:
    """(amount or None when blank / zero, readable)."""
    text = (text or "").strip().replace(",", "")
    if not text:
        return None, True
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None, False
    return (value if value != 0 else None), True


def parse_upload(data: bytes) -> tuple[list[UploadedRow], list[str]]:
    """The rows of an uploaded file, and the file-level errors."""
    if not data:
        return [], ["The file is empty."]
    if len(data) > MAX_UPLOAD_BYTES:
        return [], [f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."]
    lines = list(csv.reader(io.StringIO(_decode(data), newline="")))
    numbered = [(i, row) for i, row in enumerate(lines, start=1) if any(c.strip() for c in row)]
    if not numbered:
        return [], ["The file has no rows."]
    header_line, header = numbered[0]
    header = [c.strip() for c in header]
    while header and not header[-1]:
        header.pop()
    expected = [_norm(c) for c in COLUMNS]
    got = [_norm(c) for c in header]
    if got[:len(expected)] != expected or got[len(expected):] not in ([], [_norm(CHECK_COLUMN)]):
        return [], [f"The first row must be the column names {', '.join(COLUMNS)} (and optionally "
                    f"{CHECK_COLUMN}); the file has {', '.join(header) or 'none'}. Upload the file as it was "
                    "downloaded, saved as CSV."]
    rows, errors = [], []
    for i, row in numbered[1:]:
        row = [c.strip() for c in row] + [""] * len(HEADER)
        debit, ok_debit = _amount(row[2])
        credit, ok_credit = _amount(row[3])
        if not (ok_debit and ok_credit):
            errors.append(f"Line {i}: the debit or credit amount is not a number.")
            continue
        rows.append(UploadedRow(line=i, sr_no=row[0], account=clean_account(row[1]), debit=debit, credit=credit,
                                narration=row[6]))
    return rows, errors


def _row_error(row: UploadedRow, rrn: str | None, *messages) -> dict:
    return {"line": row.line, "sr_no": row.sr_no, "rrn": rrn, "errors": list(messages)}


def validate_upload(voucher: CreditVoucher, data: bytes) -> tuple[list[tuple[CreditVoucherRow, str]], dict]:
    """[(voucher row, account as uploaded)] for the credit rows of the file,
    and {field: [errors]} (empty when the file is acceptable). Checks: every
    RRN (from the narration) is a row of this voucher, once; its amount and
    narration are unchanged; its account is 16 digits; exactly one debit row,
    on the GL account, equal to the sum of the credits."""
    rows, file_errors = parse_upload(data)
    if file_errors:
        return [], {"file": file_errors}
    by_rrn = {r.rrn: r for r in voucher.rows.all()}
    debit_rows = [r for r in rows if r.debit is not None]
    credit_rows = [r for r in rows if r.debit is None]
    general, row_errors, matched, seen = [], [], [], {}
    if len(debit_rows) != 1:
        general.append(f"The file must have exactly one debit row (the GL {voucher.debit_account} row); "
                       f"it has {len(debit_rows)}.")
    for r in debit_rows:
        problems = []
        if r.account != voucher.debit_account:
            problems.append(f"The debit row must debit the GL account {voucher.debit_account}.")
        if r.credit is not None:
            problems.append("The debit row must have no credit amount.")
        if problems:
            row_errors.append(_row_error(r, None, *problems))
    total = Decimal("0")
    for r in credit_rows:
        found = CREDIT_NARRATION_RE.search(r.narration)
        rrn = found.group(1) if found else None
        target = by_rrn.get(rrn) if rrn else None
        if target is None:
            row_errors.append(_row_error(r, rrn, "This RRN is not in this credit file." if rrn else
                                         "No RRN found in the narration (expected 'RRN/<rrn>')."))
            continue
        problems = []
        if rrn in seen:
            problems.append(f"This RRN is listed twice (also line {seen[rrn]}).")
        seen.setdefault(rrn, r.line)
        if r.credit is None or r.credit != target.amount:
            problems.append(f"The credit amount was changed: the file had {target.amount:.2f}, the upload has "
                            f"{'nothing' if r.credit is None else f'{r.credit:.2f}'}.")
        if _norm(r.narration) != _norm(credit_narration(target.rrn, target.txn_date)):
            problems.append("The narration was changed; only accounts may be filled in or corrected.")
        if not r.account:
            problems.append("The account number is missing.")
        elif not account_ok(r.account):
            problems.append(f"The account number must be {ACCOUNT_DIGITS} digits (it has {len(r.account)} "
                            "characters). If Excel shows it as 2.00005E+15, format the cell as text.")
        if problems:
            row_errors.append(_row_error(r, rrn, *problems))
            continue
        total += r.credit
        matched.append((target, r.account))
    if not credit_rows:
        general.append("The file has no credit rows: nothing to approve.")
    if len(debit_rows) == 1 and not row_errors and debit_rows[0].debit != total:
        general.append(f"The debit row ({debit_rows[0].debit:.2f}) must equal the sum of the credit rows "
                       f"({total:.2f}). After deleting rows, correct the debit amount.")
    errors = {}
    if general:
        errors["file"] = general
    if row_errors:
        errors["rows"] = row_errors
    return matched, errors


def _refuse(user, voucher, file_name_, detail: str, errors: dict, source: str):
    log_operation(OperationType.CREDIT_VOUCHER_REFUSED, user=user, payment_module=voucher.payment_module,
                  business_date=voucher.voucher_date, source=source,
                  details={"credit_voucher_id": voucher.pk, "file_name": voucher.file_name,
                           "uploaded_file_name": file_name_, "reason": detail,
                           "error_count": len(errors.get("file", [])) + len(errors.get("rows", []))})


def approve_upload(user, voucher: CreditVoucher, uploaded_name: str, data: bytes, *, remarks: str = "",
                   source: str = "") -> ApprovalResult:
    """A Checker's upload of the checked file approves every case it lists
    (owner 2026-11-11). All or nothing: refused (InvalidInput, 400, with
    errors per row) when any row is wrong; refused (WrongState, 409) when the
    voucher is approved or superseded or a listed case changed since it was
    generated. Refusals are logged."""
    _require(user, UPLOAD_ROLES, "upload the checked CBS credit file")
    # Owner 2026-11-12: the user who generated the file may upload it, unless roles are enforced.
    if roles_enforced() and voucher.generated_by_id == user.pk:
        raise RoleRequired("The checked file must be uploaded by a Checker other than the user who generated it "
                           f"({voucher.generated_by_username}).")
    uploaded_name = (uploaded_name or voucher.file_name).strip()[:255]
    matched, errors = validate_upload(voucher, data)
    if errors:
        detail = "The checked file was refused; nothing was approved. Correct the rows listed and upload it again."
        _refuse(user, voucher, uploaded_name, detail, errors, source)
        raise InvalidInput(detail, errors)
    try:
        return _approve(user, voucher, uploaded_name, data, matched, remarks=remarks, source=source)
    except (WrongState, InvalidInput) as exc:
        _refuse(user, voucher, uploaded_name, exc.detail, exc.errors, source)
        raise


@transaction.atomic
def _approve(user, voucher, uploaded_name, data, matched, *, remarks, source) -> ApprovalResult:
    PaymentModule.objects.select_for_update().get(pk=voucher.payment_module_id)  # serialises with generation
    status = voucher_status(voucher)
    if status != CreditVoucherStatus.GENERATED:
        raise WrongState(f"{voucher.file_name} is {CreditVoucherStatus(status).label.lower()}; only the latest, "
                         "not yet approved version can be uploaded.", {"voucher": [str(status)]})
    module = voucher.payment_module
    case_ids = sorted(row.case_id for row, _acct in matched)
    cases = {c.pk: c for c in ExceptionCase.objects.select_for_update(of=("self",))
             .select_related("payment_module", "default_response_code").filter(pk__in=case_ids).order_by("pk")}
    actions, latest = generator.load_actions(case_ids)
    facts = _facts(list(cases.values()))
    credited = credited_rrns(module)
    changed = []
    for row, _acct in matched:
        case = cases[row.case_id]
        why = check_case(case, generator.current_reply(case, actions), latest, facts[case.pk])
        if why is None and row.rrn in credited:
            why = Reason.ALREADY_CREDITED, credited[row.rrn]
        if why is None and case.rule_code != row.rule_code:
            why = Reason.OTHER_REPLY, f"rule now {case.rule_code}, was {row.rule_code}"
        if why is not None:
            changed.append({"rrn": row.rrn, "case_id": case.pk, "reason": str(why[0]),
                            "reason_label": Reason(why[0]).label, "detail": why[1]})
    if changed:
        raise WrongState(f"{len(changed)} case(s) of the file changed since it was generated; nothing was approved. "
                         "Generate a new version.", {"cases": changed})

    now = timezone.now()
    total = sum((row.amount for row, _acct in matched), Decimal("0"))
    approval = CreditVoucherApproval.objects.create(
        voucher=voucher, uploaded_file_name=uploaded_name, uploaded_content=_decode(data),
        uploaded_sha256=hashlib.sha256(data).hexdigest(), approved_count=len(matched),
        removed_count=voucher.row_count - len(matched), total_amount=total, approved_by=user,
        approved_by_username=user.get_username(), approved_at=now,
    )
    CreditVoucherCredit.objects.bulk_create([
        CreditVoucherCredit(approval=approval, voucher_row=row, case_id=row.case_id, payment_module=module,
                            rrn=row.rrn, amount=row.amount, account=account, account_changed=account != row.account)
        for row, account in matched
    ])
    reason = (f"Approved and done through the CBS credit file {voucher.file_name} (credit file id {voucher.pk}), "
              f"uploaded by {user.get_username()} (owner decision 2026-11-11).")
    if remarks.strip():
        reason += f" {remarks.strip()}"
    items = [(cases[row.case_id], row.action_type, {
        "credit_voucher_id": [None, voucher.pk], "credit_voucher_file_name": [None, voucher.file_name],
        "credit_amount": [None, f"{row.amount:.2f}"],
        "account_masked": [None, generator.mask_account(account)]}) for row, account in matched]
    services.approve_by_credit_file(items, user, proposed_by=voucher.generated_by, proposed_at=voucher.generated_at,
                                    reference=voucher.file_name, remarks=reason, source=source)
    approved_ids = {row.pk for row, _acct in matched}
    rows = list(voucher.rows.order_by("line"))
    result = ApprovalResult(approval, [r for r in rows if r.pk in approved_ids],
                            [r for r in rows if r.pk not in approved_ids])
    log_operation(OperationType.CREDIT_VOUCHER_APPROVED, user=user, payment_module=module,
                  business_date=voucher.voucher_date, source=source, details={
                      "credit_voucher_id": voucher.pk, "file_name": voucher.file_name,
                      "uploaded_file_name": uploaded_name, "uploaded_sha256": approval.uploaded_sha256,
                      "approved": len(result.approved), "left_open": len(result.left_open),
                      "total_amount": f"{total:.2f}",
                      "accounts_changed": sum(1 for row, acct in matched if acct != row.account),
                  })
    return result
