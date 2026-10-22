"""
/api/v1/audit/... and /api/v1/users-and-roles/ (Phase 5 Slice 4; design
4.17, 4.19, 3.5, 3.6). Contract: docs/phase5_slice4_api_contract.txt
sections B and C. Read-only: GET only (anything else 405).

  GET /audit/case-events/      exception_management.CaseEvent
  GET /audit/settings-events/  ResponseCodeChangeEvent + NtslMappingEvent + SettlementToleranceEvent
  GET /audit/user-activity/    core.UserActivity (login, logout, failed login)
  GET /audit/operations/       core.OperationLog (upload, refused upload, CBS date, run)
  GET /audit/meta/             the filter choices
  GET /users-and-roles/        every account and its roles (Administrator)

Roles: the audit lists are for the Administrator and the Auditor (design 3.5
table and 4.19 "U, A"; /me permissions "audit.view"). The users list is for
the Administrator only (design 4.17 "A"); users and roles are still edited in
the Django admin (P3).

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
from exception_management.roles import Role, user_roles
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


class IsAdministrator(BasePermission):
    message = "Only the Administrator may read the list of users and roles."

    def has_permission(self, request, view):
        return Role.ADMINISTRATOR in user_roles(request.user)


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
        labels = dict(EventType.choices)
        rows = []
        for e in page:
            slug = e.payment_module_code.lower()
            snap = e.action_snapshot or {}
            rows.append({
                "id": e.pk, "created_at": e.created_at, "module": slug,
                "case_id": e.case_id, "business_date": e.business_date, "rrn": e.rrn,
                "event_type": e.event_type, "event_type_label": labels.get(e.event_type, e.event_type),
                "actor_username": e.actor_username or None, "source": e.source,
                "old_status": e.old_status, "new_status": e.new_status, "remarks": e.remarks,
                "changes": e.changes, "action_snapshot": e.action_snapshot,
                "self_approved": e.event_type == EventType.APPROVED and bool(snap.get("self_approved")),
                "link": f"/{slug}/cases/{e.case_id}",
            })
        return paginator.get_paginated_response(rows)


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
        by_kind: dict[str, list[int]] = {}
        for r in page:
            by_kind.setdefault(r["kind"], []).append(r["eid"])
        events, labels = {}, {}
        for kind, ids in by_kind.items():
            model, module_path, item_field = _SETTINGS_SOURCES[kind]
            for e in model.objects.filter(pk__in=ids).annotate(module_code=F(f"{module_path}__code")):
                events[(kind, e.pk)] = e
            labels[kind] = _item_labels(kind, {getattr(e, item_field) for (k, _), e in events.items() if k == kind})
        rows = []
        for r in page:
            kind = r["kind"]
            e = events[(kind, r["eid"])]
            item_id = getattr(e, _SETTINGS_SOURCES[kind][2])
            slug = e.module_code.lower()
            rows.append({
                "id": f"{kind}:{e.pk}", "kind": kind, "kind_label": SETTINGS_KINDS[kind], "event_id": e.pk,
                "created_at": e.created_at, "module": slug, "event_type": e.event_type,
                "event_type_label": _SETTINGS_EVENT_LABELS.get(e.event_type, e.event_type),
                "actor_username": e.actor_username or None, "remarks": e.remarks, "changes": e.changes,
                "self_approved": e.self_approved, "item_id": item_id,
                "item_label": labels[kind].get(item_id, ""),
                "link": f"/{slug}/settings/{_SETTINGS_ROUTE[kind]}",
            })
        return paginator.get_paginated_response(rows)


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
        return paginator.get_paginated_response([{
            "id": a.pk, "created_at": a.created_at, "event_type": a.activity_type,
            "event_type_label": a.get_activity_type_display(), "username": a.username, "user_id": a.user_id,
            "ip_address": a.ip_address, "user_agent": a.user_agent, "path": a.path,
        } for a in page])


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
        rows = []
        for o in page:
            slug = module_slug(o.payment_module)
            link = (f"/{slug}/files/{o.uploaded_file_id}" if o.uploaded_file_id
                    else f"/{slug}/dates/{o.business_date.isoformat()}" if o.business_date else None)
            rows.append({
                "id": o.pk, "created_at": o.created_at, "module": slug, "event_type": o.operation,
                "event_type_label": o.get_operation_display(), "username": o.username or None, "source": o.source,
                "business_date": o.business_date, "uploaded_file_id": o.uploaded_file_id, "run_id": o.run_id,
                "details": o.details, "link": link,
            })
        return paginator.get_paginated_response(rows)


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
        })


# ---------------------------------------------------------------------------
# C1 users and roles (read-only; editing stays in the Django admin, P3)
# ---------------------------------------------------------------------------
class UsersAndRolesView(V1APIView):
    permission_classes = [IsAuthenticated, IsAdministrator]

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
            qs = qs.filter(Q(is_superuser=True) | Q(groups__name=role)).distinct()
        if active:
            qs = qs.filter(is_active=active == "true")
        search = (p.get("search") or "").strip()
        if search:
            qs = qs.filter(Q(username__icontains=search) | Q(first_name__icontains=search)
                           | Q(last_name__icontains=search))
        paginator, page = _page(self, request, qs)
        rows = []
        for u in page:
            names = set(Role.ALL) if u.is_superuser else {g.name for g in u.groups.all()}
            rows.append({"id": u.pk, "username": u.get_username(), "full_name": u.get_full_name() or u.get_username(),
                         "roles": [r for r in Role.ALL if r in names], "is_superuser": u.is_superuser,
                         "is_active": u.is_active, "last_login": u.last_login})
        response = paginator.get_paginated_response(rows)
        response.data["admin_users_url"] = "/admin/auth/user/" if request.user.is_staff else None
        return response

