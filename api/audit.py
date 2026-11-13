"""
/api/v1/audit/... and /api/v1/users-and-roles/ (Phase 5 Slice 4; design
4.17, 4.19, 3.5, 3.6). Contract: docs/phase5_slice4_api_contract.txt
sections B and C. Read-only: GET only (anything else 405).

  GET /audit/case-events/      exception_management.CaseEvent
  GET /audit/settings-events/  ResponseCodeChangeEvent + NtslMappingEvent + SettlementToleranceEvent
  GET /audit/user-activity/    core.UserActivity (login, logout, failed login)
  GET /audit/operations/       core.OperationLog (upload, refused upload, CBS date, run)
  GET /audit/meta/             the filter choices
  GET /audit/everything/       all four in one timeline, one sentence per entry
                               (UI refinements 9.1 / 9.2; docs/ui_refinements_api_contract.txt B5)
  GET /users-and-roles/        every account and its roles (Administrator, Auditor)

Roles: the audit lists are for the Administrator and the Auditor (design 3.5
table and 4.19 "U, A"; /me permissions "audit.view"). The users list is for
the Administrator and the Auditor too (owner decision 2026-10-22, a deviation
from design 4.17 "A"; /me "users.view"); users and roles are still edited in
the Django admin (P3), whose link only a staff Administrator gets.

Common filters: date_from / date_to (inclusive local dates on created_at),
user (username, case-insensitive), event_type (csv), module (slug; not on
user activity), ordering (-created_at default | created_at). All four logs
grow forever: every list is paged on the server, and the date and module
filters use indexed columns where the table has them.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.db.models import CharField, F, Q, Value
from django.utils import timezone
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from api.base import V1APIView, module_slug
from api.operations import _bad_request
from api.results import _enum_filter
from api.settings_views import SETTINGS_KINDS
from core.models import OperationLog, OperationType, PaymentModule, UserActivity, UserActivityType
from exception_management import api as legacy
from exception_management.models import CaseEvent, CodeChangeEventType, EventType, ResponseCodeChangeEvent
from exception_management.roles import Role, has_role, listed_roles, user_roles, users_holding_q
from settlement.models import MappingEventType, NtslMappingEvent, SettlementToleranceEvent

AUDIT_ROLES = frozenset({Role.ADMINISTRATOR, Role.AUDITOR})
ORDERINGS = ("-created_at", "created_at")
SETTINGS_EVENT_TYPES = sorted(set(CodeChangeEventType.values) | set(MappingEventType.values))
_SETTINGS_EVENT_LABELS = {**dict(MappingEventType.choices), **dict(CodeChangeEventType.choices)}
_SETTINGS_ROUTE = {"response_code": "response-codes", "ntsl_mapping": "ntsl-mapping", "tolerance": "tolerance"}


class HasAuditRole(BasePermission):
    message = "Only the Administrator and the Auditor may read the audit trail."

    def has_permission(self, request, view):
        return bool(user_roles(request.user) & AUDIT_ROLES)


class AuditView(V1APIView):
    permission_classes = [IsAuthenticated, HasAuditRole]


# ---------------------------------------------------------------------------
# filters
# ---------------------------------------------------------------------------
def _date(p, name, errors) -> date | None:
    v = p.get(name)
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        errors[name] = ["Enter a date as YYYY-MM-DD."]
        return None


def _day_start(d: date) -> datetime:
    return timezone.make_aware(datetime.combine(d, time.min))


class Filters:
    """Parses the common filters once; apply() maps them onto one table's
    field names. errors holds the 400 messages."""

    def __init__(self, request, *, event_types, with_module=True, extra_dates=()):
        p, self.errors = request.query_params, {}
        self.date_from = _date(p, "date_from", self.errors)
        self.date_to = _date(p, "date_to", self.errors)
        if self.date_from and self.date_to and self.date_from > self.date_to:
            self.errors["date_to"] = ["Must not be before date_from."]
        self.user = (p.get("user") or "").strip()
        self.event_types = _enum_filter(p, "event_type", list(event_types), self.errors)
        self.ordering = p.get("ordering") or "-created_at"
        if self.ordering not in ORDERINGS:
            self.errors["ordering"] = [f"Allowed: {', '.join(ORDERINGS)}"]
        self.module = None
        if with_module and p.get("module"):
            self.module = PaymentModule.objects.filter(code__iexact=p["module"].strip()).first()
            if self.module is None:
                self.errors["module"] = ["Unknown module."]
        self.dates = {name: _date(p, name, self.errors) for name in extra_dates}

    def apply(self, qs, *, user_field, type_field, module_field=None, module_value="obj", created="created_at"):
        if self.date_from:
            qs = qs.filter(**{f"{created}__gte": _day_start(self.date_from)})
        if self.date_to:
            qs = qs.filter(**{f"{created}__lt": _day_start(self.date_to + timedelta(days=1))})
        if self.user:
            qs = qs.filter(**{f"{user_field}__iexact": self.user})
        if self.event_types:
            qs = qs.filter(**{f"{type_field}__in": self.event_types})
        if self.module is not None and module_field:
            qs = qs.filter(**{module_field: self.module.code if module_value == "code" else self.module})
        return qs

    def order(self, *fields_desc):
        """("created_at", "id") -> newest first, or oldest first."""
        if self.ordering == "created_at":
            return list(fields_desc)
        return [f"-{f}" for f in fields_desc]


def _page(view, request, qs):
    paginator = legacy.QueuePagination()
    return paginator, paginator.paginate_queryset(qs, request, view=view)


# ---------------------------------------------------------------------------
# B1 case events
# ---------------------------------------------------------------------------
class CaseEventAuditView(AuditView):
    def get(self, request):
        f = Filters(request, event_types=EventType.values, extra_dates=("business_date",))
        p = request.query_params
        case_id = p.get("case")
        if case_id and not case_id.isdigit():
            f.errors["case"] = ["A case id."]
        if f.errors:
            return _bad_request("Invalid input", f.errors)
        qs = f.apply(CaseEvent.objects.all(), user_field="actor_username", type_field="event_type",
                     module_field="payment_module_code", module_value="code")
        if f.dates["business_date"]:
            qs = qs.filter(business_date=f.dates["business_date"])
        if case_id:
            qs = qs.filter(case_id=int(case_id))
        if p.get("rrn"):
            qs = qs.filter(rrn=p["rrn"].strip())
        paginator, page = _page(self, request, qs.order_by(*f.order("created_at", "id")))
        return paginator.get_paginated_response([case_event_row(e) for e in page])


_CASE_EVENT_LABELS = dict(EventType.choices)


def case_event_row(e: CaseEvent) -> dict:
    slug = e.payment_module_code.lower()
    snap = e.action_snapshot or {}
    return {
        "id": e.pk, "created_at": e.created_at, "module": slug,
        "case_id": e.case_id, "business_date": e.business_date, "rrn": e.rrn,
        "event_type": e.event_type, "event_type_label": _CASE_EVENT_LABELS.get(e.event_type, e.event_type),
        "actor_username": e.actor_username or None, "source": e.source,
        "old_status": e.old_status, "new_status": e.new_status, "remarks": e.remarks,
        "changes": e.changes, "action_snapshot": e.action_snapshot,
        "self_approved": e.event_type == EventType.APPROVED and bool(snap.get("self_approved")),
        "link": f"/{slug}/cases/{e.case_id}",
    }


# ---------------------------------------------------------------------------
# B2 settings events (three tables, one list)
# ---------------------------------------------------------------------------
_SETTINGS_SOURCES = {
    # kind: (model, path to the payment module, path to the item id)
    "response_code": (ResponseCodeChangeEvent, "change_request__payment_module", "change_request_id"),
    "ntsl_mapping": (NtslMappingEvent, "version__payment_module", "version_id"),
    "tolerance": (SettlementToleranceEvent, "tolerance__payment_module", "tolerance_id"),
}


def _item_labels(kind, ids) -> dict[int, str]:
    model = _SETTINGS_SOURCES[kind][0]
    if kind == "response_code":
        from exception_management.models import ResponseCodeChangeRequest
        out = {}
        for r in ResponseCodeChangeRequest.objects.filter(pk__in=ids).select_related("response_code"):
            target = str(r.response_code) if r.response_code else f"new {r.flag} code '{r.new_description}'"
            out[r.pk] = f"{r.get_kind_display()}: {target}"
        return out
    parent = model._meta.get_field("version" if kind == "ntsl_mapping" else "tolerance").related_model
    prefix = "NTSL mapping" if kind == "ntsl_mapping" else "Tolerance"
    return {pk: f"{prefix} v{v}" for pk, v in parent.objects.filter(pk__in=ids).values_list("pk", "version")}


class SettingsEventAuditView(AuditView):
    def get(self, request):
        f = Filters(request, event_types=SETTINGS_EVENT_TYPES)
        kinds = _enum_filter(request.query_params, "kind", list(SETTINGS_KINDS), f.errors) or list(SETTINGS_KINDS)
        if f.errors:
            return _bad_request("Invalid input", f.errors)
        parts = []
        for kind in kinds:
            model, module_path, _item = _SETTINGS_SOURCES[kind]
            qs = f.apply(model.objects.all(), user_field="actor_username", type_field="event_type",
                         module_field=module_path)
            # Only annotations, in the same order in every part, so the UNION lines up.
            parts.append(qs.order_by().annotate(kind=Value(kind, output_field=CharField()), eid=F("pk"),
                                                ts=F("created_at")).values("kind", "eid", "ts"))
        union = parts[0].union(*parts[1:], all=True) if len(parts) > 1 else parts[0]
        union = union.order_by(*f.order("ts", "kind", "eid"))
        paginator, page = _page(self, request, union)
        rows = settings_event_rows([(r["kind"], r["eid"]) for r in page])
        return paginator.get_paginated_response([rows[(r["kind"], r["eid"])] for r in page])


def settings_event_rows(refs) -> dict[tuple[str, int], dict]:
    """{(kind, event id): row} for a page of settings events (two queries per kind)."""
    by_kind: dict[str, list[int]] = {}
    for kind, eid in refs:
        by_kind.setdefault(kind, []).append(eid)
    events, labels = {}, {}
    for kind, ids in by_kind.items():
        model, module_path, item_field = _SETTINGS_SOURCES[kind]
        for e in model.objects.filter(pk__in=ids).annotate(module_code=F(f"{module_path}__code")):
            events[(kind, e.pk)] = e
        labels[kind] = _item_labels(kind, {getattr(e, item_field) for (k, _), e in events.items() if k == kind})
    rows = {}
    for (kind, eid), e in events.items():
        item_id = getattr(e, _SETTINGS_SOURCES[kind][2])
        slug = e.module_code.lower()
        rows[(kind, eid)] = {
            "id": f"{kind}:{e.pk}", "kind": kind, "kind_label": SETTINGS_KINDS[kind], "event_id": e.pk,
            "created_at": e.created_at, "module": slug, "event_type": e.event_type,
            "event_type_label": _SETTINGS_EVENT_LABELS.get(e.event_type, e.event_type),
            "actor_username": e.actor_username or None, "remarks": e.remarks, "changes": e.changes,
            "self_approved": e.self_approved, "item_id": item_id,
            "item_label": labels[kind].get(item_id, ""),
            "link": f"/{slug}/settings/{_SETTINGS_ROUTE[kind]}",
        }
    return rows


# ---------------------------------------------------------------------------
# B3 user activity, B4 operations
# ---------------------------------------------------------------------------
class UserActivityAuditView(AuditView):
    def get(self, request):
        f = Filters(request, event_types=UserActivityType.values, with_module=False)
        if f.errors:
            return _bad_request("Invalid input", f.errors)
        qs = f.apply(UserActivity.objects.all(), user_field="username", type_field="activity_type")
        paginator, page = _page(self, request, qs.order_by(*f.order("created_at", "id")))
        return paginator.get_paginated_response([activity_row(a) for a in page])


def activity_row(a: UserActivity) -> dict:
    return {"id": a.pk, "created_at": a.created_at, "event_type": a.activity_type,
            "event_type_label": a.get_activity_type_display(), "username": a.username, "user_id": a.user_id,
            "ip_address": a.ip_address, "user_agent": a.user_agent, "path": a.path}


class OperationAuditView(AuditView):
    def get(self, request):
        f = Filters(request, event_types=OperationType.values, extra_dates=("business_date",))
        if f.errors:
            return _bad_request("Invalid input", f.errors)
        qs = f.apply(OperationLog.objects.select_related("payment_module"), user_field="username",
                     type_field="operation", module_field="payment_module")
        if f.dates["business_date"]:
            qs = qs.filter(business_date=f.dates["business_date"])
        paginator, page = _page(self, request, qs.order_by(*f.order("created_at", "id")))
        return paginator.get_paginated_response([operation_row(o) for o in page])


def operation_row(o: OperationLog) -> dict:
    # No module (the user activity report download, Phase 6 B7): module and link are null.
    slug = module_slug(o.payment_module) if o.payment_module_id else None
    link = None if slug is None else (
        f"/{slug}/files/{o.uploaded_file_id}" if o.uploaded_file_id
        else f"/{slug}/dates/{o.business_date.isoformat()}" if o.business_date else None)
    return {
        "id": o.pk, "created_at": o.created_at, "module": slug, "event_type": o.operation,
        "event_type_label": o.get_operation_display(), "username": o.username or None, "source": o.source,
        "business_date": o.business_date, "uploaded_file_id": o.uploaded_file_id, "run_id": o.run_id,
        "details": o.details, "link": link,
    }


# ---------------------------------------------------------------------------
# B5 everything (UI refinements 9.1 / 9.2)
# ---------------------------------------------------------------------------
CASE_EVENT, SETTINGS_EVENT, USER_ACTIVITY, OPERATION = "case_event", "settings_event", "user_activity", "operation"
SOURCES = {CASE_EVENT: "Case event", SETTINGS_EVENT: "Settings change", USER_ACTIVITY: "Login / logout",
           OPERATION: "Operation"}
ALL_EVENT_TYPES = sorted(set(EventType.values) | set(SETTINGS_EVENT_TYPES) | set(UserActivityType.values)
                         | set(OperationType.values))


def _who(username) -> str:
    return username or "The system"


def _day(d) -> str:
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d)
        except ValueError:
            return d
    return f"{d:%d %b %Y}" if d else ""


def _snapshot_reply(snap: dict | None) -> str:
    """'reply DRC 102 - Online Reversal' / 'action Adjustment entry in CBS' from a CaseEvent action snapshot."""
    from exception_management.actions import ACTION_SPECS, ActionType

    snap = snap or {}
    rc = snap.get("response_code") or {}
    action = snap.get("action_type") or ""
    spec = ACTION_SPECS.get(action)
    if spec is not None and spec.code_flag and rc:
        return f"reply {rc.get('flag') or spec.code_flag} {rc.get('code') or '(code not known yet)'} - " \
               f"{rc.get('description') or ''}".rstrip(" -")
    if action in ACTION_SPECS:
        return f"action {ActionType(action).label}"
    return "the action"


def case_event_summary(r: dict) -> str:
    who, case, t = _who(r["actor_username"]), f"case #{r['case_id']}", r["event_type"]
    changes = r.get("changes") or {}
    if t == EventType.SUBMITTED:
        return f"{who} submitted {_snapshot_reply(r['action_snapshot'])} on {case}"
    if t == EventType.APPROVED:
        return (f"{who} approved {_snapshot_reply(r['action_snapshot'])} on {case}"
                + (" (self-approved)" if r["self_approved"] else ""))
    if t == EventType.REJECTED:
        return f"{who} rejected {_snapshot_reply(r['action_snapshot'])} on {case}"
    if t == EventType.RULE_CHANGED and isinstance(changes.get("rule_code"), list) and len(changes["rule_code"]) == 2:
        old, new = changes["rule_code"]
        return f"{who} changed the rule on {case} from {old} to {new}"
    if t == EventType.REASSIGNED and isinstance(changes.get("assigned_to"), list):
        return f"{who} reassigned {case} to {changes['assigned_to'][-1] or 'nobody'}"
    if t == EventType.CLOSED_BY_SYSTEM:  # owner 2026-11-09
        name = changes.get("npci_file_name")
        name = name[-1] if isinstance(name, list) and name else None
        return f"{who} closed {case}: no-money reply included in NPCI file {name or '(unknown)'}"
    if t == EventType.CREDIT_FILE_APPROVED:  # owner 2026-11-11
        name = changes.get("credit_voucher_file_name")
        name = name[-1] if isinstance(name, list) and name else None
        return f"{who} approved and closed {case} through the CBS credit file {name or '(unknown)'}"
    verbs = {
        EventType.CREATED: "created {c}", EventType.RESULT_UPDATED: "updated the rule result details on {c}",
        EventType.RULE_CHANGED: "changed the rule on {c}", EventType.FLAGGED_FOR_REVIEW: "flagged {c} for review",
        EventType.SUPERSEDED: "closed {c}: no longer needs action", EventType.REOPENED: "reopened {c}",
        EventType.ADJUSTMENT_ROWS_CHANGED: "changed the multiple-adjustment-rows flag on {c}",
        EventType.WARNINGS_CHANGED: "changed a warning on {c}", EventType.TAKEN: "took {c}",
        EventType.RELEASED: "released {c}", EventType.REASSIGNED: "reassigned {c}",
        EventType.COMPLETED: "marked {c} done", EventType.REMARK: "added a remark on {c}",
        EventType.REVIEW_CLEARED: "cleared the review flag on {c}",
    }
    return f"{who} {verbs.get(t, r['event_type_label'].lower() + ' on {c}').format(c=case)}"


def settings_event_summary(r: dict) -> str:
    item = r["item_label"] or r["kind_label"]
    if r["kind"] == "response_code":
        item = f"the response code change '{item}'"
    t = r["event_type"]
    if t == MappingEventType.RETIRED:
        return f"{item[0].upper()}{item[1:]} was retired (replaced)"
    verb = {"PROPOSED": "proposed", "APPROVED": "approved", "REJECTED": "rejected", "SEEDED": "seeded"}.get(
        t, r["event_type_label"].lower())
    return f"{_who(r['actor_username'])} {verb} {item}" + (" (self-approved)" if r["self_approved"] else "")


def activity_summary(r: dict) -> str:
    who = r["username"] or "an unknown user"
    return {UserActivityType.LOGIN: f"{who} logged in", UserActivityType.LOGOUT: f"{who} logged out",
            UserActivityType.LOGIN_FAILED: f"Failed login for {who}"}.get(r["event_type"],
                                                                         f"{who}: {r['event_type_label']}")


def operation_summary(r: dict) -> str:
    who, d, t = _who(r["username"]), r.get("details") or {}, r["event_type"]
    on = f" for {_day(r['business_date'])}" if r["business_date"] else ""
    name = d.get("filename") or d.get("file_name") or "a file"
    if t == OperationType.FILE_UPLOADED:
        return f"{who} uploaded {name}{on}"
    if t == OperationType.FILE_REFUSED:
        return f"{who}'s upload of {name}{on} was refused"
    if t == OperationType.BUSINESS_DATE_SELECTED:
        return f"{who} chose business date {_day(r['business_date'])} for {name}"
    if t == OperationType.RUN_STARTED:
        return f"{who} started reconciliation run #{r['run_id']}{on}"
    if t == OperationType.NPCI_FILE_GENERATED:
        return f"{who} generated {name} ({d.get('rows', 0)} rows){on}"
    if t == OperationType.NPCI_FILE_DOWNLOADED:
        return f"{who} downloaded {name}"
    if t == OperationType.ORIGINAL_FILE_DOWNLOADED:
        return f"{who} downloaded the original file {name}"
    if t == OperationType.CREDIT_VOUCHER_GENERATED:  # owner 2026-11-11
        return f"{who} generated the CBS credit file {name} ({d.get('rows', 0)} rows){on}"
    if t == OperationType.CREDIT_VOUCHER_DOWNLOADED:
        return f"{who} downloaded the CBS credit file {name}"
    if t == OperationType.CREDIT_VOUCHER_APPROVED:
        return (f"{who} uploaded the checked CBS credit file {name}: {d.get('approved', 0)} case(s) approved, "
                f"{d.get('left_open', 0)} left open")
    if t == OperationType.CREDIT_VOUCHER_REFUSED:
        return f"{who}'s upload of the checked CBS credit file {name} was refused"
    if t == OperationType.REPORT_EXPORTED:
        return f"{who} downloaded the report {name} ({d.get('row_count', 0)} rows)"
    return f"{who}: {r['event_type_label']}{on}"


class EverythingAuditView(AuditView):
    """One timeline of the four audit lists, newest first. One UNION ALL of
    (source, id, created_at) with each list's filters applied in its own
    branch, ordered and limited in the database; then the page's rows are
    read source by source (contract B5)."""

    def get(self, request):
        f = Filters(request, event_types=ALL_EVENT_TYPES)
        sources = _enum_filter(request.query_params, "source", list(SOURCES), f.errors) or list(SOURCES)
        if f.errors:
            return _bad_request("Invalid input", f.errors)
        wanted = set(f.event_types or ())
        parts = []

        def branch(qs, src, type_values, **apply):
            if wanted and not wanted.intersection(type_values):
                return  # none of the requested event types belongs to this source
            f.event_types = sorted(wanted.intersection(type_values)) if wanted else None
            qs = f.apply(qs, **apply)
            parts.append(qs.order_by().annotate(src=Value(src, output_field=CharField()), eid=F("pk"),
                                                ts=F("created_at")).values("src", "eid", "ts"))

        if CASE_EVENT in sources:
            branch(CaseEvent.objects.all(), CASE_EVENT, EventType.values, user_field="actor_username",
                   type_field="event_type", module_field="payment_module_code", module_value="code")
        if SETTINGS_EVENT in sources:
            for kind, (model, module_path, _item) in _SETTINGS_SOURCES.items():
                branch(model.objects.all(), f"{SETTINGS_EVENT}:{kind}", SETTINGS_EVENT_TYPES,
                       user_field="actor_username", type_field="event_type", module_field=module_path)
        if USER_ACTIVITY in sources and f.module is None:  # logins have no module
            branch(UserActivity.objects.all(), USER_ACTIVITY, UserActivityType.values, user_field="username",
                   type_field="activity_type")
        if OPERATION in sources:
            branch(OperationLog.objects.all(), OPERATION, OperationType.values, user_field="username",
                   type_field="operation", module_field="payment_module")
        if not parts:
            paginator, page = _page(self, request, [])
            return paginator.get_paginated_response([])
        union = parts[0].union(*parts[1:], all=True) if len(parts) > 1 else parts[0]
        paginator, page = _page(self, request, union.order_by(*f.order("ts", "src", "eid")))
        return paginator.get_paginated_response(everything_rows(list(page)))


def everything_rows(page: list[dict]) -> list[dict]:
    ids: dict[str, list[int]] = {}
    for r in page:
        ids.setdefault(r["src"].split(":")[0], []).append(r["eid"])
    cases = {e.pk: case_event_row(e) for e in CaseEvent.objects.filter(pk__in=ids.get(CASE_EVENT, []))}
    settings_rows = settings_event_rows([(r["src"].split(":", 1)[1], r["eid"]) for r in page
                                         if r["src"].startswith(f"{SETTINGS_EVENT}:")])
    logins = {a.pk: activity_row(a) for a in UserActivity.objects.filter(pk__in=ids.get(USER_ACTIVITY, []))}
    ops = {o.pk: operation_row(o) for o in OperationLog.objects.filter(pk__in=ids.get(OPERATION, []))
           .select_related("payment_module")}
    out = []
    for r in page:
        src = r["src"].split(":")[0]
        if src == CASE_EVENT:
            d = cases[r["eid"]]
            row = (d["module"], d["actor_username"], d["event_type"], d["event_type_label"],
                   case_event_summary(d), d["link"])
        elif src == SETTINGS_EVENT:
            d = settings_rows[(r["src"].split(":", 1)[1], r["eid"])]
            row = (d["module"], d["actor_username"], d["event_type"], d["event_type_label"],
                   settings_event_summary(d), d["link"])
        elif src == USER_ACTIVITY:
            d = logins[r["eid"]]
            row = (None, d["username"] or None, d["event_type"], d["event_type_label"], activity_summary(d), None)
        else:
            d = ops[r["eid"]]
            row = (d["module"], d["username"], d["event_type"], d["event_type_label"], operation_summary(d),
                   d["link"])
        module, username, event_type, label, summary, link = row
        out.append({"id": f"{src}:{d['id']}",  # settings: "settings_event:<kind>:<id>"
                    "source": src, "source_label": SOURCES[src], "event_id": r["eid"], "created_at": d["created_at"],
                    "module": module, "username": username, "event_type": event_type, "event_type_label": label,
                    "summary": summary, "link": link, "details": d})
    return out


def _choices(enum) -> list[dict]:
    return [{"value": v, "label": label} for v, label in enum.choices]


class AuditMetaView(AuditView):
    def get(self, request):
        # Every module that has ever existed may appear in the logs, not only the active ones.
        modules = PaymentModule.objects.order_by("code")
        return Response({
            "modules": [{"code": m.code, "slug": module_slug(m), "name": m.name, "is_active": m.is_active}
                        for m in modules],
            "case_event_types": _choices(EventType),
            "settings_kinds": [{"value": k, "label": v} for k, v in SETTINGS_KINDS.items()],
            "settings_event_types": [{"value": v, "label": _SETTINGS_EVENT_LABELS[v]} for v in SETTINGS_EVENT_TYPES],
            "user_activity_types": _choices(UserActivityType),
            "operation_types": _choices(OperationType),
            "orderings": list(ORDERINGS),
            "everything_sources": [{"value": k, "label": v} for k, v in SOURCES.items()],
        })


# ---------------------------------------------------------------------------
# C1 users and roles (read-only; editing stays in the Django admin, P3)
# ---------------------------------------------------------------------------
class UsersAndRolesView(V1APIView):
    # Administrator and Auditor (owner decision 2026-10-22, a deviation from design 4.17 "A").
    permission_classes = [IsAuthenticated, HasAuditRole]

    def get(self, request):
        p, errors = request.query_params, {}
        role = p.get("role")
        if role and role not in Role.ALL:
            errors["role"] = [f"Must be one of: {', '.join(Role.ALL)}"]
        active = p.get("active")
        if active and active not in ("true", "false"):
            errors["active"] = ["Allowed: true, false"]
        if errors:
            return _bad_request("Invalid input", errors)
        qs = get_user_model().objects.prefetch_related("groups").order_by("username")
        if role:
            qs = qs.filter(users_holding_q(role)).distinct()  # ignored when roles are switched off
        if active:
            qs = qs.filter(is_active=active == "true")
        search = (p.get("search") or "").strip()
        if search:
            qs = qs.filter(Q(username__icontains=search) | Q(first_name__icontains=search)
                           | Q(last_name__icontains=search))
        paginator, page = _page(self, request, qs)
        rows = []
        for u in page:
            rows.append({"id": u.pk, "username": u.get_username(), "full_name": u.get_full_name() or u.get_username(),
                         "roles": listed_roles(u), "is_superuser": u.is_superuser,
                         "is_active": u.is_active, "last_login": u.last_login})
        response = paginator.get_paginated_response(rows)
        # The Django admin link is for the Administrator only (an Auditor gets null even if staff).
        can_admin = request.user.is_staff and has_role(request.user, Role.ADMINISTRATOR)
        response.data["admin_users_url"] = "/admin/auth/user/" if can_admin else None
        return response

