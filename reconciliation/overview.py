"""
Business-date overview (Phase 5 design 4.11): one row per business date with
the file checklist, whether the presence matrix is built, the latest rule
evaluation run, case counts, the settlement status and the latest
"Run reconciliation". Read-only; a fixed number of queries per page of
dates, whatever the page size.
"""
from __future__ import annotations

from datetime import date

from django.db import models
from django.db.models import Count, Max, Q
from django.db.models.functions import Coalesce

from core.models import PaymentModule
from file_processing.intake import date_checklists
from file_processing.models import UploadedFile
from reconciliation.models import ACTIVE_RUN_STATUSES, ReconciliationRun, RunStatus, TransactionMatchGroup
from reconciliation.runs import blockers_from, run_payload


def business_dates(module: PaymentModule) -> list[date]:
    """Every date with a current file (or a CBS file waiting with that batch
    date) or a run, newest first."""
    files = UploadedFile.objects.filter(payment_module=module, is_current=True).order_by()
    dates = set(files.exclude(business_date=None).values_list("business_date", flat=True).distinct())
    dates |= set(files.filter(business_date=None).exclude(batch_business_date=None)
                 .values_list("batch_business_date", flat=True).distinct())
    dates |= set(ReconciliationRun.objects.filter(payment_module=module).order_by()
                 .values_list("business_date", flat=True).distinct())
    return sorted(dates, reverse=True)


def _latest_per_date(qs, dates) -> dict:
    out = {}
    for obj in qs.filter(business_date__in=dates):
        out.setdefault(obj.business_date, obj)
    return out


class DateStatus(models.TextChoices):
    """One plain status per business date (UI refinements 3.4; contract
    docs/ui_refinements_api_contract.txt A0). First match wins, in this order.
    date_status() decides up to CASES_OPEN / DONE; FILE_PENDING (owner Q2,
    2026-10-27) is decided after it by npci_files.replies.finish_date_statuses,
    which needs the SSKUPI file state."""

    RUNNING = "RUNNING", "Running"
    WAITING_FOR_FILES = "WAITING_FOR_FILES", "Waiting for files"
    READY_TO_RUN = "READY_TO_RUN", "Ready to run"
    CASES_OPEN = "CASES_OPEN", "Cases open"
    FILE_PENDING = "FILE_PENDING", "SSKUPI file to generate / download"
    DONE = "DONE", "Done"


def date_status(summary: dict, evaluation, last_run, active_cases: int, latest_upload) -> tuple[str, str]:
    """(DateStatus, one plain sentence) from what the overview already read.
    Reconciled = a rule evaluation exists; files uploaded after its start
    make the date need a run again."""
    if last_run is not None and last_run.status in ACTIVE_RUN_STATUSES:
        return DateStatus.RUNNING, f"Run #{last_run.pk} is {last_run.get_status_display().lower()}."
    stale = evaluation is not None and latest_upload is not None and latest_upload > evaluation.started_at
    if evaluation is None or stale:
        not_loaded = summary["expected"] - summary["loaded"]
        if not_loaded > 0:
            return DateStatus.WAITING_FOR_FILES, f"{not_loaded} expected file(s) are not loaded yet."
        if stale:
            return DateStatus.READY_TO_RUN, "Files changed since the last run; run again."
        if last_run is not None and last_run.status == RunStatus.FAILED:
            return DateStatus.READY_TO_RUN, f"The last run failed: {last_run.error}".strip()
        return DateStatus.READY_TO_RUN, "All expected files are loaded."
    failed = (f" The last run failed: {last_run.error}".rstrip()
              if last_run is not None and last_run.status == RunStatus.FAILED else "")
    if active_cases:
        return DateStatus.CASES_OPEN, f"{active_cases} case(s) open.{failed}"
    return DateStatus.DONE, f"No case open.{failed}"


def _latest_uploads(module: PaymentModule, dates: list[date]) -> dict:
    return dict(UploadedFile.objects.filter(payment_module=module, is_current=True).order_by()
                .annotate(d=Coalesce("business_date", "batch_business_date")).filter(d__in=dates)
                .values("d").annotate(m=Max("uploaded_at")).values_list("d", "m"))


