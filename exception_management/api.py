"""
Thin DRF API for the Phase 5 front end (design D1). Session authentication
only (the same Django login as the admin; no new auth scheme). Reading needs
one of the five roles; every change goes through exception_management.services
(which checks the role and status) using the same input forms as the admin.

  GET  /api/exceptions/cases/                    queue (filters, paginated)
  GET  /api/exceptions/cases/<id>/               detail
  POST /api/exceptions/cases/<id>/<transition>/  take | release | reassign | submit | approve | reject |
                                                 complete | remark | clear_review
  POST /api/exceptions/cases/bulk/<take|submit|approve>/   {"case_ids": [...], ...}  all or nothing

Queue filters (query string): business_date, status (default: active;
"all"; or a status), priority, rule_code, case_type=TRANSACTION|SETTLEMENT,
overdue=true|false, mine=assigned|approval, flag=<settlement|overdue|r07x|
needs_review|multiple_adjustment_rows|rrc_already_sent|no_deadline|
previous_day_r12>, rrn. Errors: 400 invalid input / refused bulk, 403 role,
409 wrong status.

Settlement cases (Phase 4 S5) are in the same queue with case_type
SETTLEMENT, rrn "" and rule_result null; the detail adds "settlement" (the
result, SOP check and cycle x side rows). Their only action is
SETTLEMENT_EXPLANATION (remarks = the explanation); never bulk.
"""
from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from exception_management import services
from exception_management.detail import case_flags, case_warnings, overlay_rows, settlement_detail
from exception_management.forms import TRANSITIONS, BulkApproveForm, BulkSubmitForm
from exception_management.models import (
    ACTIVE_STATUSES,
    ATTENTION_RULE_CODES,
    QUEUE_ORDER,
    CaseEvent,
    CaseStatus,
    ExceptionCase,
    ProposedAction,
)
from exception_management.roles import Role, has_role, user_roles


class HasAnyRole(BasePermission):
    message = "One of the roles Administrator, Operations User, Maker, Checker or Auditor is required."

    def has_permission(self, request, view):
        return bool(user_roles(request.user))


class CaseAPIView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, HasAnyRole]


# ---------------------------------------------------------------------------
# serializers
# ---------------------------------------------------------------------------
class CaseListSerializer(serializers.ModelSerializer):
    assigned_to = serializers.SlugRelatedField(slug_field="username", read_only=True)
    age_days = serializers.IntegerField(read_only=True)
    ageing_bucket = serializers.CharField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    flags = serializers.SerializerMethodField()

    class Meta:
        model = ExceptionCase
        fields = ("id", "business_date", "case_type", "rrn", "rule_code", "status_category", "priority", "status", "due_date",
                  "due_cutoff_time", "age_days", "ageing_bucket", "is_overdue", "assigned_to",
                  "default_action_type", "flags", "rrc_already_sent_on")

    def get_flags(self, obj):
        return case_flags(obj)


class ProposedActionSerializer(serializers.ModelSerializer):
    response_code = serializers.StringRelatedField()
    submitted_by = serializers.SlugRelatedField(slug_field="username", read_only=True)
    decided_by = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = ProposedAction
        exclude = ("case", "completed_by")


class CaseEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = CaseEvent
        fields = ("id", "created_at", "event_type", "actor_username", "source", "old_status", "new_status",
                  "remarks", "changes", "action_snapshot")


class CaseDetailSerializer(CaseListSerializer):
    default_response_code = serializers.StringRelatedField()
    rule_result = serializers.SerializerMethodField()
    warnings = serializers.SerializerMethodField()
    overlay_rows = serializers.SerializerMethodField()
    settlement = serializers.SerializerMethodField()
    actions = ProposedActionSerializer(many=True, read_only=True)
    events = CaseEventSerializer(many=True, read_only=True)
    available_transitions = serializers.SerializerMethodField()

    class Meta(CaseListSerializer.Meta):
        fields = CaseListSerializer.Meta.fields + (
            "default_response_code", "needs_review", "needs_review_reason", "multiple_adjustment_rows", "no_deadline",
            "result_missing", "created_at", "updated_at", "rule_result", "warnings", "overlay_rows", "settlement",
            "actions",
            "events", "available_transitions",
        )

    def get_rule_result(self, obj):
        r = obj.rule_result
        if r is None:  # settlement case
            return None
        return {"id": r.pk, "rule_code": r.rule_code, "rule_set": r.rule_set.version_label, "action": r.action,
                "reason": r.reason, "tat_label": r.tat_label, "evaluated_at": r.evaluated_at, "facts": r.facts,
                "is_superseded": r.is_superseded}

    def get_warnings(self, obj):
        return case_warnings(obj)

    def get_overlay_rows(self, obj):
        return overlay_rows(obj)

    def get_settlement(self, obj):
        return settlement_detail(obj)

    def get_available_transitions(self, obj):
        return services.available_transitions(obj, self.context["request"].user)


