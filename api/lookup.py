"""
/api/v1/<module>/rrn/<rrn>/ - "Find an RRN" (UI refinements 4.5; contract
docs/ui_refinements_api_contract.txt B1). Read-only, all five roles.

Exact match only, across every business date: the RRN's transactions (match
groups) with their current rule result, its cases, and the rows of loaded
files carrying it (so an RRN that is in a file but in no transaction, e.g. a
duplicate, is still found). Indexed lookups only (match group module + rrn +
date, case rrn, fact rrn).
"""
from __future__ import annotations

import re

from django.db.models import FilteredRelation, F, Prefetch, Q
from rest_framework.response import Response

from api.base import ModuleMixin, V1APIView, module_slug
from api.operations import _bad_request
from exception_management.models import ExceptionCase
from npci_files import replies as npci_replies
from reconciliation.models import RegisterPresence, TransactionMatchGroup
from transactions.models import TransactionFact

RRN_PATTERN = re.compile(r"^[0-9A-Za-z]{1,20}$")
MAX_FILE_ROWS = 100


def clean_rrn(value: str) -> str | None:
    """Trimmed, minus a leading apostrophe (as Excel shows it); None if not an RRN."""
    v = (value or "").strip().removeprefix("'").strip()
    return v if RRN_PATTERN.match(v) else None


def _m(v) -> str | None:
    return None if v is None else f"{v:.2f}"


def _leg(legs: dict, name: str, *, npci=False) -> dict:
    p = legs.get(name)
    body = {"present": p is not None, "amount": _m(p.amount) if p else None,
            "direction": (p.direction or None) if p else None}
    if npci:
        body["response_code"] = (p.response_code or None) if p else None
    return body


class RrnLookupView(ModuleMixin, V1APIView):
    def get(self, request, module, rrn):
        p, errors = request.query_params, {}
        value = clean_rrn(rrn)
        if value is None:
            errors["rrn"] = ["Enter an RRN: letters and digits only, at most 20."]
        include = p.get("include_replaced") or "false"
        if include not in ("true", "false"):
            errors["include_replaced"] = ["Allowed: true, false"]
        if errors:
            return _bad_request("Invalid input", errors)
        slug = module_slug(self.module)

        groups = (TransactionMatchGroup.objects.filter(payment_module=self.module, rrn=value)
                  .annotate(cur=FilteredRelation("rule_results", condition=Q(rule_results__is_superseded=False)))
                  .annotate(r_rule_code=F("cur__rule_code"), r_rule_name=F("cur__rule__name"))
                  .prefetch_related(Prefetch("register_presences", queryset=RegisterPresence.objects.filter(
                      present=True, leg__in=("CBS", "NPCI")).only("id", "match_group_id", "leg", "amount",
                                                                  "direction", "response_code")))
                  .order_by("-business_date", "-pk"))
        replaced = 0
        transactions = []
        for g in groups:
            if g.is_superseded and include == "false":
                replaced += 1
                continue
            legs = {x.leg: x for x in g.register_presences.all()}
            transactions.append({
                "id": g.pk, "business_date": g.business_date, "is_superseded": g.is_superseded,
                "processing_status": g.processing_status, "rule_code": g.r_rule_code, "rule_name": g.r_rule_name,
                "cbs": _leg(legs, "CBS"), "npci": _leg(legs, "NPCI", npci=True),
                "link": f"/{slug}/dates/{g.business_date.isoformat()}/groups/{g.pk}",
            })

        cases = list(ExceptionCase.objects.filter(payment_module=self.module, rrn=value)
                     .select_related("default_response_code", "rule_result__rule")
                     .order_by("-business_date", "-pk"))
        reps = npci_replies.replies_for(cases)
        case_rows = [{
            "id": c.pk, "business_date": c.business_date, "case_type": c.case_type, "status": c.status,
            "rule_code": c.rule_code, "rule_name": c.rule_result.rule.name if c.rule_result_id else "",
            "reply_label": npci_replies.reply_label(reps[c.pk]), "link": f"/{slug}/cases/{c.pk}",
        } for c in cases]

        facts = (TransactionFact.objects.filter(payment_module=self.module, rrn=value)
                 .select_related("uploaded_file").order_by("-business_date", "-pk")[:MAX_FILE_ROWS])
        file_rows = [{
            "register_type": f.source_register_type, "business_date": f.business_date,
            "uploaded_file_id": f.uploaded_file_id, "filename": f.uploaded_file.filename if f.uploaded_file else None,
            "amount": _m(f.amount), "direction": f.direction or None,
            "link": f"/{slug}/files/{f.uploaded_file_id}" if f.uploaded_file_id else None,
        } for f in facts]

        return Response({
            "rrn": value, "found": bool(transactions or case_rows or file_rows or replaced),
            "transactions": transactions, "cases": case_rows, "file_rows": file_rows, "replaced_count": replaced,
        })