def date_overviews(module: PaymentModule, dates: list[date], *, group_breakdown: bool = False) -> dict[date, dict]:
    """group_breakdown: the match-group query also counts each processing
    status, on-us and CBS-reversed groups, in the same pass, returned as
    "_groups" (the Phase 6 reconciliation report)."""
    from exception_management.models import ACTIVE_STATUSES, ExceptionCase
    from reconciliation.models import ProcessingStatus
    from rule_engine.models import RuleEvaluationRun
    from settlement.models import SettlementResult

    checklists = date_checklists(module, dates)
    breakdown = {}
    if group_breakdown:
        breakdown = {"on_us": Count("id", filter=Q(is_on_us=True)),
                     "cbs_reversed": Count("id", filter=Q(cbs_reversed=True)),
                     **{f"s_{s}": Count("id", filter=Q(processing_status=s)) for s in ProcessingStatus.values}}
    matrix = {r["business_date"]: r for r in TransactionMatchGroup.objects.filter(
        payment_module=module, business_date__in=dates, is_superseded=False).order_by()
        .values("business_date").annotate(groups=Count("id"), built_at=Max("updated_at"), **breakdown)}
    evaluations = _latest_per_date(RuleEvaluationRun.objects.filter(payment_module=module)
                                   .select_related("rule_set").order_by("-started_at", "-id"), dates)
    cases = {r["business_date"]: r for r in ExceptionCase.objects.filter(
        payment_module=module, business_date__in=dates).order_by().values("business_date")
        .annotate(total=Count("id"), active=Count("id", filter=Q(status__in=ACTIVE_STATUSES)))}
    # Never the snapshot (the full calculation, ~150 KB a date): only the status fields are read here.
    settlements = _latest_per_date(SettlementResult.objects.filter(payment_module=module, is_current=True)
                                   .defer("snapshot").order_by("-computed_at", "-id"), dates)
    runs = _latest_per_date(ReconciliationRun.objects.filter(payment_module=module)
                            .order_by("-requested_at", "-id"), dates)
    uploads = _latest_uploads(module, dates)

    out = {}
    for d in dates:
        cl = checklists[d]
        m, ev, c, st, run = matrix.get(d), evaluations.get(d), cases.get(d), settlements.get(d), runs.get(d)
        active = run if run is not None and run.status in ACTIVE_RUN_STATUSES else None
        status, detail = date_status(cl["summary"], ev, run, c["active"] if c else 0, uploads.get(d))
        out[d] = {
            "business_date": d,
            "status": str(status), "status_label": DateStatus(status).label, "status_detail": detail,
            "files": cl["summary"],
            "missing": [{"file_type_config_id": s["file_type_config_id"], "label": s["label"], "cycle": s["cycle"]}
                        for s in cl["slots"] if s["state"] == "MISSING"],
            "matrix": {"built": m is not None, "match_groups": m["groups"] if m else 0,
                       "built_at": m["built_at"] if m else None},
            "rule_evaluation": None if ev is None else {
                "id": ev.pk, "rule_set": ev.rule_set.version_label, "started_at": ev.started_at,
                "finished_at": ev.finished_at, "group_count": ev.group_count, "triggered_by": ev.triggered_by},
            "cases": {"total": c["total"] if c else 0, "active": c["active"] if c else 0},
            "settlement": None if st is None else {
                "id": st.pk, "status": st.status, "reasons": st.reasons, "computed_at": st.computed_at,
                "unexplained_amount": f"{st.unexplained_amount:.2f}"},
            "last_run": run_payload(run),
            "run_blockers": blockers_from(cl["summary"], active),
            "_checklist": cl,
            "_evaluation": ev,
        }
        if group_breakdown:
            out[d]["_groups"] = {
                "total": m["groups"] if m else 0,
                "by_status": {s: m[f"s_{s}"] if m else 0 for s in ProcessingStatus.values},
                "on_us": m["on_us"] if m else 0, "cbs_reversed": m["cbs_reversed"] if m else 0}
    return out
