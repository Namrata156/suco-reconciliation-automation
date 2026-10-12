"""
Django admin screens: the interim UI until Phase 5 (design D1, sections 3
and 4).

  Case queue      ExceptionCase changelist: queue order (HIGH, overdue, due
                  date, age), flags, filters; bulk take / submit / approve.
  Case detail     read-only page: rule result (reason, fact snapshot), every
                  dispute row for the RRN, warnings, proposed actions and
                  events; buttons for the transitions the user may do now.
  Code changes    response-code change requests: propose (Maker), approve /
                  reject (Checker); log read-only.
  Events/actions  read-only.

Nothing here writes a model: every change goes through
exception_management.services / code_changes, which check the roles (the
buttons shown are only a convenience -- the service decides). Read access
is the model view permission each role group holds; an Auditor sees
everything and can change nothing. Admin access itself needs is_staff.
"""
from __future__ import annotations

import json

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join

from exception_management import code_changes, services
from exception_management.detail import case_flags, case_warnings, overlay_rows, settlement_detail
from exception_management.forms import (
    TRANSITIONS,
    BulkApproveForm,
    BulkSubmitForm,
    CodeApproveForm,
    CodeRejectForm,
    ProposeCodeChangeForm,
    ProposeNewCodeForm,
)
from exception_management.models import (
    ACTIVE_STATUSES,
    ATTENTION_RULE_CODES,
    QUEUE_ORDER,
    CaseEvent,
    CaseStatus,
    ChangeRequestStatus,
    ExceptionCase,
    ProposedAction,
    ResponseCodeChangeEvent,
    ResponseCodeChangeRequest,
)
from exception_management.roles import Role, has_role

_FLAG_LABELS = {
    "settlement": "SETTLEMENT",
    "overdue": "OVERDUE",
    "r07x": "R07x >35d",
    "needs_review": "NEEDS REVIEW",
    "multiple_adjustment_rows": "MULTI ADJ",
    "rrc_already_sent": "RRC SENT",
    "no_deadline": "NO DEADLINE",
    "previous_day_r12": "R12 ON D-1",
}


class ReadOnlyMixin:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# filters
# ---------------------------------------------------------------------------
class QueueFilter(admin.SimpleListFilter):
    title = "queue"
    parameter_name = "queue"

    def lookups(self, request, model_admin):
        return [("all", "All cases"), ("finished", "Completed / closed")] + list(
            (s, label) for s, label in CaseStatus.choices
        )

    def choices(self, changelist):
        yield {
            "selected": self.value() is None,
            "query_string": changelist.get_query_string(remove=[self.parameter_name]),
            "display": "Active (default)",
        }
        for lookup, title in self.lookup_choices:
            yield {
                "selected": self.value() == str(lookup),
                "query_string": changelist.get_query_string({self.parameter_name: lookup}),
                "display": title,
            }

    def queryset(self, request, queryset):
        v = self.value()
        if v is None:
            return queryset.filter(status__in=ACTIVE_STATUSES)
        if v == "all":
            return queryset
        if v == "finished":
            return queryset.exclude(status__in=ACTIVE_STATUSES)
        return queryset.filter(status=v)


