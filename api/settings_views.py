"""
/api/v1/<module>/settings/ (Phase 5 Slice 3; design 4.16). Thin views over
the existing maker-checker services, which check the role, the status and
the self-approval flag and write the append-only event log:

  response codes  exception_management.code_changes   ResponseCodeChangeEvent
  NTSL mapping    settlement.mapping_changes          NtslMappingEvent
  tolerance       settlement.tolerance                SettlementToleranceEvent

  GET  /settings/response-codes/                     the module's code table
  GET  /settings/response-codes/requests/            change requests (history, pending)
  GET  /settings/response-codes/requests/<id>/       one request + its events (Slice 4)
  POST /settings/response-codes/requests/            propose an EDIT or a CREATE (Maker)
  POST /settings/response-codes/requests/<id>/approve/  Checker; own proposal needs confirm_self_approval
  POST /settings/response-codes/requests/<id>/reject/   Checker; reason required
  GET  /settings/ntsl-mapping/                       active + pending version
  POST /settings/ntsl-mapping/                       propose entries to add / remove (Maker)
  GET  /settings/ntsl-mapping/versions/              every version (history)
  GET  /settings/ntsl-mapping/versions/<id>/         one version: change summary, events
  GET  /settings/ntsl-mapping/versions/<id>/entries/ its entries (paginated)
  POST /settings/ntsl-mapping/<id>/approve/          Checker; own proposal needs confirm_self_approval
  POST /settings/ntsl-mapping/<id>/reject/           Checker; reason required
  GET  /settings/tolerance/                          active + pending + dates computed with an older one
  POST /settings/tolerance/                          propose (Administrator)
  GET  /settings/tolerance/versions/                 every version (history)
  GET  /settings/tolerance/versions/<id>/            one version + events
  POST /settings/tolerance/<id>/approve/             Checker; own proposal needs confirm_self_approval
  POST /settings/tolerance/<id>/reject/              Checker; reason required
  GET  /settings/pending/                            every settings proposal awaiting a checker

Reads: all five roles. Writes: the services check the role (403), the status
(409) and the input (400); the views here only check the shape of the body
and the self-approval confirmation (owner 2026-10-20, Slice 3 Q1/Q2).

No settlement re-run after a tolerance approval (owner 2026-10-20, Q4): the
approve response and the tolerance read list the business dates (not
archived) whose current SettlementResult used another tolerance version; the
user runs each date again from its overview.
"""
from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response

from api.base import ModuleMixin, V1APIView
from api.operations import _bad_request
from api.results import _enum_filter
from api.views import _confirmed
from exception_management import api as legacy
from exception_management import code_changes, services
from exception_management.models import (
    CaseStatus,
    ChangeRequestKind,
    ChangeRequestStatus,
    ExceptionCase,
    ResponseCodeChangeEvent,
    ResponseCodeChangeRequest,
)
from exception_management.roles import Role, has_role
from rule_engine.models import CodeFlag, ResponseCode, RuleSet, RuleSetStatus
from settlement import tolerance as tolerance_service
from settlement.categories import NtslCategory
from file_processing.models import UploadedFile
from settlement import mapping_changes
from settlement.models import (
    MapStatus,
    MatchType,
    NtslCategoryMapEntry,
    NtslCategoryMapVersion,
    SettlementResult,
    SettlementTolerance,
)
from transactions.models import HISTORY_MODEL_BY_STAGING_MODEL

SELF_APPROVAL_MESSAGE = "You proposed this change; approving it will be recorded as self-approved."

SETTINGS_KINDS = {
    "response_code": "Response code change",
    "ntsl_mapping": "NTSL category mapping change",
    "tolerance": "Settlement tolerance change",
}


def _m(v) -> str | None:
    return None if v is None else f"{v:.2f}"


def _user(u) -> str | None:
    return u.get_username() if u is not None else None


class SettingsView(ModuleMixin, V1APIView):
    pass


def _paginate(view, request, items):
    paginator = legacy.QueuePagination()
    page = paginator.paginate_queryset(items, request, view=view)
    return paginator, page


def _not_text(data, *names) -> Response | None:
    """400 when a free-text field is sent as something other than a string
    (the services strip text)."""
    errors = {n: ["Must be text."] for n in names if data.get(n) is not None and not isinstance(data.get(n), str)}
    return _bad_request("Invalid input", errors) if errors else None


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _workflow_error(exc: services.WorkflowError) -> Response:
    return legacy._error_response(exc)


