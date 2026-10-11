"""
Input forms for the case transitions, the bulk actions and the code change
requests. Used by the admin screens AND the API (a Django form validates a
dict just as well), so each input is defined once. The forms only validate
input; every change is made by exception_management.services /
code_changes (see TRANSITIONS below).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from exception_management import services
from exception_management.actions import ACTION_SPECS, ActionType
from exception_management.roles import Role
from core.models import PaymentModule
from rule_engine.models import CodeFlag, ResponseCode

_ACTION_CODE_FLAGS = sorted({s.code_flag for s in ACTION_SPECS.values() if s.code_flag})


def _code_queryset(payment_module_id=None):
    qs = ResponseCode.objects.filter(flag__in=_ACTION_CODE_FLAGS).order_by("flag", "code", "description")
    return qs.filter(payment_module_id=payment_module_id) if payment_module_id else qs


class CaseForm(forms.Form):
    def __init__(self, *args, case=None, user=None, **kwargs):
        self.case, self.user = case, user
        super().__init__(*args, **kwargs)


class ConfirmForm(CaseForm):
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class ReassignForm(CaseForm):
    to_user = forms.ModelChoiceField(queryset=get_user_model().objects.none(), label="Assign to (Maker)")
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["to_user"].queryset = (
            get_user_model().objects.filter(is_active=True)
            .filter(Q(is_superuser=True) | Q(groups__name=Role.MAKER)).distinct().order_by("username")
        )


class SubmitForm(CaseForm):
    action_type = forms.ChoiceField(choices=ActionType.choices)
    response_code = forms.ModelChoiceField(
        queryset=ResponseCode.objects.none(), required=False,
        help_text="RET: the reason. DRC/TCC/RRC actions: filled in automatically when left empty.",
    )
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    evidence_cbs_reversal_date = forms.DateField(required=False, help_text="RRC 501 without a new credit: required")
    evidence_cbs_reversal_amount = forms.DecimalField(required=False, max_digits=18, decimal_places=2)
    evidence_cbs_reversal_rrn = forms.CharField(required=False, max_length=40)
    evidence_notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        case = self.case
        self.fields["response_code"].queryset = _code_queryset(case.payment_module_id if case else None)
        if case is not None and not self.is_bound:
            self.initial.setdefault("action_type", case.default_action_type or "")
            self.initial.setdefault("response_code", case.default_response_code_id)
            if case.rrc_already_sent_on:  # A1: the warning is the maker's evidence for closing
                self.initial.setdefault("evidence_notes", case.rrc_warning)
        if case is not None and not case.default_action_type:
            self.fields["action_type"].choices = [("", "-- choose (this rule has no default) --")] + ActionType.choices


class RejectForm(CaseForm):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))


class CompleteForm(CaseForm):
    external_reference = forms.CharField(max_length=100, label="NPCI portal / CBS reference")
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class RemarkForm(CaseForm):
    text = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Remark")


class ClearReviewForm(CaseForm):
    remarks = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Why the case is fine as it stands")


@dataclass(frozen=True)
class Transition:
    label: str
    form: type
    run: Callable  # (case, user, cleaned_data, source) -> result


def _submit(case, user, d, source):
    return services.submit(
        case, user, action_type=d.get("action_type") or None, response_code=d.get("response_code"),
        remarks=d.get("remarks", ""), evidence_cbs_reversal_date=d.get("evidence_cbs_reversal_date"),
        evidence_cbs_reversal_amount=d.get("evidence_cbs_reversal_amount"),
        evidence_cbs_reversal_rrn=d.get("evidence_cbs_reversal_rrn", ""), evidence_notes=d.get("evidence_notes", ""),
        source=source,
    )


TRANSITIONS: dict[str, Transition] = {
    "take": Transition("Take", ConfirmForm, lambda c, u, d, s: services.take(c, u, remarks=d.get("remarks", ""), source=s)),
    "release": Transition("Release", ConfirmForm,
                          lambda c, u, d, s: services.release(c, u, remarks=d.get("remarks", ""), source=s)),
    "reassign": Transition("Reassign", ReassignForm,
                           lambda c, u, d, s: services.reassign(c, u, d["to_user"], remarks=d.get("remarks", ""),
                                                                source=s)),
    "submit": Transition("Submit action", SubmitForm, _submit),
    "approve": Transition("Approve", ConfirmForm,
                          lambda c, u, d, s: services.approve(c, u, remarks=d.get("remarks", ""), source=s)),
    "reject": Transition("Reject", RejectForm, lambda c, u, d, s: services.reject(c, u, reason=d["reason"], source=s)),
    "complete": Transition("Mark done", CompleteForm,
                           lambda c, u, d, s: services.mark_completed(c, u, external_reference=d["external_reference"],
                                                                      remarks=d.get("remarks", ""), source=s)),
    "remark": Transition("Add remark", RemarkForm, lambda c, u, d, s: services.add_remark(c, u, d["text"], source=s)),
    "clear_review": Transition("Clear review flag", ClearReviewForm,
                               lambda c, u, d, s: services.clear_review(c, u, remarks=d["remarks"], source=s)),
}
assert set(TRANSITIONS) == set(services.TRANSITIONS)


# -- bulk ---------------------------------------------------------------------
class BulkSubmitForm(forms.Form):
    action_type = forms.ChoiceField(
        choices=[("", "-- the cases' shared pre-filled action --")] + ActionType.choices, required=False
    )
    response_code = forms.ModelChoiceField(queryset=_code_queryset(), required=False)
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class BulkApproveForm(forms.Form):
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


# -- code change requests ---------------------------------------------------------
class ProposeCodeChangeForm(forms.Form):
    response_code = forms.ModelChoiceField(queryset=ResponseCode.objects.order_by("payment_module", "flag", "code",
                                                                                   "description"))
    new_code = forms.CharField(required=False, max_length=10, help_text="Leave empty to keep the current code")
    new_description = forms.CharField(required=False, max_length=255, help_text="Leave empty to keep it")
    new_notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}),
                                help_text="Leave empty to keep the current notes")
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), help_text="Why, and the source of the value")

    def proposal(self) -> dict:
        d = self.cleaned_data
        return dict(new_code=d["new_code"] or None, new_description=d["new_description"] or None,
                    new_notes=d["new_notes"] or None, reason=d["reason"])


class ProposeNewCodeForm(forms.Form):
    """Amendment 2 B2: propose a NEW response code."""

    payment_module = forms.ModelChoiceField(queryset=PaymentModule.objects.order_by("code"))
    flag = forms.ChoiceField(choices=CodeFlag.choices)
    new_code = forms.CharField(required=False, max_length=10, label="Code",
                               help_text="Leave empty if the numeric code is not known yet")
    new_description = forms.CharField(max_length=255, label="Description")
    new_source = forms.CharField(max_length=255, label="Source", help_text="Citation, e.g. NPCI circular")
    new_notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Notes")
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))


class CodeApproveForm(forms.Form):
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class CodeRejectForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
