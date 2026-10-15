"""
/api/v1/ views for Phase 5 Slice 1 (design docs/phase5_design_draft_v1.txt,
approved 2026-10-13): modules, users (the Maker list for reassign), meta,
the home "my work" summary, and the case endpoints.

The case endpoints SUBCLASS the existing views in exception_management.api
(P2): same forms, same service functions (roles, statuses, maker-checker,
audit CaseEvent), same serializers. v1 adds only:
  - the payment module from the URL (cases of other modules are 404),
  - payment_module and self_approval on each case,
  - an "ordering" parameter (listed fields only) and a due_date filter,
  - 400 for a malformed date filter (the old queue answers 500),
  - 401 for "not logged in",
  - the self-approval confirmation (design 4.6 / Phase 3 P4): approving an
    action you submitted yourself needs "confirm_self_approval": true, else
    400 with errors.confirm_self_approval; the approval is then recorded
    self_approved as before.
"""
from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.db.models import Exists, F, OuterRef, Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response

from api.base import ModuleMixin, SessionAuthentication401, V1APIView, active_modules, module_slug
from core.models import SettlementCycle
from exception_management import api as legacy
from exception_management.actions import ACTION_SPECS, DEFAULT_ACTION_BY_RULE, SETTLEMENT_ACTIONS, ActionType
from exception_management.alerts import digest_counts
from exception_management.forms import TRANSITIONS, _code_queryset
from exception_management.models import (
    ACTIVE_STATUSES,
    CaseEvent,
    CaseStatus,
    CaseType,
    ChangeRequestStatus,
    Decision,
    EventType,
    ExceptionCase,
    ProposedAction,
    ResponseCodeChangeRequest,
)
from exception_management.roles import Role, has_role, user_roles
from exception_management.services import BULK_PRIORITIES
from file_processing.models import FileTypeConfig, ProcessingStatus, UploadedFile
from rule_engine.models import AgeingBucket, Priority, RuleSet, RuleSetStatus, StatusCategory
from settlement.models import MapStatus, NtslCategoryMapVersion, SettlementResult, SettlementTolerance

SELF_APPROVAL_MESSAGE = "You submitted this action; approving it will be recorded as self-approved."


def _choices(enum) -> list[dict]:
    return [{"value": v, "label": label} for v, label in enum.choices]


def _bad_request(detail: str, errors: dict | None = None, **extra) -> Response:
    body = {"detail": detail}
    if errors:
        body["errors"] = errors
    body.update(extra)
    return Response(body, status=status.HTTP_400_BAD_REQUEST)


def _confirmed(value) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() in ("true", "1", "yes"))


# ---------------------------------------------------------------------------
# not module-specific
# ---------------------------------------------------------------------------
class ModuleListView(V1APIView):
    """GET /api/v1/modules/ -- active payment modules (design 4.2)."""

    def get(self, request):
        paginator = legacy.QueuePagination()
        page = paginator.paginate_queryset(active_modules(), request, view=self)
        return paginator.get_paginated_response(
            [{"code": m.code, "slug": module_slug(m), "name": m.name} for m in page]
        )


class UserListView(V1APIView):
    """GET /api/v1/users/?role=Maker -- active users holding a role (a
    superuser holds every role, as in exception_management.roles). The Maker
    list is what "reassign" offers (the same users as ReassignForm)."""

    def get(self, request):
        role = request.query_params.get("role")
        if role and role not in Role.ALL:
            return _bad_request("Unknown role", {"role": [f"Must be one of: {', '.join(Role.ALL)}"]})
        holds = Q(groups__name=role) if role else Q(groups__name__in=Role.ALL)
        qs = (get_user_model().objects.filter(is_active=True).filter(Q(is_superuser=True) | holds)
              .distinct().order_by("username").prefetch_related("groups"))
        paginator = legacy.QueuePagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        rows = []
        for u in page:
            names = set(Role.ALL) if u.is_superuser else {g.name for g in u.groups.all()}
            rows.append({"id": u.pk, "username": u.get_username(), "full_name": u.get_full_name() or u.get_username(),
                         "roles": [r for r in Role.ALL if r in names]})
        return paginator.get_paginated_response(rows)


# ---------------------------------------------------------------------------
# /api/v1/<module>/meta/ and /summary/
# ---------------------------------------------------------------------------
class ModuleView(ModuleMixin, V1APIView):
    pass