class DecisionView(SettingsView):
    """POST .../<id>/approve/ {"remarks", "confirm_self_approval"} or
    .../<id>/reject/ {"reason"}. The service checks the Checker role (403)
    and that the item is still pending (409). Approving your own proposal
    (you hold both roles) needs confirm_self_approval: true, as for cases
    (Phase 3 P4) and the tolerance; it is then recorded self_approved."""

    def target(self, pk):  # the module's item or 404
        raise NotImplementedError

    def proposer_id(self, obj) -> int | None:
        raise NotImplementedError

    def is_pending(self, obj) -> bool:
        raise NotImplementedError

    def approve(self, obj, user, remarks: str):
        raise NotImplementedError

    def reject(self, obj, user, reason):
        raise NotImplementedError

    def body(self, obj, user, decision: str) -> dict:
        raise NotImplementedError

    def post(self, request, module, pk, decision):
        if decision not in ("approve", "reject"):
            return Response({"detail": f"Unknown decision {decision!r}"}, status=status.HTTP_404_NOT_FOUND)
        obj = self.target(pk)
        bad = _not_text(request.data, "remarks", "reason")
        if bad:
            return bad
        try:
            if decision == "approve":
                own = (has_role(request.user, Role.CHECKER) and self.is_pending(obj)
                       and self.proposer_id(obj) == request.user.pk)
                if own and not _confirmed(request.data.get("confirm_self_approval")):
                    return _bad_request(SELF_APPROVAL_MESSAGE,
                                        {"confirm_self_approval": ["Confirm the self-approval to continue."]})
                obj = self.approve(obj, request.user, request.data.get("remarks") or "")
            else:
                obj = self.reject(obj, request.user, request.data.get("reason"))
        except services.WorkflowError as exc:
            return _workflow_error(exc)
        return Response(self.body(self.target(obj.pk), request.user, decision))


# ---------------------------------------------------------------------------
# response codes (read)
# ---------------------------------------------------------------------------
def code_request_row(req: ResponseCodeChangeRequest, user=None) -> dict:
    return {
        "id": req.pk, "kind": req.kind, "flag": req.flag, "response_code_id": req.response_code_id,
        # Slice 4: the code as shown elsewhere (meta response_codes[].label); None for a CREATE
        # not yet approved.
        "response_code_label": str(req.response_code) if req.response_code_id else None,
        "changes": req.changes(), "new_source": req.new_source, "reason": req.reason, "status": req.status,
        "requested_by": _user(req.requested_by), "requested_at": req.requested_at,
        "decided_by": _user(req.decided_by), "decided_at": req.decided_at, "decision_remarks": req.decision_remarks,
        "self_approved": req.self_approved,
        "self_approval": bool(user is not None and req.status == ChangeRequestStatus.PENDING
                              and req.requested_by_id == user.pk),
    }


class ResponseCodeListView(SettingsView):
    """?flag=DRC,RET  ?known=true|false. Each code: whether the active rule
    set uses it (locked: only an unknown numeric code may be filled in) and
    any pending change request."""

    def get(self, request, module):
        p, errors = request.query_params, {}
        qs = ResponseCode.objects.filter(payment_module=self.module).order_by("flag", "code", "description", "pk")
        flags = _enum_filter(p, "flag", CodeFlag.values, errors)
        if flags:
            qs = qs.filter(flag__in=flags)
        if p.get("known") in ("true", "false"):
            qs = qs.filter(code__isnull=p["known"] == "false")
        elif p.get("known"):
            errors["known"] = ["Allowed: true, false"]
        if errors:
            return _bad_request("Invalid input", errors)
        paginator, page = _paginate(self, request, qs)
        locked = set(ResponseCode.objects.filter(pk__in=[c.pk for c in page]).locked().values_list("pk", flat=True))
        pending = dict(ResponseCodeChangeRequest.objects.filter(
            response_code__in=page, status=ChangeRequestStatus.PENDING).values_list("response_code_id", "pk"))
        rule_set = (RuleSet.objects.filter(payment_module=self.module, status=RuleSetStatus.ACTIVE)
                    .order_by("-activated_at", "-pk").first())
        rows = [{"id": c.pk, "flag": c.flag, "code": c.code, "code_known": c.code_known,
                 "description": c.description, "source": c.source, "notes": c.notes, "label": str(c),
                 "locked": c.pk in locked, "used_by": c.used_by(rule_set) if rule_set else [],
                 "pending_request_id": pending.get(c.pk), "created_at": c.created_at} for c in page]
        response = paginator.get_paginated_response(rows)
        response.data["rule_set"] = rule_set.version_label if rule_set else None
        response.data["can_propose"] = has_role(request.user, Role.MAKER)
        response.data["can_decide"] = has_role(request.user, Role.CHECKER)
        return response


