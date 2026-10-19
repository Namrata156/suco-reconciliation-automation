"""
Business-date overview (Phase 5 design 4.11): one row per business date with
the file checklist, whether the presence matrix is built, the latest rule
evaluation run, case counts, the settlement status and the latest
"Run reconciliation". Read-only; a fixed number of queries per page of
dates, whatever the page size.
"""
from __future__ import annotations

from datetime import date

from django.db.models import Count, Max, Q

from core.models import PaymentModule
from file_processing.intake import date_checklists
from file_processing.models import UploadedFile
from reconciliation.models import ACTIVE_RUN_STATUSES, ReconciliationRun, TransactionMatchGroup
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


def date_overviews(module: PaymentModule, dates: list[date]) -> dict[date, dict]:
    from exception_management.models import ACTIVE_STATUSES, ExceptionCase
    from rule_engine.models import RuleEvaluationRun
    from settlement.models import SettlementResult

    checklists = date_checklists(module, dates)
    matrix = {r["business_date"]: r for r in TransactionMatchGroup.objects.filter(
        payment_module=module, business_date__in=dates, is_superseded=False).order_by()
        .values("business_date").annotate(groups=Count("id"), built_at=Max("updated_at"))}
    evaluations = _latest_per_date(RuleEvaluationRun.objects.filter(payment_module=module)
                                   .select_related("rule_set").order_by("-started_at", "-id"), dates)
    cases = {r["business_date"]: r for r in ExceptionCase.objects.filter(
        payment_module=module, business_date__in=dates).order_by().values("business_date")
        .annotate(total=Count("id"), active=Count("id", filter=Q(status__in=ACTIVE_STATUSES)))}
    settlements = _latest_per_date(SettlementResult.objects.filter(payment_module=module, is_current=True)
                                   .order_by("-computed_at", "-id"), dates)
    runs = _latest_per_date(ReconciliationRun.objects.filter(payment_module=module)
                            .order_by("-requested_at", "-id"), dates)

    out = {}
    for d in dates:
        cl = checklists[d]
        m, ev, c, st, run = matrix.get(d), evaluations.get(d), cases.get(d), settlements.get(d), runs.get(d)
        active = run if run is not None and run.status in ACTIVE_RUN_STATUSES else None
        out[d] = {
            "business_date": d,
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
        }
    return out