class MetaView(ModuleView):
    """Everything the screens need that differs per module or is an enum
    (design 3.1), so no module name or choice list is hard-coded in the SPA."""

    def get(self, request, module):
        m = self.module
        rule_set = (RuleSet.objects.filter(payment_module=m, status=RuleSetStatus.ACTIVE)
                    .order_by("-activated_at", "-pk").first())
        rules = []
        if rule_set is not None:
            rules = [{"code": r.code, "name": r.name, "priority": r.priority, "status_category": r.status_category,
                      "default_action_type": DEFAULT_ACTION_BY_RULE.get(r.code, "")}
                     for r in rule_set.rules.order_by("evaluation_order")]
        file_types, seen = [], set()
        for f in FileTypeConfig.objects.filter(payment_module=m, is_active=True).order_by("file_type_code", "-version"):
            if f.file_type_code in seen:
                continue
            seen.add(f.file_type_code)
            file_types.append({"code": f.file_type_code, "register_type": f.register_type,
                               "is_cycle_wise": f.is_cycle_wise, "cycles": list(f.cycle_set or [])})
        return Response({
            "module": {"code": m.code, "slug": module_slug(m), "name": m.name},
            "rule_set": rule_set.version_label if rule_set else None,
            "rules": rules,
            "case_statuses": _choices(CaseStatus),
            "active_statuses": list(ACTIVE_STATUSES),
            "case_types": _choices(CaseType),
            "priorities": _choices(Priority),
            "status_categories": _choices(StatusCategory),
            "flags": [{"value": k, "label": label} for k, label in _FLAG_LABELS.items()],
            "decisions": _choices(Decision),
            "event_types": _choices(EventType),
            "action_types": [
                {"value": a.value, "label": a.label, "code_flag": ACTION_SPECS[a].code_flag,
                 "fixed_code": ACTION_SPECS[a].fixed_code,
                 "requires_cbs_reversal_evidence": ACTION_SPECS[a].requires_cbs_reversal_evidence,
                 "requires_remarks": ACTION_SPECS[a].requires_remarks,
                 "completes_on_approval": ACTION_SPECS[a].completes_on_approval,
                 "settlement_only": a in SETTLEMENT_ACTIONS}
                for a in ActionType
            ],
            "response_codes": [{"id": rc.pk, "flag": rc.flag, "code": rc.code, "description": rc.description,
                                "label": str(rc)} for rc in _code_queryset(m.pk)],
            "ageing_buckets": [{"label": b.label, "min_days": b.min_days, "max_days": b.max_days}
                               for b in AgeingBucket.objects.filter(payment_module=m).order_by("order")],
            "file_types": file_types,
            "settlement_cycles": [{"code": c.cycle_code, "type": c.cycle_type, "start_time": c.start_time,
                                   "end_time": c.end_time}
                                  for c in SettlementCycle.objects.filter(payment_module=m, is_active=True)
                                  .order_by("start_time", "cycle_code")],
            "transitions": [{"value": k, "label": t.label} for k, t in TRANSITIONS.items()],
            "bulk_actions": ["take", "submit", "approve"],
            "bulk_priorities": list(BULK_PRIORITIES),
            "case_orderings": sorted(ORDERING_FIELDS),
            "self_approval_message": SELF_APPROVAL_MESSAGE,
        })


_FLAG_LABELS = {
    "settlement": "Settlement case",
    "overdue": "Overdue",
    "r07x": "R07x (chargeback > 35 days)",
    "needs_review": "Needs review",
    "multiple_adjustment_rows": "Multiple adjustment rows",
    "rrc_already_sent": "RRC already sent",
    "no_deadline": "No deadline",
    "previous_day_r12": "R12 on the previous day",
}
assert set(_FLAG_LABELS) == set(legacy._FLAG_FILTERS)


def _count(n, **query) -> dict:
    """A home-page count and the queue filter it links to (None: no queue view)."""
    return {"count": n, "query": query or None}