class ResponseCodeRequestListView(SettingsView):
    """?status=PENDING,APPROVED  ?kind=EDIT|CREATE  ?response_code=<id>; newest first."""

    def get(self, request, module):
        p, errors = request.query_params, {}
        qs = _code_requests(self.module).order_by("-requested_at", "-pk")
        statuses = _enum_filter(p, "status", ChangeRequestStatus.values, errors)
        if statuses:
            qs = qs.filter(status__in=statuses)
        kinds = _enum_filter(p, "kind", ChangeRequestKind.values, errors)
        if kinds:
            qs = qs.filter(kind__in=kinds)
        if p.get("response_code"):
            if not p["response_code"].isdigit():
                errors["response_code"] = ["An id."]
            else:
                qs = qs.filter(response_code_id=int(p["response_code"]))
        if errors:
            return _bad_request("Invalid input", errors)
        paginator, page = _paginate(self, request, qs)
        events = _code_events(page)
        response = paginator.get_paginated_response(
            [{**code_request_row(r, request.user), "events": events.get(r.pk, [])} for r in page])
        response.data["can_propose"] = has_role(request.user, Role.MAKER)
        response.data["can_decide"] = has_role(request.user, Role.CHECKER)
        return response

    def post(self, request, module):
        """Maker. {"kind": "EDIT", "response_code": <id>, "new_code", "new_description",
        "new_notes", "reason"}: fields left out keep their value. {"kind": "CREATE",
        "flag", "new_description", "new_source", "new_code", "new_notes", "reason"}."""
        data, errors = request.data, {}
        bad = _not_text(data, "kind", "flag", "new_code", "new_description", "new_notes", "new_source", "reason")
        if bad:
            return bad
        kind = data.get("kind")
        if kind not in ChangeRequestKind.values:
            errors["kind"] = [f"Allowed: {', '.join(ChangeRequestKind.values)}"]
        elif kind == ChangeRequestKind.EDIT:
            for name in ("flag", "new_source"):
                if data.get(name) is not None:
                    errors[name] = ["Not used when editing a code."]
            rc_id = data.get("response_code")
            if not _is_id(rc_id):
                errors["response_code"] = ["The id of the code to edit."]
            elif not ResponseCode.objects.filter(payment_module=self.module, pk=rc_id).exists():
                errors["response_code"] = ["No such response code in this module."]
        elif data.get("response_code") is not None:
            errors["response_code"] = ["Not used when creating a code."]
        if errors:
            return _bad_request("Invalid input", errors)
        try:
            if kind == ChangeRequestKind.EDIT:
                req = code_changes.propose_code_change(
                    data["response_code"], request.user, new_code=data.get("new_code"),
                    new_description=data.get("new_description"), new_notes=data.get("new_notes"),
                    reason=data.get("reason"))
            else:
                req = code_changes.propose_code_creation(
                    self.module, request.user, flag=data.get("flag"), new_description=data.get("new_description"),
                    new_source=data.get("new_source"), new_code=data.get("new_code"),
                    new_notes=data.get("new_notes") or "", reason=data.get("reason"))
        except services.WorkflowError as exc:
            return _workflow_error(exc)
        return Response(_code_request_detail(_code_requests(self.module).get(pk=req.pk), request.user),
                        status=status.HTTP_201_CREATED)


def _code_requests(module):
    return (ResponseCodeChangeRequest.objects.filter(payment_module=module)
            .select_related("requested_by", "decided_by", "response_code"))


def _code_events(requests) -> dict[int, list]:
    events = {}
    for e in ResponseCodeChangeEvent.objects.filter(change_request__in=requests).order_by("created_at", "id"):
        events.setdefault(e.change_request_id, []).append(
            {"event_type": e.event_type, "actor_username": e.actor_username, "created_at": e.created_at,
             "remarks": e.remarks, "self_approved": e.self_approved})
    return events


