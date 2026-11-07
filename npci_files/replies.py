"""
Read-only views of the NPCI reply each case carries and of the SSKUPI file
state, for the screens (UI refinements U2, docs/ui_refinements_api_contract.txt
"The reply object", A1-A4). Nothing here decides anything new: the reply and
the reasons a case is left out are npci_files.generator's own (same
precedence, same hold rules), so the screens can never disagree with the file.

  ReplyType              the queue tabs: DRC / TCC / RET / RRC / NEEDS_REVIEW / SETTLEMENT
  annotate_reply_type()  the tab of every case as a SQL expression (filter + counts)
  replies_for()          {case id: generator.Reply} for a page of cases (one query)
  reply_payload()        the "reply" object of the contract
  case_file_status()     where one case stands with the SSKUPI file
  date_file_summaries()  compact SSKUPI block per business date (2 queries per page)
  date_file_detail()     full SSKUPI block for one date (the generator's plan)
  finish_date_statuses() DONE -> FILE_PENDING until the date's SSKUPI file is generated
                         and downloaded and no row waits for a file (owner Q2, 2026-10-27)
"""
from __future__ import annotations

from django.db import models
from django.db.models import Case, CharField, Exists, F, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce

from exception_management.actions import ACTION_SPECS, ActionType
from exception_management.models import CaseStatus, CaseType, Decision, ExceptionCase, ProposedAction
from exception_management.roles import user_roles
from npci_files import generator
from npci_files.formats import LEG_BY_FLAG, FormatError, format_for
from npci_files.models import NpciFile, NpciFileRow, ReplySource
from reconciliation.models import RegisterPresence

REPLY_FLAGS = tuple(LEG_BY_FLAG)  # DRC, TCC, RET, RRC


class ReplyType(models.TextChoices):
    DRC = "DRC", "DRC"
    TCC = "TCC", "TCC"
    RET = "RET", "RET"
    RRC = "RRC", "RRC"
    NEEDS_REVIEW = "NEEDS_REVIEW", "Needs review"
    SETTLEMENT = "SETTLEMENT", "Settlement"


ACTION_TYPES_BY_FLAG: dict[str, list[str]] = {
    flag: [str(a) for a, spec in ACTION_SPECS.items() if spec.code_flag == flag] for flag in REPLY_FLAGS
}


def _latest_action_type(decision: str):
    return Subquery(ProposedAction.objects.filter(case=OuterRef("pk"), decision=decision)
                    .order_by("-submitted_at", "-pk").values("action_type")[:1])


def annotate_reply_type(qs):
    """Adds reply_action_type (the action the case's reply comes from, same
    precedence as generator._reply) and reply_type (ReplyType) to a case
    queryset, as SQL, so the queue can filter and count on them."""
    default = F("default_action_type")
    effective = Case(
        When(status__in=generator.DECIDED, then=Coalesce(_latest_action_type(Decision.APPROVED), default)),
        When(status=CaseStatus.PENDING_APPROVAL, then=Coalesce(_latest_action_type(Decision.PENDING), default)),
        default=default, output_field=CharField(),
    )
    return qs.annotate(reply_action_type=effective).annotate(reply_type=Case(
        When(case_type=CaseType.SETTLEMENT, then=Value(ReplyType.SETTLEMENT.value)),
        *[When(reply_action_type__in=types, then=Value(flag)) for flag, types in ACTION_TYPES_BY_FLAG.items()],
        default=Value(ReplyType.NEEDS_REVIEW.value), output_field=CharField(),
    ))


def reply_type_of(case: ExceptionCase, reply: generator.Reply) -> str:
    if case.case_type == CaseType.SETTLEMENT:
        return ReplyType.SETTLEMENT
    return reply.flag if reply.flag in REPLY_FLAGS else ReplyType.NEEDS_REVIEW


def replies_for(cases) -> dict[int, generator.Reply]:
    """{case id: the reply it carries now}; one query for the actions. The
    cases should have default_response_code loaded (select_related)."""
    cases = list(cases)
    actions, _latest = generator.load_actions([c.pk for c in cases])
    return {c.pk: generator.current_reply(c, actions) for c in cases}