class OverdueFilter(admin.SimpleListFilter):
    title = "overdue"
    parameter_name = "overdue"

    def lookups(self, request, model_admin):
        return [("yes", "Overdue"), ("no", "Not overdue")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(is_overdue=True)
        if self.value() == "no":
            return queryset.exclude(is_overdue=True)
        return queryset


class MyWorkFilter(admin.SimpleListFilter):
    title = "my work"
    parameter_name = "mine"

    def lookups(self, request, model_admin):
        return [("assigned", "Assigned to me"), ("approval", "Awaiting my approval")]

    def queryset(self, request, queryset):
        if self.value() == "assigned":
            return queryset.filter(assigned_to=request.user)
        if self.value() == "approval":
            # Self-approval is allowed (owner D3), so every pending case is
            # awaiting any checker -- including the checker who submitted it.
            if not has_role(request.user, Role.CHECKER):
                return queryset.none()
            return queryset.filter(status=CaseStatus.PENDING_APPROVAL)
        return queryset


class FlagFilter(admin.SimpleListFilter):
    title = "flag"
    parameter_name = "flag"

    def lookups(self, request, model_admin):
        return list(_FLAG_LABELS.items())

    def queryset(self, request, queryset):
        v = self.value()
        filters = {
            "settlement": {"case_type": "SETTLEMENT"},
            "overdue": {"is_overdue": True},
            "r07x": {"rule_code__in": ATTENTION_RULE_CODES},
            "needs_review": {"needs_review": True},
            "multiple_adjustment_rows": {"multiple_adjustment_rows": True},
            "rrc_already_sent": {"rrc_already_sent_on__isnull": False},
            "no_deadline": {"no_deadline": True},
            "previous_day_r12": {"previous_day_r12": True},
        }
        return queryset.filter(**filters[v]) if v in filters else queryset


# ---------------------------------------------------------------------------
# case queue + detail
# ---------------------------------------------------------------------------
class ProposedActionInline(ReadOnlyMixin, admin.TabularInline):
    model = ProposedAction
    extra = 0
    fields = ("submitted_at", "submitted_by", "rule_code", "action_type", "response_code", "remarks",
              "evidence_summary", "decision", "decided_by", "decided_at", "decision_remarks", "self_approved",
              "external_reference", "completed_at")
    readonly_fields = fields
    ordering = ("submitted_at",)

    @admin.display(description="Evidence")
    def evidence_summary(self, obj):
        parts = []
        if obj.evidence_cbs_reversal_date:
            parts.append(f"CBS reversal {obj.evidence_cbs_reversal_date} Rs {obj.evidence_cbs_reversal_amount} "
                         f"RRN {obj.evidence_cbs_reversal_rrn}")
        if obj.evidence_notes:
            parts.append(obj.evidence_notes)
        return "; ".join(parts)


class CaseEventInline(ReadOnlyMixin, admin.TabularInline):
    model = CaseEvent
    extra = 0
    fields = ("created_at", "event_type", "actor_username", "source", "old_status", "new_status", "remarks",
              "changes")
    readonly_fields = fields
    ordering = ("created_at", "id")


@admin.register(ExceptionCase)
class ExceptionCaseAdmin(ReadOnlyMixin, admin.ModelAdmin):
    change_form_template = "admin/exception_management/exceptioncase/change_form.html"
    list_display = ("case_ref", "business_date", "priority", "rule_code", "status", "due", "overdue",
                    "ageing_bucket_col", "assigned_to", "flags")
    list_filter = (QueueFilter, "case_type", "business_date", "priority", "rule_code", OverdueFilter, MyWorkFilter,
                   FlagFilter)
    search_fields = ("rrn",)
    list_per_page = 100
    list_select_related = ("assigned_to",)
    actions = ["bulk_take_action", "bulk_submit_action", "bulk_approve_action"]
    inlines = [ProposedActionInline, CaseEventInline]
    fieldsets = (
        ("Case", {"fields": ("payment_module", "business_date", "case_type", "rrn", "status", "assigned_to",
                             "warnings")}),
        ("Settlement (case type SETTLEMENT only)", {"fields": ("settlement_table",)}),
        ("Rule result", {"fields": ("rule_code", "status_category", "priority", "due_date", "due_cutoff_time",
                                    "age_and_bucket", "rule_result_detail", "default_action_type",
                                    "default_response_code")}),
        ("Dispute rows for this RRN", {"fields": ("overlay_rows_table",)}),
        ("Fact snapshot", {"classes": ("collapse",), "fields": ("facts_snapshot",)}),
        ("Flags", {"fields": ("needs_review", "needs_review_reason", "multiple_adjustment_rows",
                              "rrc_already_sent_on", "no_deadline", "result_missing", "created_at", "updated_at")}),
    )
    readonly_fields = ("payment_module", "business_date", "case_type", "settlement_table", "rrn", "status", "assigned_to", "warnings", "rule_code",
                       "status_category", "priority", "due_date", "due_cutoff_time", "age_and_bucket",
                       "rule_result_detail", "default_action_type", "default_response_code", "overlay_rows_table",
                       "facts_snapshot", "needs_review", "needs_review_reason", "multiple_adjustment_rows",
                       "rrc_already_sent_on", "no_deadline", "result_missing", "created_at", "updated_at")

    def get_queryset(self, request):
        # Annotate before ordering: the queue order uses the annotations.
        qs = self.model._default_manager.get_queryset().with_queue_annotations()
        return qs.order_by(*self.get_ordering(request))

    def get_ordering(self, request):
        return list(QUEUE_ORDER)

    # -- list columns ------------------------------------------------------------
    @admin.display(description="RRN / case", ordering="rrn")
    def case_ref(self, obj):
        return obj.label

    @admin.display(description="Due", ordering="due_date")
    def due(self, obj):
        if obj.due_date is None:
            return "no deadline" if obj.no_deadline else "-"
        return f"{obj.due_date:%Y-%m-%d}" + (f" {obj.due_cutoff_time:%H:%M}" if obj.due_cutoff_time else "")

    @admin.display(description="Overdue", boolean=True, ordering="is_overdue")
    def overdue(self, obj):
        return bool(obj.is_overdue)

    @admin.display(description="Age", ordering="age_days")
    def ageing_bucket_col(self, obj):
        return obj.ageing_bucket

    @admin.display(description="Flags")
    def flags(self, obj):
        on = [label for key, label in _FLAG_LABELS.items() if case_flags(obj)[key] and key != "overdue"]
        return " | ".join(on)

    # -- detail fields -------------------------------------------------------------
    @admin.display(description="Warnings")
    def warnings(self, obj):
        w = case_warnings(obj)
        if not w:
            return "-"
        return format_html("<ul style='margin:0;color:#ba2121'>{}</ul>", format_html_join("", "<li>{}</li>", ((x,) for x in w)))

    @admin.display(description="Age / bucket")
    def age_and_bucket(self, obj):
        if obj.rule_result_id is None:
            return "-"
        return f"{obj.age_days} days, {obj.ageing_bucket}"

    @admin.display(description="Settlement result")
    def settlement_table(self, obj):
        d = settlement_detail(obj)
        if d is None:
            return "-"
        url = reverse("admin:settlement_settlementresult_change", args=[d["result_id"]])
        head = ("Cycle", "Side", "NTSL Rs", "NTSL txn", "Matched Rs", "Matched txn", "Explained Rs", "Explained txn",
                "Unexplained Rs", "Unexplained txn", "Within tolerance")
        return format_html(
            "<p><a href='{}'>Result {}</a> ({}{}), computed {}. Tolerance Rs {} / {} txn. SOP check: difference "
            "Rs {}, explained Rs {}, unexplained Rs {}.</p><ul>{}</ul>"
            "<table><thead><tr>{}</tr></thead><tbody>{}</tbody></table>",
            url, d["result_id"], d["status"], "" if d["result_is_current"] else ", superseded",
            f"{d['computed_at']:%Y-%m-%d %H:%M}", d["tolerance"]["amount"], d["tolerance"]["txn_count"],
            d["sop"]["difference"], d["sop"]["explained"], d["sop"]["unexplained"],
            format_html_join("", "<li>{}</li>", ((x,) for x in d["reasons"])),
            format_html_join("", "<th>{}</th>", ((h,) for h in head)),
            format_html_join("", "<tr>" + "<td>{}</td>" * 11 + "</tr>", (
                (c["cycle"] or "-", c["side"], c["ntsl_amount"], "-" if c["ntsl_count"] is None else c["ntsl_count"],
                 c["matched_amount"], c["matched_count"], c["explained_amount"], c["explained_count"],
                 c["unexplained_amount"], c["unexplained_count"], "yes" if c["within_tolerance"] else "NO")
                for c in d["cycles"])),
        )

    @admin.display(description="Rule result")
    def rule_result_detail(self, obj):
        r = obj.rule_result
        if r is None:
            return "- (settlement case: no rule result)"
        return format_html(
            "<b>{}</b> (rule set {}, evaluated {})<br>Action: {}<br>Reason: {}<br>Deadline: {}",
            r.rule_code, r.rule_set.version_label, f"{r.evaluated_at:%Y-%m-%d %H:%M}", r.action, r.reason or "-",
            r.tat_label or "-",
        )

    @admin.display(description="Facts the rules saw")
    def facts_snapshot(self, obj):
        if obj.rule_result_id is None:
            return "-"
        return format_html("<pre>{}</pre>", json.dumps(obj.rule_result.facts, indent=2, sort_keys=True, default=str))

    @admin.display(description="Rows")
    def overlay_rows_table(self, obj):
        rows = overlay_rows(obj)
        if not rows:
            return "No DRC / Timeout / Adjustment rows for this RRN"
        head = ("Leg", "File date", "Amount", "Type", "SSK side", "Resp.", "Txndate", "On this case's date")
        return format_html(
            "<table><thead><tr>{}</tr></thead><tbody>{}</tbody></table>",
            format_html_join("", "<th>{}</th>", ((h,) for h in head)),
            format_html_join("", "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
                                 "<td>{}</td></tr>",
                             ((r["leg"], r["file_date"], r["amount"], r["dispute_type"], r["ssk_side"],
                               r["response_code"], r["txn_date"], "yes" if r["belongs_to_case_date"] else "no")
                              for r in rows)),
        )

    # -- transitions -----------------------------------------------------------------
    def get_urls(self):
        return [
            path("<int:object_id>/do/<str:name>/", self.admin_site.admin_view(self.transition_view),
                 name="exception_management_exceptioncase_transition"),
        ] + super().get_urls()

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        case = self.get_object(request, object_id)
        if case is not None:
            extra_context["transitions"] = [
                (name, TRANSITIONS[name].label,
                 reverse("admin:exception_management_exceptioncase_transition", args=[case.pk, name]))
                for name in services.available_transitions(case, request.user)
            ]
        return super().change_view(request, object_id, form_url, extra_context)

    def transition_view(self, request, object_id, name):
        if name not in TRANSITIONS:
            raise PermissionDenied
        case = get_object_or_404(self.get_queryset(request), pk=object_id)
        if not self.has_view_permission(request, case):
            raise PermissionDenied
        change_url = reverse("admin:exception_management_exceptioncase_change", args=[case.pk])
        if name not in services.available_transitions(case, request.user):
            messages.error(request, f"'{TRANSITIONS[name].label}' is not available to you for this case now.")
            return redirect(change_url)
        t = TRANSITIONS[name]
        form = t.form(request.POST if request.method == "POST" else None, case=case, user=request.user)
        if request.method == "POST" and form.is_valid():
            try:
                t.run(case, request.user, form.cleaned_data, "admin")
            except services.WorkflowError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f"{t.label}: done for {case.label} ({case.business_date}).")
                return redirect(change_url)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "case": case,
            "warnings": case_warnings(case),
            "form": form,
            "title": f"{t.label}: {case.label} ({case.business_date}{', ' + case.rule_code if case.rule_code else ''})",
            "change_url": change_url,
        }
        return TemplateResponse(request, "admin/exception_management/transition_form.html", context)

    # -- bulk actions (P2) ------------------------------------------------------------------
    def get_actions(self, request):
        actions = super().get_actions(request)
        if not has_role(request.user, Role.MAKER):
            actions.pop("bulk_take_action", None)
            actions.pop("bulk_submit_action", None)
        if not has_role(request.user, Role.CHECKER):
            actions.pop("bulk_approve_action", None)
        return actions

    def _bulk(self, request, queryset, *, title, form_class, run, explanation):
        form = form_class(request.POST if "apply" in request.POST else None)
        if "apply" in request.POST and form.is_valid():
            try:
                with transaction.atomic():
                    done = run(list(queryset.values_list("pk", flat=True)), form.cleaned_data)
            except services.WorkflowError as exc:
                messages.error(request, f"{title}: refused, nothing was applied. {exc}")
                return None
            messages.success(request, f"{title}: applied to {len(done)} case(s).")
            return None
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": title,
            "explanation": explanation,
            "form": form,
            "cases": queryset,
            "action": request.POST.get("action"),
        }
        return TemplateResponse(request, "admin/exception_management/bulk_form.html", context)

    @admin.action(description="Take selected cases")
    def bulk_take_action(self, request, queryset):
        return self._bulk(
            request, queryset, title="Take selected cases", form_class=BulkApproveForm,
            explanation="Assigns every selected OPEN case to you. All or nothing.",
            run=lambda ids, d: services.bulk_take(ids, request.user, source="admin bulk"),
        )

    @admin.action(description="Submit the same action for selected cases (LOW/MEDIUM only)")
    def bulk_submit_action(self, request, queryset):
        return self._bulk(
            request, queryset, title="Bulk submit", form_class=BulkSubmitForm,
            explanation="LOW/MEDIUM cases only, all taken by you, one shared action; cases with a warning must be "
                        "handled one by one. All or nothing: if any case is refused, nothing is applied.",
            run=lambda ids, d: services.bulk_submit(ids, request.user, action_type=d.get("action_type") or None,
                                                    response_code=d.get("response_code"), remarks=d.get("remarks", ""),
                                                    source="admin bulk"),
        )

    @admin.action(description="Approve selected cases (LOW/MEDIUM, same action only)")
    def bulk_approve_action(self, request, queryset):
        return self._bulk(
            request, queryset, title="Bulk approve", form_class=BulkApproveForm,
            explanation="LOW/MEDIUM cases pending approval that share one proposed action. All or nothing.",
            run=lambda ids, d: services.bulk_approve(ids, request.user, remarks=d.get("remarks", ""),
                                                     source="admin bulk"),
        )