def _code_request_detail(req: ResponseCodeChangeRequest, user) -> dict:
    return {**code_request_row(req, user), "events": _code_events([req]).get(req.pk, [])}


class ResponseCodeRequestDetailView(SettingsView):
    """GET .../response-codes/requests/<id>/ -- one request and its events
    (Slice 4 fit item: the pending request's history)."""

    def get(self, request, module, pk):
        return Response(_code_request_detail(get_object_or_404(_code_requests(self.module), pk=pk), request.user))


class ResponseCodeDecisionView(DecisionView):
    """POST .../response-codes/requests/<id>/approve|reject/. Approval applies
    the change (EDIT) or creates the code (CREATE); see code_changes."""

    def target(self, pk):
        return get_object_or_404(_code_requests(self.module), pk=pk)

    def proposer_id(self, obj):
        return obj.requested_by_id

    def is_pending(self, obj):
        return obj.status == ChangeRequestStatus.PENDING

    def approve(self, obj, user, remarks):
        return code_changes.approve_code_change(obj, user, remarks=remarks)

    def reject(self, obj, user, reason):
        return code_changes.reject_code_change(obj, user, reason=reason)

    def body(self, obj, user, decision):
        return _code_request_detail(obj, user)


# ---------------------------------------------------------------------------
# NTSL category mapping (read)
# ---------------------------------------------------------------------------
def map_version_row(v: NtslCategoryMapVersion, user=None, entry_count=None) -> dict:
    summary = v.change_summary or {}
    return {
        "id": v.pk, "version": v.version, "status": v.status, "based_on_version": v.based_on.version if v.based_on else None,
        "reason": v.reason, "proposed_by": v.proposed_by_username or None, "proposed_at": v.proposed_at,
        "decided_by": v.decided_by_username or None, "decided_at": v.decided_at,
        "decision_remarks": v.decision_remarks, "self_approved": v.self_approved,
        "activated_at": v.activated_at, "retired_at": v.retired_at,
        "entry_count": entry_count if entry_count is not None else v.entries.count(),
        "change_counts": {"added": len(summary.get("added", [])), "removed": len(summary.get("removed", [])),
                          "recategorised_loaded_descriptions":
                              len(summary.get("recategorised_loaded_descriptions", []))},
        "self_approval": bool(user is not None and v.status == MapStatus.PENDING and v.proposed_by_id == user.pk),
    }


def _map_versions(module):
    return NtslCategoryMapVersion.objects.filter(payment_module=module).select_related("based_on")


class NtslMappingView(SettingsView):
    def get(self, request, module):
        versions = {v.status: v for v in _map_versions(self.module).filter(
            status__in=(MapStatus.ACTIVE, MapStatus.PENDING))}
        active, pending = versions.get(MapStatus.ACTIVE), versions.get(MapStatus.PENDING)
        return Response({
            "active": map_version_row(active, request.user) if active else None,
            "pending": map_version_row(pending, request.user) if pending else None,
            "categories": [{"value": v, "label": label} for v, label in NtslCategory.choices],
            "can_propose": has_role(request.user, Role.MAKER),
            "can_decide": has_role(request.user, Role.CHECKER),
        })

    def post(self, request, module):
        """Maker. {"add": [{"match_type", "pattern", "category", "txn_type",
        "priority", "notes"}, ...], "remove_entry_ids": [<ids of the ACTIVE
        version's entries>], "reason"}. A new PENDING version = active - removed
        + added; refused if any loaded NTSL description would be unmapped or
        ambiguous (settlement.mapping_changes)."""
        data, errors = request.data, {}
        bad = _not_text(data, "reason")
        if bad:
            return bad
        add, remove = data.get("add", []), data.get("remove_entry_ids", [])
        if not isinstance(add, list) or not all(isinstance(a, dict) for a in add):
            errors["add"] = ["A list of entries (objects)."]
        else:
            for i, entry in enumerate(add):
                problems = [f"unknown field {k!r}" for k in entry if k not in _MAP_ENTRY_FIELDS]
                problems += [f"{k} must be text" for k in ("match_type", "pattern", "category", "txn_type", "notes")
                             if entry.get(k) is not None and not isinstance(entry.get(k), str)]
                if "priority" in entry and not _is_id(entry["priority"]):
                    problems.append("priority must be a whole number")
                if problems:
                    errors[f"add[{i}]"] = problems
        if not isinstance(remove, list) or not all(_is_id(x) for x in remove):
            errors["remove_entry_ids"] = ["A list of entry ids."]
        if errors:
            return _bad_request("Invalid input", errors)
        try:
            v = mapping_changes.propose_mapping_change(self.module, request.user, add=add,
                                                       remove_entry_ids=remove, reason=data.get("reason"))
        except services.WorkflowError as exc:
            return _workflow_error(exc)
        return Response(_map_detail(_map_versions(self.module).get(pk=v.pk), request.user),
                        status=status.HTTP_201_CREATED)


