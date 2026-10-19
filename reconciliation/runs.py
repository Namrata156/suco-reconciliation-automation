"""
"Run reconciliation" for one business date (Phase 5 design 4.11, owner P6 a,
Amendment 1 step 3): the run chain and its status record
(reconciliation.ReconciliationRun).

The chain calls the EXISTING services in the order the design gives, with
no logic of its own:
  1 presence matrix               reconciliation.presence.build_presence_matrix
  2 rule evaluation + case sync   rule_engine.evaluator.evaluate_business_date
                                  + exception_management.sync.sync_cases_for_date
                                  (one transaction, as rule_engine.tasks.evaluate_rules)
  3 settlement reconciliation     settlement.reconcile.reconcile_settlement_date
A step that raises stops the run (FAILED, with the error); what earlier
steps wrote stays, as when the tasks are run one by one. A re-run (e.g.
after a corrected file) is the same call: each service supersedes its own
previous output.

Operations User only (P10b). Who started it: ReconciliationRun.requested_by
and the core.OperationLog RUN_STARTED row, which also records the file
checklist at that moment. The services' own audit fields name the run
(RuleEvaluationRun.triggered_by, SettlementResult.source, CaseEvent.source).
"""
from __future__ import annotations

import logging
from datetime import date

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import OperationType, PaymentModule
from core.operations import InvalidInput, WrongState, log_operation
from file_processing.intake import date_checklists, require_operations_user
from reconciliation.models import ACTIVE_RUN_STATUSES, RUN_STEPS, ReconciliationRun, RunStatus, RunStep

logger = logging.getLogger(__name__)


def blockers_from(summary: dict, active_run: ReconciliationRun | None) -> list[dict]:
    """Why a run would be refused ([] = it may start), from a date's
    checklist summary (file_processing.intake.date_checklists) and its
    active run. Missing files do NOT block (P6: completeness is shown, not
    assumed); files still being processed do, so the matrix is never built
    from half a file."""
    blockers = []
    if active_run is not None:
        blockers.append({"code": "RUN_IN_PROGRESS",
                         "message": f"Run #{active_run.pk} for this date is {active_run.get_status_display().lower()}."})
    if summary["files_processing"]:
        blockers.append({"code": "FILES_PROCESSING",
                         "message": f"{summary['files_processing']} file(s) of this date are still being processed."})
    if not summary["files_loaded"]:
        blockers.append({"code": "NOTHING_LOADED", "message": "No file of this date is loaded yet."})
    return blockers


def active_run(module: PaymentModule, business_date: date) -> ReconciliationRun | None:
    return ReconciliationRun.objects.filter(payment_module=module, business_date=business_date,
                                            status__in=ACTIVE_RUN_STATUSES).first()


def run_blockers(module: PaymentModule, business_date: date) -> list[dict]:
    summary = date_checklists(module, [business_date])[business_date]["summary"]
    return blockers_from(summary, active_run(module, business_date))


def _queue(run_id: int) -> None:
    from reconciliation.tasks import run_business_date

    try:
        run_business_date.delay(run_id)
    except Exception as exc:  # noqa: BLE001 - broker down: the run must not stay QUEUED for ever
        logger.exception("Could not queue reconciliation run %s", run_id)
        ReconciliationRun.objects.filter(pk=run_id, status=RunStatus.QUEUED).update(
            status=RunStatus.FAILED, error=f"The run could not be queued: {exc}", finished_at=timezone.now())


def start_run(user, module: PaymentModule, business_date: date, *, source: str = "api") -> ReconciliationRun:
    require_operations_user(user)
    checklist = date_checklists(module, [business_date])[business_date]["summary"]
    blockers = blockers_from(checklist, active_run(module, business_date))
    if blockers:
        detail = " ".join(b["message"] for b in blockers)
        if any(b["code"] == "NOTHING_LOADED" for b in blockers) and len(blockers) == 1:
            raise InvalidInput(detail, {"business_date": [detail]})
        raise WrongState(detail)
    try:
        with transaction.atomic():
            run = ReconciliationRun.objects.create(
                payment_module=module, business_date=business_date, status=RunStatus.QUEUED,
                requested_by=user, requested_by_username=user.get_username(), source=source,
                steps={s: {"status": "PENDING"} for s in RUN_STEPS},
            )
            log_operation(OperationType.RUN_STARTED, user=user, payment_module=module, business_date=business_date,
                          run_id=run.pk, source=source, details={"checklist": checklist})
            transaction.on_commit(lambda pk=run.pk: _queue(pk))
    except IntegrityError:  # another run for the date was started at the same moment
        raise WrongState("A run for this date is already queued or running.") from None
    return run