def reply_label(reply: generator.Reply) -> str | None:
    rc = reply.response_code
    if reply.flag in REPLY_FLAGS and rc is not None:
        code = rc.code if rc.code is not None else "(code not known yet)"
        return f"{reply.flag} {code} - {rc.description}"
    if reply.action_type in ACTION_SPECS:
        return ActionType(reply.action_type).label
    return None


def reply_payload(case: ExceptionCase, reply: generator.Reply) -> dict:
    rtype = reply_type_of(case, reply)
    flag = reply.flag if reply.flag in REPLY_FLAGS else None
    rc = reply.response_code if flag else None
    action_label = ActionType(reply.action_type).label if reply.action_type in ACTION_SPECS else ""
    return {
        "reply_type": str(rtype), "reply_type_label": ReplyType(rtype).label,
        "action_type": reply.action_type or "", "action_type_label": action_label,
        "flag": flag, "code": rc.code if rc is not None else None,
        "text": rc.description if rc is not None else None,
        "label": reply_label(reply),
        "source": str(reply.source), "source_label": ReplySource(reply.source).label,
    }


# ---------------------------------------------------------------------------
# one case and the SSKUPI file (contract A4 what_happened.sskupi)
# ---------------------------------------------------------------------------
class FileStatus(models.TextChoices):
    IN_FILE = "IN_FILE", "In an SSKUPI file"
    NOT_YET_IN_FILE = "NOT_YET_IN_FILE", "Not yet in a file; goes into the next file"
    HELD_FOR_CHECKER = "HELD_FOR_CHECKER", "Held until a Checker approves"
    LEFT_OUT = "LEFT_OUT", "Left out of the file for now"
    NO_FILE_REPLY = "NO_FILE_REPLY", "No SSKUPI reply for this case"


CHECKER_REASONS = (generator.Reason.NEEDS_APPROVAL, generator.Reason.NEEDS_REVIEW)


def _status(status, *, reason=None, detail="", file_date=None, file=None, changed=False) -> dict:
    return {"status": str(status), "status_label": FileStatus(status).label,
            "reason": str(reason) if reason else None,
            "reason_label": generator.Reason(reason).label if reason else None,
            "detail": detail or "", "file_date": file_date, "file": file, "changed_since_included": changed}


def case_file_status(case: ExceptionCase, reply: generator.Reply | None = None) -> dict:
    """Where the case stands with the SSKUPI file now, by the generator's own
    rules (plan_file): in a file, waiting for the next file, held for a
    Checker, left out for another reason, or no file reply at all."""
    row = case.npci_file_rows.select_related("npci_file").first()
    actions, latest = generator.load_actions([case.pk])
    if reply is None:
        reply = generator.current_reply(case, actions)
    flag, rc = reply.flag, reply.response_code
    if row is not None:
        f = row.npci_file
        changed = (case.needs_review or case.status == CaseStatus.CLOSED_SUPERSEDED
                   or (flag, reply.code) != (row.flag, row.code))
        return _status(FileStatus.IN_FILE, file_date=f.file_date, changed=changed, file={
            "id": f.pk, "file_name": f.file_name, "file_date": f.file_date, "line": row.line,
            "generated_at": f.generated_at, "generated_by": f.generated_by_username, "flag": row.flag,
            "code": row.code, "reply_source": row.reply_source})
    if case.case_type != CaseType.TRANSACTION:
        return _status(FileStatus.NO_FILE_REPLY, detail="A settlement case has no SSKUPI row.")
    if case.status == CaseStatus.CLOSED_SUPERSEDED:
        return _status(FileStatus.NO_FILE_REPLY, detail="Closed by a re-run: no longer needs a reply.")
    if flag not in LEG_BY_FLAG:
        return _status(FileStatus.NO_FILE_REPLY,
                       detail="The reply is not a DRC / TCC / RET / RRC reply, so it is not sent in the SSKUPI file.")
    presence = (RegisterPresence.objects.filter(match_group_id=case.match_group_id, leg=LEG_BY_FLAG[flag],
                                                present=True, transaction_record__isnull=False)
                .select_related("transaction_record").first() if case.match_group_id else None)
    fact = presence.transaction_record if presence else None
    file_date = fact.business_date if (flag == "RRC" and fact is not None) else case.business_date
    held = generator.hold_reason(case, reply, latest)
    if held:
        status = FileStatus.HELD_FOR_CHECKER if held[0] in CHECKER_REASONS else FileStatus.LEFT_OUT
        return _status(status, reason=held[0], detail=held[1], file_date=file_date)
    if rc is None or reply.code is None:
        return _status(FileStatus.LEFT_OUT, reason=generator.Reason.NO_NUMERIC_CODE, file_date=file_date,
                       detail=f"{flag} {rc.description}" if rc is not None else flag)
    if fact is None:
        return _status(FileStatus.LEFT_OUT, reason=generator.Reason.NO_NPCI_ROW, file_date=file_date,
                       detail=f"No NPCI {LEG_BY_FLAG[flag]} row is attached to this case")
    fmt = format_for(case.payment_module)
    if fmt is None:
        return _status(FileStatus.NO_FILE_REPLY, detail=f"No NPCI file format is defined for {case.payment_module.code}.")
    try:
        generator.build_line(fmt, case, flag, reply.code, rc, fact, generator._raw_cells([fact]).get(fact.pk, {}))
    except FormatError as exc:
        return _status(FileStatus.LEFT_OUT, reason=generator.Reason.BAD_SOURCE_DATA, detail=str(exc),
                       file_date=file_date)
    return _status(FileStatus.NOT_YET_IN_FILE, file_date=file_date,
                   detail=f"Goes into the next SSKUPI file of {file_date:%d %b %Y}.")


