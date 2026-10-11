"""
S1 classification: NTSL description -> category, using a mapping version's
entries (data, never descriptions hard-coded here).

Precedence: an EXACT entry wins; otherwise REGEX entries are tried in
(priority, id) order and the first full match wins. `matches()` returns
every candidate so callers can detect ambiguity (the maker-checker
validation refuses a proposal that makes a loaded description ambiguous).
A description with no match is unmapped -> None; the self-check fails the
cycle and lists it, it is never silently dropped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from settlement.models import MapStatus, MatchType, NtslCategoryMapEntry, NtslCategoryMapVersion


class NoActiveMapping(Exception):
    """The payment module has no ACTIVE NTSL category mapping."""


@dataclass(frozen=True)
class Classification:
    category: str
    txn_type: str
    entry_id: int | None
    match_type: str
    pattern: str


@dataclass(frozen=True)
class _Entry:
    id: int | None
    match_type: str
    pattern: str
    category: str
    txn_type: str
    priority: int
    regex: re.Pattern | None


class Classifier:
    """Built from a version's entries (or from plain dicts, for validating a
    proposal before it exists)."""

    def __init__(self, entries):
        self._exact: dict[str, _Entry] = {}
        self._regex: list[_Entry] = []
        for e in entries:
            get = (lambda k: e[k]) if isinstance(e, dict) else (lambda k: getattr(e, k))
            entry = _Entry(
                id=None if isinstance(e, dict) else e.pk,
                match_type=get("match_type"), pattern=get("pattern"), category=get("category"),
                txn_type=get("txn_type") or "", priority=get("priority"),
                regex=re.compile(get("pattern")) if get("match_type") == MatchType.REGEX else None,
            )
            if entry.match_type == MatchType.EXACT:
                self._exact[entry.pattern] = entry
            else:
                self._regex.append(entry)
        self._regex.sort(key=lambda x: (x.priority, x.id or 0))
        self._cache: dict[str, Classification | None] = {}

    @classmethod
    def for_version(cls, version: NtslCategoryMapVersion) -> "Classifier":
        return cls(NtslCategoryMapEntry.objects.filter(version=version).order_by("id"))

    @staticmethod
    def _out(e: _Entry) -> Classification:
        return Classification(e.category, e.txn_type, e.id, e.match_type, e.pattern)

    def matches(self, description: str) -> list[Classification]:
        """Every entry that applies: the EXACT one alone if there is one,
        otherwise all REGEX full matches."""
        if description in self._exact:
            return [self._out(self._exact[description])]
        return [self._out(e) for e in self._regex if e.regex.fullmatch(description)]

    def classify(self, description: str) -> Classification | None:
        if description not in self._cache:
            hits = self.matches(description)
            self._cache[description] = hits[0] if hits else None
        return self._cache[description]


def active_version(payment_module) -> NtslCategoryMapVersion:
    version = NtslCategoryMapVersion.objects.filter(payment_module=payment_module, status=MapStatus.ACTIVE).first()
    if version is None:
        raise NoActiveMapping(
            f"No ACTIVE NTSL category mapping for {payment_module}; run manage.py seed_ntsl_category_map"
        )
    return version


def active_classifier(payment_module) -> tuple[NtslCategoryMapVersion, Classifier]:
    version = active_version(payment_module)
    return version, Classifier.for_version(version)