_MAP_ENTRY_FIELDS = ("match_type", "pattern", "category", "txn_type", "priority", "notes")


def _map_detail(v: NtslCategoryMapVersion, user) -> dict:
    body = map_version_row(v, user)
    body["change_summary"] = v.change_summary
    body["events"] = [{"event_type": e.event_type, "actor_username": e.actor_username or None,
                       "created_at": e.created_at, "remarks": e.remarks, "self_approved": e.self_approved}
                      for e in v.events.order_by("created_at", "id")]
    return body


class NtslMappingDecisionView(DecisionView):
    """POST .../ntsl-mapping/<id>/approve|reject/. Approval makes the version
    ACTIVE and retires the old one; existing NTSL checks and settlement
    results keep the version they used."""

    def target(self, pk):
        return get_object_or_404(_map_versions(self.module), pk=pk)

    def proposer_id(self, obj):
        return obj.proposed_by_id

    def is_pending(self, obj):
        return obj.status == MapStatus.PENDING

    def approve(self, obj, user, remarks):
        return mapping_changes.approve_mapping_change(obj, user, remarks=remarks)

    def reject(self, obj, user, reason):
        return mapping_changes.reject_mapping_change(obj, user, reason=reason)

    def body(self, obj, user, decision):
        return _map_detail(obj, user)


class NtslMappingVersionListView(SettingsView):
    def get(self, request, module):
        p, errors = request.query_params, {}
        qs = _map_versions(self.module).order_by("-version")
        statuses = _enum_filter(p, "status", MapStatus.values, errors)
        if statuses:
            qs = qs.filter(status__in=statuses)
        if errors:
            return _bad_request("Invalid input", errors)
        paginator, page = _paginate(self, request, qs)
        return paginator.get_paginated_response([map_version_row(v, request.user) for v in page])


class NtslMappingVersionDetailView(SettingsView):
    def get(self, request, module, pk):
        return Response(_map_detail(get_object_or_404(_map_versions(self.module), pk=pk), request.user))


class NtslMappingEntriesView(SettingsView):
    """?category=TXN_REMITTER,FEE  ?match_type=EXACT|REGEX  ?search=<text in the pattern>."""

    def get(self, request, module, pk):
        v = get_object_or_404(_map_versions(self.module), pk=pk)
        p, errors = request.query_params, {}
        qs = NtslCategoryMapEntry.objects.filter(version=v).order_by("match_type", "priority", "id")
        categories = _enum_filter(p, "category", NtslCategory.values, errors)
        if categories:
            qs = qs.filter(category__in=categories)
        types = _enum_filter(p, "match_type", MatchType.values, errors)
        if types:
            qs = qs.filter(match_type__in=types)
        if p.get("search"):
            qs = qs.filter(pattern__icontains=p["search"].strip())
        if errors:
            return _bad_request("Invalid input", errors)
        paginator, page = _paginate(self, request, qs)
        return paginator.get_paginated_response([{"id": e.pk, **e.as_dict(),
                                                  "category_label": e.get_category_display()} for e in page])


# ---------------------------------------------------------------------------
# tolerance (read, propose, approve, reject)
# ---------------------------------------------------------------------------
def tolerance_row(t: SettlementTolerance, user=None) -> dict:
    return {
        "id": t.pk, "version": t.version, "status": t.status, "amount": _m(t.amount), "txn_count": t.txn_count,
        "based_on_version": t.based_on.version if t.based_on else None, "reason": t.reason,
        "proposed_by": t.proposed_by_username or None, "proposed_at": t.proposed_at,
        "decided_by": t.decided_by_username or None, "decided_at": t.decided_at,
        "decision_remarks": t.decision_remarks, "self_approved": t.self_approved,
        "activated_at": t.activated_at, "retired_at": t.retired_at,
        "self_approval": bool(user is not None and t.status == MapStatus.PENDING and t.proposed_by_id == user.pk),
    }


