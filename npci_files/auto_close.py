"""
Owner decision 2026-11-09: cases where no money moves at the bank close by
themselves once their reply is in a generated NPCI upload (SSKUPI) file. No
Maker or Checker step: the system already proved them from CBS GL 1535.

  R01   DRC, debited and already reversed   -> DRC 102
  R03   DRC, never reached CBS              -> DRC 104
  R04a  Timeout, credited online            -> TCC 102

Every other rule (R02 DRC 103, R05 RET, R06 RRC 501, R06b, R04b, R07x, ...)
keeps the full Maker-Checker workflow (docs/npci_file_design_draft_v1.txt
Amendment 2 B3 still applies to them).

A case included in a file is closed, in the SAME transaction as the file
(generator.generate_file), only when all of these hold:
  - it is a transaction case whose rule is in AUTO_CLOSE_REPLIES;
  - the row written for it is that rule's no-money reply (flag + numeric
    code), and the case still carries that reply (a Maker who submitted, or a
    Checker who approved, a DIFFERENT reply leaves the case for people);
  - it is not flagged for review, and nothing else holds it back
    (generator.hold_reason: e.g. the Checker rejected that very reply);
  - it is OPEN, IN_PROGRESS, PENDING_APPROVAL or APPROVED
    (SYSTEM_CLOSABLE_STATUSES).
It becomes COMPLETED with a CLOSED_BY_SYSTEM case event (actor NULL = the
system) naming the file; a pending proposal is withdrawn, an approved one is
recorded as done with the file name as its reference
(exception_management.services.close_by_system).

Re-runs are unchanged: a COMPLETED case is never reopened or silently
changed by a re-run (exception_management.sync): a changed rule flags it for
review, and the next file's preview lists it under "changed since included".

Files generated before this decision: `manage.py close_included_no_money_cases`
(not run automatically).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from django.db import transaction

from exception_management import services
from exception_management.models import SYSTEM_CLOSABLE_STATUSES, CaseStatus, CaseType, ExceptionCase
from npci_files.models import NpciFile, NpciFileRow

# The one place the list lives: rule code -> its no-money reply (flag, numeric code).
AUTO_CLOSE_REPLIES: dict[str, tuple[str, str]] = {
    "R01": ("DRC", "102"),  # DRC, already reversed
    "R03": ("DRC", "104"),  # DRC, never reached CBS
    "R04a": ("TCC", "102"),  # Timeout, credited online
}
AUTO_CLOSE_RULES = tuple(AUTO_CLOSE_REPLIES)

GENERATION_SOURCE = "system: NPCI file generation"


class Skip:
    """Why an included case is not closed (dry-run counts)."""

    NOT_A_NO_MONEY_RULE = "rule not in the no-money list"
    ROW_NOT_NO_MONEY_REPLY = "the file row is not the rule's no-money reply"
    REPLY_CHANGED = "the case now carries another reply"
    NEEDS_REVIEW = "flagged for review"
    HELD = "held back (e.g. the Checker rejected the reply)"
    ALREADY_COMPLETED = "already completed"
    SUPERSEDED = "closed by a re-run"
    NOT_TRANSACTION = "not a transaction case"


def reply_closes_case(case: ExceptionCase, flag: str | None, code: str | None) -> bool:
    """True when (flag, code) is the no-money reply of the case's rule."""
    expected = AUTO_CLOSE_REPLIES.get(case.rule_code)
    return expected is not None and (flag, code) == expected


def skip_reason(case: ExceptionCase, row_flag: str, row_code: str, reply, latest: dict) -> str | None:
    """None when the case, included with reply (row_flag, row_code), closes now;
    else why not. `reply` = generator.current_reply, `latest` from load_actions."""
    from npci_files import generator

    if case.case_type != CaseType.TRANSACTION:
        return Skip.NOT_TRANSACTION
    if case.status == CaseStatus.COMPLETED:
        return Skip.ALREADY_COMPLETED
    if case.status == CaseStatus.CLOSED_SUPERSEDED:
        return Skip.SUPERSEDED
    if case.rule_code not in AUTO_CLOSE_REPLIES:
        return Skip.NOT_A_NO_MONEY_RULE
    if not reply_closes_case(case, row_flag, row_code):
        return Skip.ROW_NOT_NO_MONEY_REPLY
    if not reply_closes_case(case, reply.flag, reply.code):
        return Skip.REPLY_CHANGED
    if case.needs_review:
        return Skip.NEEDS_REVIEW
    if generator.hold_reason(case, reply, latest) or case.status not in SYSTEM_CLOSABLE_STATUSES:
        return Skip.HELD
    return None


@dataclass
class FileClosure:
    npci_file: NpciFile
    closed: list[ExceptionCase] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)  # {Skip reason: n}
    status_before: Counter = field(default_factory=Counter)  # {status the closed cases had: n}

    def closed_by(self, attr: str) -> dict[str, int]:
        return dict(Counter(getattr(c, attr) for c in self.closed))


def _remarks(npci_file: NpciFile) -> str:
    return (f"Closed by the system: the no-money reply was included in NPCI file {npci_file.file_name} "
            f"(file id {npci_file.pk}). No money moves at the bank, so no Maker or Checker step "
            "(owner decision 2026-11-09).")


def close_included_cases(npci_file: NpciFile, *, source: str = GENERATION_SOURCE,
                         dry_run: bool = False) -> FileClosure:
    """Closes the eligible cases of one file's rows. Locks them first (pk
    order) and re-checks each against its current reply, so a Maker's change
    made while the file was being built is respected. Call inside the file's
    transaction (generate_file) or let it open its own (the backfill
    command). dry_run: the same checks, nothing written."""
    from npci_files import generator

    result = FileClosure(npci_file)
    with transaction.atomic():
        rows = {r.case_id: r for r in NpciFileRow.objects.filter(npci_file=npci_file).only(
            "id", "case_id", "flag", "code")}
        if not rows:
            return result
        cases = list(ExceptionCase.objects.select_for_update(of=("self",))
                     .select_related("payment_module", "assigned_to", "default_response_code")
                     .filter(pk__in=list(rows)).order_by("pk"))
        actions, latest = generator.load_actions([c.pk for c in cases])
        eligible = []
        for case in cases:
            row = rows[case.pk]
            why = skip_reason(case, row.flag, row.code, generator.current_reply(case, actions), latest)
            if why is None:
                eligible.append(case)
                result.status_before[case.status] += 1
            else:
                result.skipped[why] += 1
        if dry_run:
            result.closed = eligible
            return result
        result.closed = services.close_by_system(
            eligible, reference=npci_file.file_name, remarks=_remarks(npci_file), source=source,
            changes={"npci_file_id": [None, npci_file.pk], "npci_file_name": [None, npci_file.file_name]})
    return result