class SummaryView(ModuleView):
    """GET /api/v1/<module>/summary/ -- the home page's "my work" counts
    (design 4.3, owner P4). Case counts are digest_counts() for this module.
    Each block is returned to the role it is for; Administrator and Auditor
    get every block. Each count carries the queue filter it links to."""

    def get(self, request, module):
        roles = user_roles(request.user)
        everything = bool(roles & {Role.ADMINISTRATOR, Role.AUDITOR})
        d = digest_counts(payment_module=self.module)
        t, me = d["totals"], request.user.pk
        body = {"business_date_today": d["today"], "roles": [r for r in Role.ALL if r in roles]}
        if everything or Role.MAKER in roles:
            maker_open, maker_all = "OPEN,IN_PROGRESS", "OPEN,IN_PROGRESS,APPROVED"
            body["maker"] = {
                "high_open": _count(t["high_open"], priority="HIGH", status=maker_open),
                "high_overdue": _count(t["high_overdue"], priority="HIGH", status=maker_open, overdue="true"),
                "overdue": _count(t["overdue"], status=maker_all, overdue="true"),
                "due_today": _count(t["due_today"], status=maker_all, due_date=d["today"].isoformat()),
                "approved_waiting": _count(t["approved_waiting"], status="APPROVED"),
                "mine": _count(d["mine"][me], mine="assigned", status="IN_PROGRESS,APPROVED"),
                "mine_overdue": _count(d["mine_overdue"][me], mine="assigned", status="IN_PROGRESS,APPROVED",
                                       overdue="true"),
            }
        if everything or Role.CHECKER in roles:
            m = self.module
            body["checker"] = {
                "awaiting": _count(t["awaiting"], status="PENDING_APPROVAL"),
                "awaiting_high": _count(t["awaiting_high"], status="PENDING_APPROVAL", priority="HIGH"),
                "awaiting_submitted_by_me": _count(d["submitted"][me]),
                "needs_review": _count(t["needs_review"], status="all", flag="needs_review"),
                # Settings proposals (screens in Slice 3): counts only.
                "settings_awaiting": {
                    "response_codes": ResponseCodeChangeRequest.objects.filter(
                        payment_module=m, status=ChangeRequestStatus.PENDING).count(),
                    "ntsl_mapping": NtslCategoryMapVersion.objects.filter(
                        payment_module=m, status=MapStatus.PENDING).count(),
                    "tolerance": SettlementTolerance.objects.filter(payment_module=m, status=MapStatus.PENDING).count(),
                },
            }
        if everything or Role.OPERATIONS_USER in roles:
            body["operations"] = self._operations()
        return Response(body)

    def _operations(self) -> dict:
        """Latest business date's files and settlement status. "Missing"
        files need the expected type x cycle checklist of the business-date
        overview (design 4.11, Slice 2) and are not counted yet (None)."""
        files = UploadedFile.objects.filter(payment_module=self.module, is_current=True)
        latest = files.exclude(business_date=None).order_by("-business_date").values_list("business_date", flat=True).first()
        on_date = files.filter(business_date=latest) if latest else files.none()
        result = (SettlementResult.objects.filter(payment_module=self.module, business_date=latest, is_current=True)
                  .first() if latest else None)
        return {
            "latest_business_date": latest,
            "files_received": on_date.count(),
            "files_loaded": on_date.filter(processing_status=ProcessingStatus.LOADED).count(),
            "files_failed": on_date.filter(processing_status__in=(ProcessingStatus.FAILED,
                                                                  ProcessingStatus.VALIDATION_FAILED)).count(),
            "files_missing": None,
            # CBS files waiting for their date have no business date yet: all of them.
            "awaiting_date_selection": files.filter(processing_status=ProcessingStatus.AWAITING_DATE_SELECTION).count(),
            "settlement_status": result.status if result else None,
        }


# ---------------------------------------------------------------------------
# cases (thin wrappers over exception_management.api, design P2)
# ---------------------------------------------------------------------------
ORDERING_FIELDS = {
    "priority": "priority_rank",  # "priority" = HIGH first
    "business_date": "business_date",
    "due_date": "due_date",
    "age": "age_days",
    "status": "status",
    "rule_code": "rule_code",
    "rrn": "rrn",
    "created_at": "created_at",
    "updated_at": "updated_at",
}


def _ordering(value: str | None):
    """None/"" -> the queue order (HIGH, overdue, due date, age). Else a
    comma list of ORDERING_FIELDS, "-" for descending; ties by id. Returns
    (order_by args, error message)."""
    if not value:
        return (*legacy.QUEUE_ORDER, "pk"), None
    out = []
    for part in value.split(","):
        part = part.strip()
        desc = part.startswith("-")
        field = ORDERING_FIELDS.get(part.lstrip("-"))
        if field is None:
            return None, f"Unknown ordering {part!r}; allowed: {', '.join(sorted(ORDERING_FIELDS))} (prefix - to reverse)"
        out.append(F(field).desc(nulls_last=True) if desc else F(field).asc(nulls_last=True))
    return (*out, "pk"), None


def _own_pending(user):
    return Exists(ProposedAction.objects.filter(case=OuterRef("pk"), decision=Decision.PENDING, submitted_by=user.pk))


class _SelfApprovalField:
    def get_self_approval(self, obj) -> bool:
        """True when this user submitted the pending action: approving it
        will be recorded as self-approved (Phase 3 P4)."""
        user = self.context["request"].user
        own = getattr(obj, "own_pending_action", None)
        if own is None:
            own = obj.actions.filter(decision=Decision.PENDING, submitted_by=user.pk).exists()
        return bool(own)


