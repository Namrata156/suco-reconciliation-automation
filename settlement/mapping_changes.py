"""
Maker-checker for the NTSL category mapping (Phase 4 design S1: "Changing
the mapping goes through maker-checker, like the code table"). Same pattern
as exception_management.code_changes:

  - a Maker proposes a NEW version: the active version's entries, minus the
    entries removed, plus the entries added, with a reason. It is stored
    PENDING; the active mapping is unchanged.
  - a Checker approves (the proposal becomes ACTIVE, the old one RETIRED) or
    rejects (with a reason).
  - the same user may be both if they hold both roles (owner D3); it is
    allowed and flagged (self_approved), as in Phase 3.
  - every step is logged in NtslMappingEvent (append-only).

A proposal is refused when:
  - it changes nothing, or an entry is invalid (unknown category/type, a
    regex that doesn't compile, a duplicate pattern);
  - any description already loaded for the module would be UNMAPPED or
    AMBIGUOUS (two regex entries, no exact one) under it;
  - another proposal is already pending.
Approval re-runs the same validation and refuses if the active version
changed since the proposal was made (propose again on the new one).

Existing NtslCycleCheck results keep pointing at the version they used;
re-run `check_ntsl` after an approval to check with the new mapping.
"""
from __future__ import annotations

import re

from django.db import transaction
from django.utils import timezone

from exception_management.roles import Role
from exception_management.services import InvalidTransition, MissingInput, _require, _text
from settlement.categories import NtslCategory
from settlement.mapping import Classifier, active_version
from settlement.models import (
    MappingEventType,
    MapStatus,
    MatchType,
    NtslCategoryMapEntry,
    NtslCategoryMapVersion,
    NtslMappingEvent,
    TxnType,
)
from transactions.models import NtslSettlementLine

_ENTRY_KEYS = ("match_type", "pattern", "category", "txn_type", "priority", "notes")
_MAX_LISTED = 10


def _clean_entry(raw: dict) -> dict:
    match_type = (raw.get("match_type") or "").strip().upper()
    pattern = raw.get("pattern") or ""
    entry = {
        "match_type": match_type,
        # NtslSettlementLine.description is stored whitespace-stripped; a regex is kept verbatim.
        "pattern": pattern if match_type == MatchType.REGEX else pattern.strip(),
        "category": (raw.get("category") or "").strip(),
        "txn_type": (raw.get("txn_type") or "").strip(),
        "priority": int(raw.get("priority", 100)),
        "notes": (raw.get("notes") or "").strip(),
    }
    if entry["match_type"] not in MatchType.values:
        raise MissingInput(f"Unknown match type {entry['match_type']!r}; one of {', '.join(MatchType.values)}")
    if not entry["pattern"].strip():
        raise MissingInput("The pattern (description or regex) is required")
    if entry["category"] not in NtslCategory.values:
        raise MissingInput(f"Unknown category {entry['category']!r}")
    if entry["txn_type"] not in TxnType.values:
        raise MissingInput(f"Unknown transaction type {entry['txn_type']!r}; one of U2, U3 or blank")
    if entry["match_type"] == MatchType.REGEX:
        try:
            re.compile(entry["pattern"])
        except re.error as exc:
            raise MissingInput(f"Regex {entry['pattern']!r} does not compile: {exc}") from exc
    return entry


def loaded_descriptions(payment_module) -> list[str]:
    return list(
        NtslSettlementLine.objects.filter(payment_module=payment_module, uploaded_file__is_current=True)
        .values_list("description", flat=True).distinct().order_by("description")
    )


def _validate(payment_module, entries: list[dict]) -> Classifier:
    if not entries:
        raise MissingInput("A mapping needs at least one entry")
    keys = [(e["match_type"], e["pattern"]) for e in entries]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise MissingInput(f"Duplicate entries: {dupes[:_MAX_LISTED]}")
    classifier = Classifier(entries)
    unmapped, ambiguous = [], []
    for d in loaded_descriptions(payment_module):
        hits = classifier.matches(d)
        if not hits:
            unmapped.append(d)
        elif len(hits) > 1:
            ambiguous.append(d)
    if unmapped:
        raise MissingInput(
            f"{len(unmapped)} loaded NTSL description(s) would be unmapped, e.g. {unmapped[:_MAX_LISTED]}"
        )
    if ambiguous:
        raise MissingInput(
            f"{len(ambiguous)} loaded NTSL description(s) would match more than one regex entry, "
            f"e.g. {ambiguous[:_MAX_LISTED]}; add an exact entry or tighten the patterns"
        )
    return classifier


