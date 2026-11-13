"""
Phase 6 Slice 6.4: user activity report (docs/phase6_api_contract.txt B7;
design 2.7). Not under a module: logins are global. Administrator and
Auditor only (as audit.view: the log holds IP addresses and failed logins).

  GET /api/v1/reports/user-activity/

One row per user (including users with no activity in the range), paged by
username. Every count is one grouped query over the range (event
timestamp, IST date) on an indexed created_at, so the cost does not grow
with the number of users. The module filter narrows the WORK counts only
(cases, files, runs, settings, downloads), never the logins.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from urllib.parse import quote, urlencode

from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.db.models.functions import Lower
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated

from api.audit import _SETTINGS_SOURCES, HasAuditRole
from api.base import V1APIView, module_slug
from api.export import COUNT, DATETIME, BOOL, Column, Sheet, export_block, export_response, range_file_base, \
    wants_export
from api.operations import _bad_request
from api.reports import report_block, report_range, versions
from api.results import _bool_param
from core.models import OperationLog, OperationType, PaymentModule, UserActivity, UserActivityType
from exception_management import api as legacy
from exception_management.models import CaseEvent, EventType
from exception_management.roles import Role, listed_roles, users_holding_q
from settlement.models import MappingEventType

CASE_COUNTS = {"taken": EventType.TAKEN, "submitted": EventType.SUBMITTED, "approved": EventType.APPROVED,
               "rejected": EventType.REJECTED, "marked_done": EventType.COMPLETED, "remarks": EventType.REMARK}
OPERATION_COUNTS = {"uploaded": OperationType.FILE_UPLOADED, "refused": OperationType.FILE_REFUSED,
                    "cbs_dates_selected": OperationType.BUSINESS_DATE_SELECTED,
                    "runs_started": OperationType.RUN_STARTED, "report_downloads": OperationType.REPORT_EXPORTED}
DECIDED_EVENTS = ("APPROVED", "REJECTED")  # CodeChangeEventType / MappingEventType share the values


def _bounds(d_from, d_to):
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.combine(d_from, time.min), tz),
            timezone.make_aware(datetime.combine(d_to + timedelta(days=1), time.min), tz))


def activity_counts(start, end, module) -> dict[str, dict]:
    """{name: {user id: count}} plus "_failed_by_name" {lower username: n}
    and "_last_login" {user id: (created_at, ip)}."""
    out: dict[str, dict] = defaultdict(dict)
    logins = UserActivity.objects.filter(created_at__gte=start, created_at__lt=end)
    for r in (logins.exclude(user=None).order_by().values("user_id")
              .annotate(logins=Count("id", filter=Q(activity_type=UserActivityType.LOGIN)),
                        failed=Count("id", filter=Q(activity_type=UserActivityType.LOGIN_FAILED)))):
        out["logins"][r["user_id"]] = r["logins"]
        out["failed_logins"][r["user_id"]] = r["failed"]
    for r in (logins.filter(user=None, activity_type=UserActivityType.LOGIN_FAILED).order_by()
              .values(name=Lower("username")).annotate(n=Count("id"))):
        out["_failed_by_name"][r["name"]] = r["n"]
    for r in (logins.filter(activity_type=UserActivityType.LOGIN).exclude(user=None)
              .order_by("user_id", "-created_at", "-id").distinct("user_id")
              .values("user_id", "created_at", "ip_address")):
        out["_last_login"][r["user_id"]] = (r["created_at"], r["ip_address"])

    events = CaseEvent.objects.filter(created_at__gte=start, created_at__lt=end).exclude(actor=None)
    if module is not None:
        events = events.filter(payment_module_code=module.code)
    for r in events.order_by().values("actor_id").annotate(
            **{name: Count("id", filter=Q(event_type=t)) for name, t in CASE_COUNTS.items()}):
        for name in CASE_COUNTS:
            out[f"case_{name}"][r["actor_id"]] = r[name]

    ops = OperationLog.objects.filter(created_at__gte=start, created_at__lt=end).exclude(user=None)
    if module is not None:
        ops = ops.filter(payment_module=module)
    for r in ops.order_by().values("user_id").annotate(
            **{name: Count("id", filter=Q(operation=t)) for name, t in OPERATION_COUNTS.items()}):
        for name in OPERATION_COUNTS:
            out[name][r["user_id"]] = r[name]

    for model, module_path, _item in _SETTINGS_SOURCES.values():
        qs = model.objects.filter(created_at__gte=start, created_at__lt=end).exclude(actor=None)
        if module is not None:
            qs = qs.filter(**{module_path: module})
        for r in qs.order_by().values("actor_id").annotate(
                proposed=Count("id", filter=Q(event_type=MappingEventType.PROPOSED)),
                decided=Count("id", filter=Q(event_type__in=DECIDED_EVENTS))):
            for name in ("proposed", "decided"):
                out[f"settings_{name}"][r["actor_id"]] = out[f"settings_{name}"].get(r["actor_id"], 0) + r[name]
    return out


def user_row(u, counts: dict, d_from, d_to) -> dict:
    def n(name):
        return counts.get(name, {}).get(u.pk, 0)

    last = counts.get("_last_login", {}).get(u.pk)
    username = u.get_username()
    q = lambda **extra: urlencode({"user": username, **extra}, quote_via=quote)  # noqa: E731
    dates = {"date_from": d_from.isoformat(), "date_to": d_to.isoformat()}
    return {
        "user_id": u.pk, "username": username, "full_name": u.get_full_name() or username,
        "roles": listed_roles(u), "is_active": u.is_active,
        "logins": n("logins"),
        "failed_logins": n("failed_logins") + counts.get("_failed_by_name", {}).get(username.lower(), 0),
        "last_login": last[0] if last else None, "last_login_ip": last[1] if last else None,
        "cases": {name: n(f"case_{name}") for name in CASE_COUNTS},
        "files": {"uploaded": n("uploaded"), "refused": n("refused")},
        "cbs_dates_selected": n("cbs_dates_selected"), "runs_started": n("runs_started"),
        "settings": {"proposed": n("settings_proposed"), "decided": n("settings_decided")},
        "report_downloads": n("report_downloads"),
        "links": {"case_events": f"/audit/case-events?{q(**dates)}",
                  "user_activity": f"/audit/user-activity?{q(**dates)}",
                  "operations": f"/audit/operations?{q(**dates)}"},
    }


def _sheet(users_qs, counts, d_from, d_to) -> Sheet:
    cols = [Column("username", "User"), Column("full_name", "Name"), Column("roles", "Roles"),
            Column("is_active", "Active", BOOL), Column("logins", "Logins", COUNT),
            Column("failed_logins", "Failed logins", COUNT), Column("last_login", "Last login", DATETIME),
            Column("last_login_ip", "Last login IP")]
    cols += [Column(f"case_{k}", f"Cases {k.replace('_', ' ')}", COUNT) for k in CASE_COUNTS]
    cols += [Column("uploaded", "Files uploaded", COUNT), Column("refused", "Files refused", COUNT),
             Column("cbs_dates_selected", "CBS dates selected", COUNT), Column("runs_started", "Runs started", COUNT),
             Column("settings_proposed", "Settings proposed", COUNT),
             Column("settings_decided", "Settings decided", COUNT),
             Column("report_downloads", "Report downloads", COUNT)]

    def gen():
        for u in users_qs:
            r = user_row(u, counts, d_from, d_to)
            yield {**r, "roles": ", ".join(r["roles"]), **{f"case_{k}": v for k, v in r["cases"].items()},
                   **r["files"], "settings_proposed": r["settings"]["proposed"],
                   "settings_decided": r["settings"]["decided"]}

    return Sheet("users", "Users", cols, gen, users_qs.count())


class UserActivityReportView(V1APIView):
    permission_classes = [IsAuthenticated, HasAuditRole]

    def get(self, request):
        p, errors = request.query_params, {}
        d_from, d_to = report_range(p, errors)
        role = p.get("role") or None
        if role and role not in Role.ALL:
            errors["role"] = [f"Must be one of: {', '.join(Role.ALL)}"]
        active = _bool_param(p, "active", errors)
        module = None
        if p.get("module"):
            module = PaymentModule.objects.filter(code__iexact=p["module"].strip()).first()
            if module is None:
                errors["module"] = ["Unknown module."]
        user = (p.get("user") or "").strip()
        if errors:
            return _bad_request("Invalid input", errors)
        users = get_user_model().objects.prefetch_related("groups").order_by("username")
        if role:
            users = users.filter(users_holding_q(role)).distinct()  # ignored when roles are switched off
        if active is not None:
            users = users.filter(is_active=active)
        if user:
            users = users.filter(username__iexact=user)
        start, end = _bounds(d_from, d_to)
        counts = activity_counts(start, end, module)
        report = report_block("user_activity", "User activity report", None,
                              {"date_from": d_from, "date_to": d_to, "user": user or None, "role": role,
                               "module": module_slug(module) if module else None, "active": active},
                              request.user, versions())
        sheets = [_sheet(users, counts, d_from, d_to)]
        if wants_export(request):
            return export_response(request, report=report, module=None, sheets=sheets,
                                   file_base=range_file_base(None, "user_activity", d_from, d_to))
        paginator = legacy.QueuePagination()
        page = paginator.paginate_queryset(users, request, view=self)
        response = paginator.get_paginated_response([user_row(u, counts, d_from, d_to) for u in page])
        response.data.update({"report": report, "export": export_block(sheets)})
        return response