# ---------------------------------------------------------------------------
# the chain (runs in the Celery worker: reconciliation.tasks.run_business_date)
# ---------------------------------------------------------------------------
def _presence(run: ReconciliationRun) -> dict:
    from reconciliation.presence import build_presence_matrix

    r = build_presence_matrix(run.payment_module, run.business_date)
    return {k: v for k, v in r.as_dict().items()
            if k in ("matched", "pending_rule_evaluation", "unmatched", "on_us", "cbs_reversed", "superseded",
                     "total_match_groups", "per_leg_row_counts", "one_sided_counts")}


def _rules(run: ReconciliationRun, label: str) -> dict:
    from exception_management.sync import sync_cases_for_date
    from rule_engine.evaluator import evaluate_business_date

    with transaction.atomic():  # results and their cases change together (as rule_engine.tasks.evaluate_rules)
        summary = evaluate_business_date(run.payment_module, run.business_date, triggered_by=label)
        cases = sync_cases_for_date(run.payment_module, run.business_date, source=label)
    s = summary.as_dict()
    return {"rule_evaluation_run_id": s["run_id"], "rule_set": s["rule_set_version"], "group_count": s["group_count"],
            "rule_counts": s["rule_counts"], "superseded_count": s["superseded_count"], "cases": cases.as_dict()}


def _settlement(run: ReconciliationRun, label: str) -> dict:
    from settlement.reconcile import reconcile_settlement_date

    outcome = reconcile_settlement_date(run.payment_module, run.business_date, source=label)
    r = outcome.result
    return {"settlement_result_id": r.pk, "status": r.status, "reasons": r.reasons, "case": outcome.case_action,
            "case_id": outcome.case_id}


def _save(run: ReconciliationRun, *fields: str) -> None:
    run.save(update_fields=list(fields))


def execute_run(run_id: int) -> ReconciliationRun:
    with transaction.atomic():
        run = ReconciliationRun.objects.select_for_update().select_related("payment_module").get(pk=run_id)
        if run.status != RunStatus.QUEUED:
            logger.warning("Reconciliation run %s is %s, not QUEUED; not run again", run_id, run.status)
            return run
        run.status, run.started_at = RunStatus.RUNNING, timezone.now()
        _save(run, "status", "started_at")

    label = f"run #{run.pk} ({run.requested_by_username or 'unknown'})"[:150]
    steps = {RunStep.PRESENCE: lambda: _presence(run), RunStep.RULES: lambda: _rules(run, label),
             RunStep.SETTLEMENT: lambda: _settlement(run, label)}
    for step in RUN_STEPS:
        run.current_step = step
        run.steps[step] = {"status": "RUNNING", "started_at": timezone.now().isoformat()}
        _save(run, "current_step", "steps")
        try:
            summary = steps[step]()
        except Exception as exc:  # noqa: BLE001 - recorded on the run, then re-raised for the worker log
            logger.exception("Reconciliation run %s failed at %s", run.pk, step)
            run.steps[step].update(status="FAILED", finished_at=timezone.now().isoformat())
            run.status, run.error, run.finished_at = RunStatus.FAILED, f"{step}: {exc}"[:2000], timezone.now()
            _save(run, "steps", "status", "error", "finished_at")
            return run
        run.steps[step].update(status="DONE", finished_at=timezone.now().isoformat(), summary=summary)
        _save(run, "steps")
    run.status, run.current_step, run.finished_at = RunStatus.SUCCEEDED, "", timezone.now()
    _save(run, "status", "current_step", "finished_at")
    return run


def run_payload(run: ReconciliationRun | None) -> dict | None:
    if run is None:
        return None
    return {"id": run.pk, "business_date": run.business_date, "status": run.status, "current_step": run.current_step,
            "steps": run.steps, "error": run.error, "requested_by": run.requested_by_username,
            "source": run.source, "requested_at": run.requested_at, "started_at": run.started_at,
            "finished_at": run.finished_at}