def _tolerances(module):
    return SettlementTolerance.objects.filter(payment_module=module).select_related("based_on")


def _tolerance_detail(t: SettlementTolerance, user) -> dict:
    body = tolerance_row(t, user)
    body["events"] = [{"event_type": e.event_type, "actor_username": e.actor_username or None,
                       "created_at": e.created_at, "remarks": e.remarks, "changes": e.changes,
                       "self_approved": e.self_approved} for e in t.events.order_by("created_at", "id")]
    return body


def _archived_dates(module, dates) -> set:
    """The dates among `dates` with staging rows moved to history (same test
    as file_processing.intake.date_is_archived, one query per history table
    instead of per date)."""
    files = dict(UploadedFile.objects.filter(payment_module=module, business_date__in=dates).order_by()
                 .values_list("pk", "business_date"))
    out = set()
    for history in HISTORY_MODEL_BY_STAGING_MODEL.values():
        if not files:
            break
        hit = set(history.objects.filter(uploaded_file_id__in=list(files)).order_by()
                  .values_list("uploaded_file_id", flat=True).distinct())
        out |= {files.pop(pk) for pk in hit if pk in files}
        files = {pk: d for pk, d in files.items() if d not in out}
    return out


def stale_tolerance_dates(module) -> list[dict]:
    """Q4 (owner 2026-10-20): the business dates, not archived, whose CURRENT
    SettlementResult used a tolerance version other than the active one
    (SettlementResult.tolerance; a result with none recorded is listed too),
    newest first. Nothing is re-run: the user runs each date from its
    overview."""
    active = SettlementTolerance.objects.filter(payment_module=module, status=MapStatus.ACTIVE).first()
    if active is None:
        return []
    results = list(SettlementResult.objects.filter(payment_module=module, is_current=True)
                   .exclude(tolerance=active).select_related("tolerance").order_by("-business_date", "-id"))
    archived = _archived_dates(module, [r.business_date for r in results])
    return [{"business_date": r.business_date, "result_id": r.pk, "settlement_status": r.status,
             "computed_at": r.computed_at, "tolerance_version": r.tolerance.version if r.tolerance else None,
             "tolerance": {"amount": _m(r.tolerance_amount), "txn_count": r.tolerance_count}}
            for r in results if r.business_date not in archived]


def _stale_block(module) -> dict:
    dates = stale_tolerance_dates(module)
    return {"count": len(dates), "dates": dates,
            "note": "These dates' settlement used an older tolerance. Run each date again from its overview "
                    "to apply the active one; nothing is re-run automatically."}


class ToleranceView(SettingsView):
    def get(self, request, module):
        # Read only: no seeding here. With no version yet, settlement.tolerance
        # applies (and seeds) v1 Rs 0.00 / 0 on first use.
        versions = {t.status: t for t in _tolerances(self.module).filter(
            status__in=(MapStatus.ACTIVE, MapStatus.PENDING))}
        active, pending = versions.get(MapStatus.ACTIVE), versions.get(MapStatus.PENDING)
        return Response({
            "active": tolerance_row(active, request.user) if active else None,
            "default_if_none": {"amount": _m(tolerance_service.DEFAULT_AMOUNT),
                                "txn_count": tolerance_service.DEFAULT_COUNT},
            "pending": _tolerance_detail(pending, request.user) if pending else None,
            "can_propose": has_role(request.user, Role.ADMINISTRATOR),
            "can_decide": has_role(request.user, Role.CHECKER),
            "note": "Results already computed keep the tolerance they used; run the date again to apply a new one.",
            "dates_with_older_tolerance": _stale_block(self.module),
        })

    def post(self, request, module):
        """{"amount": "1.00", "txn_count": 0, "reason": "..."}; Administrator."""
        bad = _not_text(request.data, "reason")
        if bad:
            return bad
        try:
            t = tolerance_service.propose_tolerance_change(
                self.module, request.user, amount=request.data.get("amount"),
                txn_count=request.data.get("txn_count"), reason=request.data.get("reason"))
        except services.WorkflowError as exc:
            return _workflow_error(exc)
        return Response(_tolerance_detail(t, request.user), status=status.HTTP_201_CREATED)