class V1CaseListSerializer(_SelfApprovalField, legacy.CaseListSerializer):
    payment_module = serializers.SlugRelatedField(slug_field="code", read_only=True)
    self_approval = serializers.SerializerMethodField()

    class Meta(legacy.CaseListSerializer.Meta):
        fields = ("payment_module",) + legacy.CaseListSerializer.Meta.fields + ("self_approval",)


class V1CaseDetailSerializer(_SelfApprovalField, legacy.CaseDetailSerializer):
    payment_module = serializers.SlugRelatedField(slug_field="code", read_only=True)
    self_approval = serializers.SerializerMethodField()

    class Meta(legacy.CaseDetailSerializer.Meta):
        fields = ("payment_module",) + legacy.CaseDetailSerializer.Meta.fields + ("self_approval",)


class CaseMixin(ModuleMixin):
    authentication_classes = [SessionAuthentication401]
    detail_serializer = V1CaseDetailSerializer

    def scope(self, qs):
        return qs.filter(payment_module=self.module).annotate(own_pending_action=_own_pending(self.request.user))


def _date_param(params, name, errors) -> date | None:
    value = params.get(name)
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors[name] = ["Enter a date as YYYY-MM-DD."]
        return None


class CaseListView(CaseMixin, legacy.CaseListView):
    def get(self, request, module):
        errors = {}
        _date_param(request.query_params, "business_date", errors)
        due = _date_param(request.query_params, "due_date", errors)
        order, order_error = _ordering(request.query_params.get("ordering"))
        if order_error:
            errors["ordering"] = [order_error]
        if errors:
            return _bad_request("Invalid input", errors)
        qs, error = legacy.filter_queue(self.scope(legacy._annotated()), request)
        if error is not None:
            return error
        if due is not None:
            qs = qs.filter(due_date=due)
        paginator = legacy.QueuePagination()
        page = paginator.paginate_queryset(qs.order_by(*order), request, view=self)
        return paginator.get_paginated_response(
            V1CaseListSerializer(page, many=True, context={"request": request}).data
        )


class CaseDetailView(CaseMixin, legacy.CaseDetailView):
    def get(self, request, module, pk):
        return super().get(request, pk)


class CaseHistoryView(CaseMixin, legacy.CaseAPIView):
    """GET .../cases/<id>/history/ -- the case's events, oldest first
    (ordering=-created_at for newest first), paginated."""

    def get(self, request, module, pk):
        case = get_object_or_404(self.scope(ExceptionCase.objects.all()), pk=pk)
        order = request.query_params.get("ordering") or "created_at"
        if order not in ("created_at", "-created_at"):
            return _bad_request("Invalid input", {"ordering": ["Allowed: created_at, -created_at"]})
        qs = CaseEvent.objects.filter(case=case).order_by(order, "id" if order == "created_at" else "-id")
        paginator = legacy.QueuePagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(legacy.CaseEventSerializer(page, many=True).data)


def _self_approval_refused(case_ids: list[int]) -> Response:
    n = len(case_ids)
    detail = (SELF_APPROVAL_MESSAGE if n == 1 else
              f"You submitted the actions of {n} selected cases; approving them will be recorded as self-approved.")
    return _bad_request(detail, {"confirm_self_approval": ["Confirm the self-approval to continue."]},
                        self_approval_case_ids=case_ids)


class CaseTransitionView(CaseMixin, legacy.CaseTransitionView):
    def before_run(self, request, case, name, cleaned_data):
        if name != "approve" or not has_role(request.user, Role.CHECKER):
            return None  # the service answers 403 / 409 as usual
        own = ProposedAction.objects.filter(case=case, decision=Decision.PENDING, submitted_by=request.user.pk).exists()
        if own and not _confirmed(request.data.get("confirm_self_approval")):
            return _self_approval_refused([case.pk])
        return None

    def post(self, request, module, pk, name):
        return super().post(request, pk, name)


class BulkView(CaseMixin, legacy.BulkView):
    """Same as the existing bulk endpoint (all or nothing), limited to this
    module's cases; bulk approve needs confirm_self_approval when any of the
    selected actions was submitted by the approver."""

    def post(self, request, module, name):
        ids = request.data.get("case_ids")
        if isinstance(ids, list) and ids and all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            wanted = set(ids)
            if ExceptionCase.objects.filter(pk__in=wanted, payment_module=self.module).count() != len(wanted):
                return _bad_request("Some cases do not exist; nothing was applied")
            if name == "approve" and has_role(request.user, Role.CHECKER):
                own = sorted(ProposedAction.objects.filter(case_id__in=wanted, decision=Decision.PENDING,
                                                           submitted_by=request.user.pk)
                             .values_list("case_id", flat=True))
                if own and not _confirmed(request.data.get("confirm_self_approval")):
                    return _self_approval_refused(own)
        return super().post(request, name)