# ---------------------------------------------------------------------------
# views
# ---------------------------------------------------------------------------
_FLAG_FILTERS = {
    "settlement": {"case_type": "SETTLEMENT"},
    "overdue": {"is_overdue": True},
    "r07x": {"rule_code__in": ATTENTION_RULE_CODES},
    "needs_review": {"needs_review": True},
    "multiple_adjustment_rows": {"multiple_adjustment_rows": True},
    "rrc_already_sent": {"rrc_already_sent_on__isnull": False},
    "no_deadline": {"no_deadline": True},
    "previous_day_r12": {"previous_day_r12": True},
}


class QueuePagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500


def _annotated():
    return ExceptionCase.objects.with_queue_annotations().select_related("assigned_to", "payment_module")


class CaseListView(CaseAPIView):
    def get(self, request):
        p = request.query_params
        qs = _annotated()
        st = p.get("status")
        if not st:
            qs = qs.filter(status__in=ACTIVE_STATUSES)
        elif st != "all":
            qs = qs.filter(status__in=st.split(","))
        for param, lookup in (("business_date", "business_date"), ("priority", "priority"),
                              ("rule_code", "rule_code"), ("rrn", "rrn"), ("case_type", "case_type")):
            if p.get(param):
                qs = qs.filter(**{lookup: p[param]})
        if p.get("overdue") in ("true", "false"):
            qs = qs.filter(is_overdue=True) if p["overdue"] == "true" else qs.exclude(is_overdue=True)
        if p.get("mine") == "assigned":
            qs = qs.filter(assigned_to=request.user)
        elif p.get("mine") == "approval":
            qs = qs.filter(status=CaseStatus.PENDING_APPROVAL) if has_role(request.user, Role.CHECKER) else qs.none()
        if p.get("flag"):
            if p["flag"] not in _FLAG_FILTERS:
                return Response({"detail": f"Unknown flag {p['flag']!r}"}, status=status.HTTP_400_BAD_REQUEST)
            qs = qs.filter(**_FLAG_FILTERS[p["flag"]])
        qs = qs.order_by(*QUEUE_ORDER)
        paginator = QueuePagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(CaseListSerializer(page, many=True).data)


class CaseDetailView(CaseAPIView):
    def get(self, request, pk):
        case = get_object_or_404(_annotated(), pk=pk)
        return Response(CaseDetailSerializer(case, context={"request": request}).data)


def _error_response(exc: services.WorkflowError) -> Response:
    if isinstance(exc, services.RoleRequired):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, services.InvalidTransition):
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_400_BAD_REQUEST
    return Response({"detail": str(exc)}, status=code)


class CaseTransitionView(CaseAPIView):
    def post(self, request, pk, name):
        if name not in TRANSITIONS:
            return Response({"detail": f"Unknown transition {name!r}"}, status=status.HTTP_404_NOT_FOUND)
        case = get_object_or_404(ExceptionCase.objects.select_related("payment_module"), pk=pk)
        t = TRANSITIONS[name]
        form = t.form(data=request.data, case=case, user=request.user)
        if not form.is_valid():
            return Response({"detail": "Invalid input", "errors": form.errors}, status=status.HTTP_400_BAD_REQUEST)
        try:
            t.run(case, request.user, form.cleaned_data, "api")
        except services.WorkflowError as exc:
            return _error_response(exc)
        case = _annotated().get(pk=pk)
        return Response(CaseDetailSerializer(case, context={"request": request}).data)


class BulkView(CaseAPIView):
    def post(self, request, name):
        ids = request.data.get("case_ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
            return Response({"detail": "case_ids must be a non-empty list of integers"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            if name == "take":
                done = services.bulk_take(ids, request.user, source="api bulk")
            elif name == "submit":
                form = BulkSubmitForm(data=request.data)
                if not form.is_valid():
                    return Response({"detail": "Invalid input", "errors": form.errors}, status=400)
                d = form.cleaned_data
                done = services.bulk_submit(ids, request.user, action_type=d["action_type"] or None,
                                            response_code=d["response_code"], remarks=d["remarks"], source="api bulk")
            elif name == "approve":
                form = BulkApproveForm(data=request.data)
                if not form.is_valid():
                    return Response({"detail": "Invalid input", "errors": form.errors}, status=400)
                done = services.bulk_approve(ids, request.user, remarks=form.cleaned_data["remarks"], source="api bulk")
            else:
                return Response({"detail": f"Unknown bulk action {name!r}"}, status=status.HTTP_404_NOT_FOUND)
        except services.ExceptionCase.DoesNotExist:
            return Response({"detail": "Some cases do not exist; nothing was applied"}, status=400)
        except services.WorkflowError as exc:
            return _error_response(exc)
        return Response({"applied": len(done), "case_ids": sorted(ids)})