@admin.register(ProposedAction)
class ProposedActionAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ("case", "action_type", "response_code", "decision", "submitted_by", "submitted_at", "decided_by",
                    "self_approved", "external_reference")
    list_filter = ("decision", "action_type", "self_approved")
    search_fields = ("case__rrn",)
    list_select_related = ("case", "response_code", "submitted_by", "decided_by")


@admin.register(CaseEvent)
class CaseEventAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ("created_at", "business_date", "rrn", "event_type", "actor_username", "old_status", "new_status",
                    "source")
    list_filter = ("event_type", "business_date")
    search_fields = ("rrn", "actor_username")
    date_hierarchy = "created_at"


# ---------------------------------------------------------------------------
# response-code change requests (P3)
# ---------------------------------------------------------------------------
class CodeChangeEventInline(ReadOnlyMixin, admin.TabularInline):
    model = ResponseCodeChangeEvent
    extra = 0
    fields = ("created_at", "event_type", "actor_username", "changes", "remarks", "self_approved")
    readonly_fields = fields


@admin.register(ResponseCodeChangeRequest)
class ResponseCodeChangeRequestAdmin(ReadOnlyMixin, admin.ModelAdmin):
    change_list_template = "admin/exception_management/responsecodechangerequest/change_list.html"
    change_form_template = "admin/exception_management/responsecodechangerequest/change_form.html"
    list_display = ("kind", "flag", "response_code", "old_code", "new_code", "old_description", "new_description", "status",
                    "requested_by", "requested_at", "decided_by", "self_approved")
    list_filter = ("status", "kind")
    inlines = [CodeChangeEventInline]
    readonly_fields = ("kind", "payment_module", "flag", "response_code", "new_source", "old_code", "new_code", "old_description", "new_description", "old_notes",
                       "new_notes", "reason", "status", "requested_by", "requested_at", "decided_by", "decided_at",
                       "decision_remarks", "self_approved")

    def get_urls(self):
        wrap = self.admin_site.admin_view
        return [
            path("propose/", wrap(self.propose_view), name="exception_management_codechange_propose"),
            path("propose-new/", wrap(self.propose_new_view), name="exception_management_codechange_propose_new"),
            path("<int:object_id>/decide/<str:decision>/", wrap(self.decide_view),
                 name="exception_management_codechange_decide"),
        ] + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        extra_context = {**(extra_context or {}), "can_propose": has_role(request.user, Role.MAKER)}
        return super().changelist_view(request, extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        obj = self.get_object(request, object_id)
        can = obj is not None and obj.status == ChangeRequestStatus.PENDING and has_role(request.user, Role.CHECKER)
        extra_context = {**(extra_context or {}), "can_decide": can}
        return super().change_view(request, object_id, form_url, extra_context)

    def _render(self, request, title, form, back):
        return TemplateResponse(request, "admin/exception_management/transition_form.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta, "form": form, "title": title,
            "change_url": back, "warnings": [],
        })

    def propose_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        back = reverse("admin:exception_management_responsecodechangerequest_changelist")
        if not has_role(request.user, Role.MAKER):
            messages.error(request, "Proposing a code change needs the Maker role.")
            return redirect(back)
        form = ProposeCodeChangeForm(request.POST if request.method == "POST" else None,
                                     initial={"response_code": request.GET.get("code")})
        if request.method == "POST" and form.is_valid():
            try:
                req = code_changes.propose_code_change(form.cleaned_data["response_code"], request.user,
                                                       **form.proposal())
            except services.WorkflowError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f"Change request {req.pk} proposed; a checker must approve it.")
                return redirect(reverse("admin:exception_management_responsecodechangerequest_change", args=[req.pk]))
        return self._render(request, "Propose a response-code change", form, back)

    def propose_new_view(self, request):
        """Amendment 2 B2: a maker proposes a NEW response code."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        back = reverse("admin:exception_management_responsecodechangerequest_changelist")
        if not has_role(request.user, Role.MAKER):
            messages.error(request, "Proposing a new code needs the Maker role.")
            return redirect(back)
        form = ProposeNewCodeForm(request.POST if request.method == "POST" else None)
        if request.method == "POST" and form.is_valid():
            d = form.cleaned_data
            try:
                req = code_changes.propose_code_creation(
                    d["payment_module"], request.user, flag=d["flag"], new_code=d["new_code"],
                    new_description=d["new_description"], new_source=d["new_source"], new_notes=d["new_notes"],
                    reason=d["reason"],
                )
            except services.WorkflowError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f"Request {req.pk} to create a new code proposed; a checker must approve it.")
                return redirect(reverse("admin:exception_management_responsecodechangerequest_change", args=[req.pk]))
        return self._render(request, "Propose a new response code", form, back)

    def decide_view(self, request, object_id, decision):
        req = get_object_or_404(ResponseCodeChangeRequest, pk=object_id)
        if not self.has_view_permission(request, req) or decision not in ("approve", "reject"):
            raise PermissionDenied
        back = reverse("admin:exception_management_responsecodechangerequest_change", args=[req.pk])
        if not has_role(request.user, Role.CHECKER):
            messages.error(request, "Deciding a code change needs the Checker role.")
            return redirect(back)
        form_class = CodeApproveForm if decision == "approve" else CodeRejectForm
        form = form_class(request.POST if request.method == "POST" else None)
        if request.method == "POST" and form.is_valid():
            try:
                if decision == "approve":
                    code_changes.approve_code_change(req, request.user, remarks=form.cleaned_data["remarks"])
                else:
                    code_changes.reject_code_change(req, request.user, reason=form.cleaned_data["reason"])
            except services.WorkflowError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f"Change request {req.pk}: {decision}d.")
                return redirect(back)
        return self._render(request, f"{decision.title()} change request {req.pk}: {req}", form, back)


@admin.register(ResponseCodeChangeEvent)
class ResponseCodeChangeEventAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ("created_at", "change_request", "response_code", "event_type", "actor_username", "self_approved")
    list_filter = ("event_type",)