class ToleranceVersionListView(SettingsView):
    def get(self, request, module):
        p, errors = request.query_params, {}
        qs = _tolerances(self.module).order_by("-version")
        statuses = _enum_filter(p, "status", MapStatus.values, errors)
        if statuses:
            qs = qs.filter(status__in=statuses)
        if errors:
            return _bad_request("Invalid input", errors)
        paginator, page = _paginate(self, request, qs)
        return paginator.get_paginated_response([tolerance_row(t, request.user) for t in page])


class ToleranceVersionDetailView(SettingsView):
    def get(self, request, module, pk):
        return Response(_tolerance_detail(get_object_or_404(_tolerances(self.module), pk=pk), request.user))


class ToleranceDecisionView(DecisionView):
    """POST .../tolerance/<id>/approve|reject/. The approve response also
    lists the dates whose settlement used an older tolerance (Q4)."""

    def target(self, pk):
        return get_object_or_404(_tolerances(self.module), pk=pk)

    def proposer_id(self, obj):
        return obj.proposed_by_id

    def is_pending(self, obj):
        return obj.status == MapStatus.PENDING

    def approve(self, obj, user, remarks):
        return tolerance_service.approve_tolerance_change(obj, user, remarks=remarks)

    def reject(self, obj, user, reason):
        return tolerance_service.reject_tolerance_change(obj, user, reason=reason)

    def body(self, obj, user, decision):
        body = _tolerance_detail(obj, user)
        if decision == "approve":
            body["dates_with_older_tolerance"] = _stale_block(self.module)
        return body


# ---------------------------------------------------------------------------
# pending approvals across the settings (design 4.16)
# ---------------------------------------------------------------------------
class PendingApprovalsView(SettingsView):
    """Every settings proposal of the module awaiting a checker, oldest first:
    response-code requests, the NTSL mapping version and the tolerance
    version. ?kind= filters (SETTINGS_KINDS). Cases awaiting approval are a
    count here (they have their own queue: cases?status=PENDING_APPROVAL)."""

    def get(self, request, module):
        p, errors = request.query_params, {}
        kinds = _enum_filter(p, "kind", list(SETTINGS_KINDS), errors) or list(SETTINGS_KINDS)
        if errors:
            return _bad_request("Invalid input", errors)
        me, items = request.user, []
        if "response_code" in kinds:
            requests = list(_code_requests(self.module).filter(status=ChangeRequestStatus.PENDING))
            events = _code_events(requests)
            for r in requests:
                target = str(r.response_code) if r.response_code else f"new {r.flag} code '{r.new_description}'"
                items.append({"kind": "response_code", "id": r.pk,
                              "title": f"{r.get_kind_display()}: {target}", "reason": r.reason,
                              "proposed_by": _user(r.requested_by), "proposed_at": r.requested_at,
                              "self_approval": r.requested_by_id == me.pk,
                              "detail": {**code_request_row(r, me), "events": events.get(r.pk, [])}})
        if "ntsl_mapping" in kinds:
            for v in _map_versions(self.module).filter(status=MapStatus.PENDING):
                row = map_version_row(v, me)
                c = row["change_counts"]
                items.append({"kind": "ntsl_mapping", "id": v.pk,
                              "title": f"NTSL mapping v{v.version}: {c['added']} added, {c['removed']} removed",
                              "reason": v.reason, "proposed_by": row["proposed_by"], "proposed_at": v.proposed_at,
                              "self_approval": row["self_approval"], "detail": row})
        if "tolerance" in kinds:
            for t in _tolerances(self.module).filter(status=MapStatus.PENDING):
                row = tolerance_row(t, me)
                items.append({"kind": "tolerance", "id": t.pk,
                              "title": f"Tolerance v{t.version}: Rs {row['amount']} / {t.txn_count} txn",
                              "reason": t.reason, "proposed_by": row["proposed_by"], "proposed_at": t.proposed_at,
                              "self_approval": row["self_approval"], "detail": row})
        for item in items:
            item["kind_label"] = SETTINGS_KINDS[item["kind"]]
        items.sort(key=lambda i: (i["proposed_at"], i["kind"], i["id"]))
        paginator, page = _paginate(self, request, items)
        response = paginator.get_paginated_response(page)
        response.data["can_decide"] = has_role(me, Role.CHECKER)
        response.data["cases_awaiting_approval"] = {
            "count": ExceptionCase.objects.filter(payment_module=self.module,
                                                  status=CaseStatus.PENDING_APPROVAL).count(),
            "query": {"status": CaseStatus.PENDING_APPROVAL}}
        return response