def _summary(payment_module, old_entries: list[dict], new_entries: list[dict]) -> dict:
    key = lambda e: (e["match_type"], e["pattern"], e["category"], e["txn_type"], e["priority"])  # noqa: E731
    old_keys, new_keys = {key(e) for e in old_entries}, {key(e) for e in new_entries}
    old_c, new_c = Classifier(old_entries), Classifier(new_entries)
    recategorised = []
    for d in loaded_descriptions(payment_module):
        a, b = old_c.classify(d), new_c.classify(d)
        if (a and (a.category, a.txn_type)) != (b and (b.category, b.txn_type)):
            recategorised.append({
                "description": d, "old": a and a.category, "new": b and b.category,
            })
    return {
        "added": [e for e in new_entries if key(e) not in old_keys],
        "removed": [e for e in old_entries if key(e) not in new_keys],
        "recategorised_loaded_descriptions": recategorised,
    }


def _log(version, event_type, user, *, remarks="", changes=None, self_approved=False):
    return NtslMappingEvent.objects.create(
        version=version, event_type=event_type, actor=user,
        actor_username=user.get_username() if user else "", remarks=remarks,
        changes=changes or {}, self_approved=self_approved,
    )


@transaction.atomic
def propose_mapping_change(payment_module, user, *, add=(), remove_entry_ids=(), reason: str) -> NtslCategoryMapVersion:
    """Maker: a new PENDING version = active entries - removed + added."""
    _require(user, Role.MAKER)
    reason = _text(reason, "A reason (and the source of the change)")
    active = active_version(payment_module)
    if NtslCategoryMapVersion.objects.filter(payment_module=payment_module, status=MapStatus.PENDING).exists():
        raise InvalidTransition(f"{payment_module} already has an NTSL mapping change pending approval")
    current = list(NtslCategoryMapEntry.objects.filter(version=active).order_by("id"))
    remove_entry_ids = set(remove_entry_ids)
    unknown = remove_entry_ids - {e.pk for e in current}
    if unknown:
        raise MissingInput(f"Entries {sorted(unknown)} are not in the active version v{active.version}")
    old_entries = [e.as_dict() for e in current]
    new_entries = [e.as_dict() for e in current if e.pk not in remove_entry_ids] + [_clean_entry(a) for a in add]
    if not add and not remove_entry_ids:
        raise MissingInput("The proposal changes nothing")
    _validate(payment_module, new_entries)
    summary = _summary(payment_module, old_entries, new_entries)
    if not summary["added"] and not summary["removed"]:
        raise MissingInput("The proposal changes nothing")
    last = NtslCategoryMapVersion.objects.filter(payment_module=payment_module).order_by("-version").first()
    version = NtslCategoryMapVersion.objects.create(
        payment_module=payment_module, version=last.version + 1, status=MapStatus.PENDING, based_on=active,
        reason=reason, change_summary=summary, proposed_by=user, proposed_by_username=user.get_username(),
        proposed_at=timezone.now(),
    )
    NtslCategoryMapEntry.objects.bulk_create(NtslCategoryMapEntry(version=version, **e) for e in new_entries)
    _log(version, MappingEventType.PROPOSED, user, remarks=reason, changes=summary)
    return version


def _lock_pending(version) -> NtslCategoryMapVersion:
    pk = version.pk if isinstance(version, NtslCategoryMapVersion) else version
    v = NtslCategoryMapVersion.objects.select_for_update().get(pk=pk)
    if v.status != MapStatus.PENDING:
        raise InvalidTransition(f"NTSL mapping v{v.version} is {v.status}, not pending")
    return v


@transaction.atomic
def approve_mapping_change(version, user, *, remarks: str = "") -> NtslCategoryMapVersion:
    _require(user, Role.CHECKER)
    v = _lock_pending(version)
    active = NtslCategoryMapVersion.objects.select_for_update().filter(
        payment_module_id=v.payment_module_id, status=MapStatus.ACTIVE
    ).first()
    if active is None or active.pk != v.based_on_id:
        raise InvalidTransition("The active NTSL mapping changed since this was proposed; reject it and propose again")
    _validate(v.payment_module, [e.as_dict() for e in v.entries.order_by("id")])
    now = timezone.now()
    active.status, active.retired_at = MapStatus.RETIRED, now
    active._workflow_change = True
    active.save()
    _log(active, MappingEventType.RETIRED, user, remarks=f"Replaced by v{v.version}")
    v.status, v.activated_at = MapStatus.ACTIVE, now
    v.decided_by, v.decided_by_username, v.decided_at = user, user.get_username(), now
    v.decision_remarks = (remarks or "").strip()
    v.self_approved = v.proposed_by_id == user.pk
    v._workflow_change = True
    v.save()
    _log(v, MappingEventType.APPROVED, user, remarks=v.decision_remarks, self_approved=v.self_approved)
    return v


@transaction.atomic
def reject_mapping_change(version, user, *, reason: str) -> NtslCategoryMapVersion:
    _require(user, Role.CHECKER)
    v = _lock_pending(version)
    reason = _text(reason, "A rejection reason")
    v.status = MapStatus.REJECTED
    v.decided_by, v.decided_by_username, v.decided_at, v.decision_remarks = user, user.get_username(), timezone.now(), reason
    v._workflow_change = True
    v.save()
    _log(v, MappingEventType.REJECTED, user, remarks=reason)
    return v