# ---------------------------------------------------------------------------
# the SSKUPI file per business date (contract A1a, A2)
# ---------------------------------------------------------------------------
class FileState(models.TextChoices):
    NONE = "NONE", "Not generated"
    GENERATED = "GENERATED", "Generated, not downloaded"
    DOWNLOADED = "DOWNLOADED", "Downloaded"


def _file_entry(f: NpciFile, downloads: list[dict]) -> dict:
    return {"id": f.pk, "file_name": f.file_name, "sequence": f.sequence, "row_count": f.row_count,
            "generated_at": f.generated_at, "generated_by": f.generated_by_username,
            "download_count": len(downloads), "last_download": downloads[0] if downloads else None}


def _summaries(module, dates, *, with_files: bool) -> dict:
    from api.npci_files import downloads_by_file  # one query for every file of the page

    by_date: dict = {d: [] for d in dates}
    for f in NpciFile.objects.filter(payment_module=module, file_date__in=list(dates)).order_by("file_date",
                                                                                                "sequence"):
        by_date.setdefault(f.file_date, []).append(f)
    downloads = downloads_by_file(module, [f.pk for files in by_date.values() for f in files])
    out = {}
    for d, files in by_date.items():
        entries = [_file_entry(f, downloads.get(f.pk, [])) for f in files]
        latest = entries[-1] if entries else None
        state = (FileState.NONE if latest is None
                 else FileState.DOWNLOADED if latest["download_count"] else FileState.GENERATED)
        body = {"state": str(state), "state_label": FileState(state).label, "file_count": len(entries),
                "latest": latest,
                "not_downloaded": [e["file_name"] for e in entries if not e["download_count"]]}
        if with_files:
            body["files"] = entries
        out[d] = body
    return out


def date_file_summaries(module, dates) -> dict:
    """{date: compact block, or None when the module has no NPCI file format}."""
    if format_for(module) is None:
        return {d: None for d in dates}
    return _summaries(module, dates, with_files=False)


def date_file_detail(module, d, user) -> dict | None:
    """The full block for one date: the files plus what the next file would
    hold now (generator.plan_file; nothing saved)."""
    if format_for(module) is None:
        return None
    body = _summaries(module, [d], with_files=True)[d]
    plan = generator.plan_file(module, d)
    held = sum(1 for e in plan.excluded if e.reason in CHECKER_REASONS)
    body.update({
        "waiting_rows": len(plan.rows),
        "next_file_name": plan.file_format.file_name(d, plan.next_sequence()),
        "held_for_checker": held,
        "left_out": len(plan.excluded) - held,
        "changed_since_included": len({case.pk for case, _row in plan.changed_since_included}),
        "can_generate": bool(plan.rows) and bool(user_roles(user).intersection(generator.GENERATE_ROLES)),
    })
    return body


# ---------------------------------------------------------------------------
# FILE_PENDING (owner Q2, 2026-10-27; contract A0)
# ---------------------------------------------------------------------------
def dates_with_unsent_replies(module, dates) -> set:
    """Of `dates`, those that MAY have reply rows waiting for a file: a
    transaction case not closed by a re-run, with a DRC / TCC / RET / RRC
    reply, in no file yet, whose file date (RRC: the Adjustment row's date)
    is one of the dates. One query. A superset of the generator's rows (it
    also holds cases the generator leaves out); plan_file decides."""
    from exception_management.models import CaseType

    dates = list(dates)
    if not dates:
        return set()
    adj_leg = LEG_BY_FLAG["RRC"]
    adjustment_groups = RegisterPresence.objects.filter(
        leg=adj_leg, present=True, match_group__payment_module=module,
        transaction_record__business_date__in=dates).values("match_group_id")
    adj_date = Subquery(RegisterPresence.objects.filter(
        match_group_id=OuterRef("match_group_id"), leg=adj_leg, present=True, transaction_record__isnull=False)
        .values("transaction_record__business_date")[:1])
    qs = (annotate_reply_type(
        ExceptionCase.objects.filter(payment_module=module, case_type=CaseType.TRANSACTION)
        .exclude(status=CaseStatus.CLOSED_SUPERSEDED)
        .filter(Q(business_date__in=dates) | Q(match_group_id__in=adjustment_groups))
        .filter(~Exists(NpciFileRow.objects.filter(case=OuterRef("pk")))))
        .filter(reply_type__in=REPLY_FLAGS).annotate(adj_date=adj_date).order_by()
        .values_list("reply_type", "business_date", "adj_date"))
    wanted, out = set(dates), set()
    for reply_type, business_date, adjustment_date in qs:
        file_date = adjustment_date if (reply_type == "RRC" and adjustment_date) else business_date
        if file_date in wanted:
            out.add(file_date)
    return out


def finish_date_statuses(module, rows: dict, summaries: dict, *, exact: bool = True) -> None:
    """Turns DONE into FILE_PENDING where the SSKUPI step is not finished
    (owner Q2): rows still wait for a file, or a file of the date is not
    downloaded. A date with nothing to send at all (no reply rows, no file)
    stays DONE. `rows`: {date: date row with status / status_label /
    status_detail / last_run}, changed in place; `summaries`: the compact
    blocks of date_file_summaries. The generator's plan is built only for
    DONE dates that may still have rows to send. exact=False (the dashboard,
    fixed query count): no plan is built; a date with a reply case in no file
    counts as pending even if the generator would leave that row out."""
    from reconciliation.overview import DateStatus

    if format_for(module) is None:
        return
    done = [d for d, r in rows.items() if r["status"] == DateStatus.DONE]
    maybe = dates_with_unsent_replies(module, done)
    for d in done:
        row, files = rows[d], summaries.get(d) or {}
        waiting = left_out = 0
        next_name = None
        if d in maybe and not exact:
            waiting, next_name = None, "the next file"
        elif d in maybe:
            plan = generator.plan_file(module, d)
            waiting, left_out = len(plan.rows), len(plan.excluded)
            next_name = plan.file_format.file_name(d, plan.next_sequence())
        not_downloaded = files.get("not_downloaded") or []
        if waiting is None:
            status = DateStatus.FILE_PENDING
            detail = "All cases worked. Reply row(s) may wait for the SSKUPI file; open the date to check."
        elif waiting:
            status = DateStatus.FILE_PENDING
            detail = (f"All cases worked. {waiting} reply row(s) wait for the SSKUPI file: "
                      f"generate {next_name}.")
        elif not_downloaded:
            status = DateStatus.FILE_PENDING
            detail = f"All cases worked. Download {', '.join(not_downloaded)}."
        else:
            status = DateStatus.DONE
            detail = ("No case open; SSKUPI file(s) generated and downloaded." if files.get("file_count")
                      else "No case open; nothing to send in an SSKUPI file.")
        if left_out:
            detail += f" {left_out} reply row(s) cannot go into a file yet (see the SSKUPI preview)."
        run = row.get("last_run")
        if run and run.get("status") == "FAILED":
            detail += f" The last run failed: {run.get('error') or ''}".rstrip()
        row.update({"status": str(status), "status_label": DateStatus(status).label, "status_detail": detail})
