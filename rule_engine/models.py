"""
`rule_engine` app: Business Rule Engine, Phase 2 Step 3 -- the rule
repository. The approved rule table (docs/phase2_rule_table_v3_approved.txt)
is stored here as DATA: a versioned RuleSet of Rules, a ResponseCode table
and the AgeingBucket config. Step 4 (2026-10-10) adds the evaluator
(rule_engine.facts + rule_engine.evaluator) and its output: one RuleResult
per TransactionMatchGroup per evaluation, grouped by RuleEvaluationRun.

Why a separate app (not inside `reconciliation`): the Business Rule Engine
is its own tracked component in the spec and the tracker. It consumes the
presence matrix the same way `reconciliation` consumes the `transactions`
ledger, so it follows the same one-app-per-responsibility precedent. Rule
results (Step 4) will live here too, keyed to the RuleSet version, which
the spec requires on every result ("Record the rule version used").

Versioning rules (enforced in code, see RuleSet / Rule / RuleTat / the
querysets and rule_engine.signals):
  - A RuleSet starts as DRAFT. Only a DRAFT set and its rules may be edited.
  - RuleSet.activate() validates the set and makes it ACTIVE. The module's
    previous ACTIVE set (if any) is RETIRED in the same transaction. A DB
    constraint allows at most one ACTIVE set per payment module.
  - ACTIVE and RETIRED sets are immutable: no field changes (except the
    ACTIVE -> RETIRED transition through retire()), no deletes, no changes
    to their rules, TAT rows or alternative codes. A rule change is a NEW
    version.
  - A ResponseCode used by an ACTIVE/RETIRED rule cannot be changed or
    deleted, except filling in a numeric code that was stored as unknown
    (NULL) -- see ResponseCode.
  Limits: raw SQL bypasses all of this; the ORM paths (save, delete,
  queryset update/delete/bulk_create, m2m add/remove/clear) are guarded.

Condition format: see rule_engine.conditions (module docstring).

TAT storage: Rule.tat_type says what kind of deadline the rule has:
  NONE                     -- no deadline (the doc's "TAT: -" or no TAT line)
  DAYS                     -- tat_days + tat_day_basis. "T+1" in the doc means
                              "next working day" -> tat_days=1, WORKING.
                              "35 days" / "7 days" -> CALENDAR.
  BY_ADJTYPE               -- R07b: the deadline depends on the Adjtype; one
                              RuleTat row per type, checked in order, first
                              match wins (same condition format).
  NEXT_BUSINESS_DATE_LOAD  -- R13: event-driven, due when the next business
                              date's NPCI files are loaded. No fixed days.
Due date = T + TAT (doc, AGEING). WORKING days use core.working_days
(Karnataka bank holidays + Sundays + 2nd/4th Saturdays, owner 2026-10-10).
"""
from __future__ import annotations

import string
from datetime import date, datetime, time

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from core.models import PaymentModule
from reconciliation.models import TransactionMatchGroup
from transactions.models import TransactionFact
from rule_engine.conditions import ConditionError, is_catch_all, validate_condition


class RuleSetImmutableError(Exception):
    """An ACTIVE/RETIRED rule set (or something belonging to it) was about to change."""


# ---------------------------------------------------------------------------
# Choices
# ---------------------------------------------------------------------------
class RuleSetStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ACTIVE = "ACTIVE", "Active"
    RETIRED = "RETIRED", "Retired"


class StatusCategory(models.TextChoices):
    """The spec's nine categories (Business Rule Engine > Exception and Action
    Classification), labels verbatim from the spec."""

    RECONCILED = "RECONCILED", "Reconciled"
    EXCEPTION = "EXCEPTION", "Exception"
    PENDING_INVESTIGATION = "PENDING_INVESTIGATION", "Pending investigation"
    REVERSAL_REQUIRED = "REVERSAL_REQUIRED", "Reversal Required"
    ADJUSTMENT_REQUIRED = "ADJUSTMENT_REQUIRED", "Adjustment Required"
    MANUAL_CREDIT_REQUIRED = "MANUAL_CREDIT_REQUIRED", "Manual Credit Required"
    RETURN_REQUIRED = "RETURN_REQUIRED", "Return Required"
    CHARGEBACK_REQUIRED = "CHARGEBACK_REQUIRED", "Chargeback Required"
    NO_FINANCIAL_ACTION_REQUIRED = "NO_FINANCIAL_ACTION_REQUIRED", "No Financial Action Required"


class Priority(models.TextChoices):
    HIGH = "HIGH", "High"
    MEDIUM = "MEDIUM", "Medium"
    LOW = "LOW", "Low"
    NONE = "NONE", "None"


class TatType(models.TextChoices):
    NONE = "NONE", "No deadline"
    DAYS = "DAYS", "Fixed days"
    BY_ADJTYPE = "BY_ADJTYPE", "Varies by Adjtype (see TAT rows)"
    NEXT_BUSINESS_DATE_LOAD = "NEXT_BUSINESS_DATE_LOAD", "Next business date's load"


class DayBasis(models.TextChoices):
    WORKING = "WORKING", "Working days"
    CALENDAR = "CALENDAR", "Calendar days"


class CodeFlag(models.TextChoices):
    """The 'flag' a code is sent/received under (ops' note: Flag column)."""

    DRC = "DRC", "DRC (Debit Reversal Confirmation)"
    TCC = "TCC", "TCC (Transaction Credit Confirmation)"
    RET = "RET", "RET (Return)"
    RRC = "RRC", "RRC (Adjustment reply)"
    NPCI_RESPONSE = "NPCI_RESPONSE", "NPCI response code"


_NON_DRAFT = (RuleSetStatus.ACTIVE, RuleSetStatus.RETIRED)

# Placeholders allowed in Rule.reason_template ({name}); the Step 4 evaluator
# fills them from the matrix fact on the right.
REASON_TEMPLATE_PLACEHOLDERS = {"age_days": "adjustment.age_days"}


def _validate_reason_template(template: str) -> None:
    try:
        names = {name for _, name, _, _ in string.Formatter().parse(template) if name is not None}
    except ValueError as exc:
        raise ValidationError({"reason_template": f"Bad template: {exc}"}) from exc
    unknown = names - set(REASON_TEMPLATE_PLACEHOLDERS)
    if unknown:
        raise ValidationError({"reason_template": f"Unknown placeholder(s): {sorted(unknown)}"})


def _validate_tat(tat_type: str, tat_days, tat_day_basis: str, field_prefix: str = "tat_") -> None:
    if tat_type == TatType.DAYS:
        if tat_days is None or not tat_day_basis:
            raise ValidationError(f"{field_prefix}type DAYS needs tat_days and tat_day_basis")
    elif tat_days is not None or tat_day_basis:
        raise ValidationError(f"{field_prefix}type {tat_type} must not set tat_days / tat_day_basis")


# ---------------------------------------------------------------------------
# RuleSet
# ---------------------------------------------------------------------------
class RuleSetQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if self.filter(status__in=_NON_DRAFT).exists():
            raise RuleSetImmutableError("Bulk update touches an ACTIVE/RETIRED rule set")
        if kwargs.get("status") not in (None, RuleSetStatus.DRAFT):
            raise RuleSetImmutableError("Use RuleSet.activate()/retire() to change status")
        return super().update(**kwargs)

    def delete(self):
        if self.filter(status__in=_NON_DRAFT).exists():
            raise RuleSetImmutableError("Only DRAFT rule sets can be deleted")
        return super().delete()


class RuleSet(models.Model):
    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="rule_sets")
    version_label = models.CharField(max_length=20, help_text="e.g. 'v3.1'")
    status = models.CharField(max_length=10, choices=RuleSetStatus.choices, default=RuleSetStatus.DRAFT)
    effective_from = models.DateField()
    source_document = models.CharField(
        max_length=255, help_text="Approved document this version was built from (repo path + approval)"
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(
        max_length=150, help_text="Username, or the seed process ('seed_rule_sets', a migration)"
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    objects = RuleSetQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["payment_module", "version_label"], name="unique_ruleset_version_per_module"
            ),
            models.UniqueConstraint(
                fields=["payment_module"],
                condition=Q(status="ACTIVE"),
                name="one_active_ruleset_per_module",
            ),
        ]
        ordering = ["payment_module", "-created_at"]

    def __str__(self) -> str:
        return f"{self.payment_module.code} {self.version_label} [{self.status}]"

    # -- immutability -------------------------------------------------------
    _transition = False  # set only by activate()/retire()

    def save(self, *args, **kwargs):
        try:
            if self._state.adding:
                if self.status != RuleSetStatus.DRAFT:
                    raise RuleSetImmutableError("A new rule set must start as DRAFT; use activate()")
            else:
                self._check_update_allowed()
            super().save(*args, **kwargs)
        finally:
            self._transition = False

    def _check_update_allowed(self) -> None:
        old = RuleSet.objects.get(pk=self.pk)
        if old.status == RuleSetStatus.DRAFT:
            if self.status == RuleSetStatus.DRAFT or (self.status == RuleSetStatus.ACTIVE and self._transition):
                return
            raise RuleSetImmutableError("Use RuleSet.activate() to make a draft active")
        if old.status == RuleSetStatus.ACTIVE and self.status == RuleSetStatus.RETIRED and self._transition:
            changed = [
                f.attname
                for f in self._meta.concrete_fields
                if f.attname not in ("status", "retired_at") and getattr(old, f.attname) != getattr(self, f.attname)
            ]
            if not changed:
                return
            raise RuleSetImmutableError(f"Retiring may not change other fields: {changed}")
        raise RuleSetImmutableError(
            f"Rule set {old.version_label} is {old.status} and immutable. Create a new version instead."
        )

    def delete(self, *args, **kwargs):
        if RuleSet.objects.filter(pk=self.pk, status__in=_NON_DRAFT).exists():
            raise RuleSetImmutableError("Only DRAFT rule sets can be deleted")
        return super().delete(*args, **kwargs)

    # -- lifecycle -----------------------------------------------------------
    def validate_for_activation(self) -> None:
        """Structural checks a set must pass before it can become ACTIVE."""
        errors: list[str] = []
        rules = list(self.rules.order_by("evaluation_order").prefetch_related("tat_rows"))
        if not rules:
            raise ValidationError("Rule set has no rules")
        for rule in rules:
            try:
                validate_condition(rule.condition)
            except ConditionError as exc:
                errors.append(f"{rule.code}: {exc}")
            has_tat_rows = bool(rule.tat_rows.all())
            if rule.tat_type == TatType.BY_ADJTYPE and not has_tat_rows:
                errors.append(f"{rule.code}: tat_type BY_ADJTYPE needs at least one TAT row")
            if rule.tat_type != TatType.BY_ADJTYPE and has_tat_rows:
                errors.append(f"{rule.code}: TAT rows are only allowed with tat_type BY_ADJTYPE")
        catch_alls = [r.code for r in rules if is_catch_all(r.condition)]
        if catch_alls != [rules[-1].code]:
            errors.append(
                "Exactly one catch-all rule ({'always': true}) is required, and it must be last; "
                f"found {catch_alls or 'none'}"
            )
        if errors:
            raise ValidationError(errors)

    def activate(self) -> None:
        with transaction.atomic():
            locked = RuleSet.objects.select_for_update().get(pk=self.pk)
            if locked.status != RuleSetStatus.DRAFT:
                raise RuleSetImmutableError(f"Only a DRAFT rule set can be activated (is {locked.status})")
            self.validate_for_activation()
            for current in RuleSet.objects.select_for_update().filter(
                payment_module_id=self.payment_module_id, status=RuleSetStatus.ACTIVE
            ):
                current.retire()
            self.status = RuleSetStatus.ACTIVE
            self.activated_at = timezone.now()
            self._transition = True
            self.save()

    def retire(self) -> None:
        if RuleSet.objects.get(pk=self.pk).status != RuleSetStatus.ACTIVE:
            raise RuleSetImmutableError("Only an ACTIVE rule set can be retired")
        self.status = RuleSetStatus.RETIRED
        self.retired_at = timezone.now()
        self._transition = True
        self.save()


def _assert_rule_sets_draft(rule_set_ids) -> None:
    ids = {i for i in rule_set_ids if i is not None}
    if ids and RuleSet.objects.filter(pk__in=ids, status__in=_NON_DRAFT).exists():
        raise RuleSetImmutableError("Rules of an ACTIVE/RETIRED rule set cannot change; create a new version")


# ---------------------------------------------------------------------------
# ResponseCode
# ---------------------------------------------------------------------------
def _locked_codes_q() -> Q:
    return Q(rules_as_default__rule_set__status__in=_NON_DRAFT) | Q(
        rules_as_alternative__rule_set__status__in=_NON_DRAFT
    )


class ResponseCodeQuerySet(models.QuerySet):
    def locked(self):
        return self.filter(_locked_codes_q()).distinct()

    def update(self, **kwargs):
        if self.locked().exists():
            raise RuleSetImmutableError("Bulk update touches a code used by an ACTIVE/RETIRED rule set")
        return super().update(**kwargs)

    def delete(self):
        if self.locked().exists():
            raise RuleSetImmutableError("Bulk delete touches a code used by an ACTIVE/RETIRED rule set")
        return super().delete()


class ResponseCode(models.Model):
    """
    Centralized Error/Response Code repository (spec: Business Rule Engine >
    Error Code and Response Code Management), one table for every payment
    module. `code` is NULL where the numeric code is not known yet (the SOP's
    RET reasons other than 115): never guessed. Which rules use a code is
    derived (see used_by), not stored, so it cannot drift from the rules.
    """

    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="response_codes")
    flag = models.CharField(max_length=20, choices=CodeFlag.choices)
    code = models.CharField(max_length=10, null=True, blank=True, help_text="NULL = numeric code unknown")
    description = models.CharField(max_length=255)
    source = models.CharField(max_length=255)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ResponseCodeQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["payment_module", "flag", "code"],
                condition=Q(code__isnull=False),
                name="unique_known_code_per_module_flag",
            ),
            models.UniqueConstraint(
                fields=["payment_module", "flag", "description"], name="unique_code_description_per_flag"
            ),
        ]
        ordering = ["payment_module", "flag", "code", "description"]

    def __str__(self) -> str:
        return f"{self.flag} {self.code or '(unknown)'} - {self.description}"

    @property
    def code_known(self) -> bool:
        return self.code is not None

    def is_locked(self) -> bool:
        return self.pk is not None and ResponseCode.objects.filter(pk=self.pk).filter(_locked_codes_q()).exists()

    def save(self, *args, **kwargs):
        if not self._state.adding and self.is_locked():
            old = ResponseCode.objects.get(pk=self.pk)
            changed = [
                f.attname
                for f in self._meta.concrete_fields
                if getattr(old, f.attname) != getattr(self, f.attname)
                and not (f.attname == "code" and old.code is None)
            ]
            if changed:
                raise RuleSetImmutableError(
                    f"{old} is used by an ACTIVE/RETIRED rule set; only an unknown code may be filled in "
                    f"(tried to change {changed})"
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.is_locked():
            raise RuleSetImmutableError(f"{self} is used by an ACTIVE/RETIRED rule set")
        return super().delete(*args, **kwargs)

    def used_by(self, rule_set: RuleSet) -> list[str]:
        """Rule codes in `rule_set` that use this code: as the rule's code, as
        an alternative the maker may pick, or (NPCI response codes) in the
        rule's condition on npci.response_code."""
        used = []
        for rule in rule_set.rules.order_by("evaluation_order").prefetch_related("alternative_codes"):
            if rule.response_code_id == self.pk or any(c.pk == self.pk for c in rule.alternative_codes.all()):
                used.append(rule.code)
            elif self.flag == CodeFlag.NPCI_RESPONSE and self.code and _condition_mentions(
                rule.condition, "npci.response_code", self.code
            ):
                used.append(rule.code)
        return used


def _condition_mentions(node: dict, fact: str, value: str) -> bool:
    for key in ("all", "any"):
        if key in node:
            return any(_condition_mentions(c, fact, value) for c in node[key])
    if node.get("fact") == fact:
        v = node.get("value")
        return v == value or (isinstance(v, list) and value in v)
    return False


# ---------------------------------------------------------------------------
# Rule
# ---------------------------------------------------------------------------
class RuleQuerySet(models.QuerySet):
    def update(self, **kwargs):
        _assert_rule_sets_draft(self.values_list("rule_set_id", flat=True))
        if "rule_set" in kwargs or "rule_set_id" in kwargs:
            target = kwargs.get("rule_set_id") or getattr(kwargs.get("rule_set"), "pk", None)
            _assert_rule_sets_draft([target])
        return super().update(**kwargs)

    def delete(self):
        _assert_rule_sets_draft(self.values_list("rule_set_id", flat=True))
        return super().delete()

    def bulk_create(self, objs, *args, **kwargs):
        _assert_rule_sets_draft([o.rule_set_id for o in objs])
        for o in objs:
            o.full_clean(exclude=["rule_set"], validate_unique=False, validate_constraints=False)
        return super().bulk_create(objs, *args, **kwargs)


class Rule(models.Model):
    rule_set = models.ForeignKey(RuleSet, on_delete=models.CASCADE, related_name="rules")
    code = models.CharField(max_length=10, help_text="e.g. R01, R04c, R06b, R07x, R99")
    evaluation_order = models.PositiveSmallIntegerField(help_text="Checked low to high; first match wins")
    name = models.CharField(max_length=200)
    condition = models.JSONField(help_text="Declarative `when`; format in rule_engine.conditions")
    status_category = models.CharField(max_length=40, choices=StatusCategory.choices)
    action = models.TextField()
    priority = models.CharField(max_length=10, choices=Priority.choices)
    response_code = models.ForeignKey(
        ResponseCode,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="rules_as_default",
        help_text="The rule's code (pre-filled for the maker where alternatives exist)",
    )
    alternative_codes = models.ManyToManyField(
        ResponseCode,
        blank=True,
        related_name="rules_as_alternative",
        help_text="Other codes the maker may choose instead (R05: the SOP's other RET reasons)",
    )
    tat_type = models.CharField(max_length=30, choices=TatType.choices)
    tat_days = models.PositiveSmallIntegerField(null=True, blank=True)
    tat_day_basis = models.CharField(max_length=10, choices=DayBasis.choices, blank=True)
    tat_cutoff_time = models.TimeField(
        null=True, blank=True, help_text="Time of day on the due date (R05: RET by 4 pm)"
    )
    tat_note = models.TextField(blank=True, help_text="TAT text from the doc not captured by the fields above")
    rbi_compensation_after_tat = models.BooleanField(
        default=False, help_text="RBI Rs 100/day customer compensation after the TAT (doc AGEING: R02, R05, R12)"
    )
    reason_template = models.TextField(
        blank=True,
        help_text="Reason shown to the maker. Placeholders: " + ", ".join(f"{{{k}}}" for k in REASON_TEMPLATE_PLACEHOLDERS),
    )
    source = models.TextField(help_text="Citation, from the doc's Source line")
    notes = models.TextField(blank=True)

    objects = RuleQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["rule_set", "code"], name="unique_rule_code_per_set"),
            models.UniqueConstraint(fields=["rule_set", "evaluation_order"], name="unique_rule_order_per_set"),
            models.CheckConstraint(
                condition=(Q(tat_type="DAYS", tat_days__isnull=False) & ~Q(tat_day_basis=""))
                | (~Q(tat_type="DAYS") & Q(tat_days__isnull=True, tat_day_basis="")),
                name="rule_tat_days_iff_days_type",
            ),
        ]
        ordering = ["rule_set", "evaluation_order"]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"

    def clean(self):
        try:
            validate_condition(self.condition)
        except ConditionError as exc:
            raise ValidationError({"condition": str(exc)}) from exc
        _validate_tat(self.tat_type, self.tat_days, self.tat_day_basis)
        _validate_reason_template(self.reason_template)
        if self.response_code_id and self.rule_set_id:
            if self.response_code.payment_module_id != self.rule_set.payment_module_id:
                raise ValidationError({"response_code": "Code belongs to another payment module"})

    def save(self, *args, **kwargs):
        ids = [self.rule_set_id]
        if self.pk:
            ids += list(Rule.objects.filter(pk=self.pk).values_list("rule_set_id", flat=True))
        _assert_rule_sets_draft(ids)
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _assert_rule_sets_draft(Rule.objects.filter(pk=self.pk).values_list("rule_set_id", flat=True))
        return super().delete(*args, **kwargs)


# ---------------------------------------------------------------------------
# RuleTat -- per-Adjtype deadlines (R07b)
# ---------------------------------------------------------------------------
class RuleTatQuerySet(models.QuerySet):
    def update(self, **kwargs):
        _assert_rule_sets_draft(self.values_list("rule__rule_set_id", flat=True))
        return super().update(**kwargs)

    def delete(self):
        _assert_rule_sets_draft(self.values_list("rule__rule_set_id", flat=True))
        return super().delete()

    def bulk_create(self, objs, *args, **kwargs):
        _assert_rule_sets_draft(Rule.objects.filter(pk__in=[o.rule_id for o in objs]).values_list("rule_set_id", flat=True))
        for o in objs:
            o.full_clean(exclude=["rule"], validate_unique=False, validate_constraints=False)
        return super().bulk_create(objs, *args, **kwargs)


class RuleTat(models.Model):
    """One deadline for a rule whose TAT varies by Adjtype (tat_type BY_ADJTYPE).
    Rows are checked in evaluation_order; the first whose condition matches
    gives the TAT. No match = no TAT known for that type (Step 4 must flag it)."""

    rule = models.ForeignKey(Rule, on_delete=models.CASCADE, related_name="tat_rows")
    evaluation_order = models.PositiveSmallIntegerField()
    label = models.CharField(max_length=200, help_text="The doc's type label")
    condition = models.JSONField(help_text="Which Adjtypes this row covers; format in rule_engine.conditions")
    tat_days = models.PositiveSmallIntegerField()
    tat_day_basis = models.CharField(max_length=10, choices=DayBasis.choices)
    source = models.CharField(max_length=255)
    notes = models.TextField(blank=True)

    objects = RuleTatQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["rule", "evaluation_order"], name="unique_tat_row_order_per_rule"),
        ]
        ordering = ["rule", "evaluation_order"]

    def __str__(self) -> str:
        return f"{self.rule.code}: {self.label} -> {self.tat_days} {self.tat_day_basis}"

    def clean(self):
        try:
            validate_condition(self.condition)
        except ConditionError as exc:
            raise ValidationError({"condition": str(exc)}) from exc
        if self.rule_id and self.rule.tat_type != TatType.BY_ADJTYPE:
            raise ValidationError("TAT rows are only allowed on a rule with tat_type BY_ADJTYPE")

    def save(self, *args, **kwargs):
        ids = [self.rule.rule_set_id]
        if self.pk:
            ids += list(RuleTat.objects.filter(pk=self.pk).values_list("rule__rule_set_id", flat=True))
        _assert_rule_sets_draft(ids)
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        _assert_rule_sets_draft(RuleTat.objects.filter(pk=self.pk).values_list("rule__rule_set_id", flat=True))
        return super().delete(*args, **kwargs)


# ---------------------------------------------------------------------------
# AgeingBucket
# ---------------------------------------------------------------------------
class AgeingBucket(models.Model):
    """Ageing report bucket (doc AGEING, Phase 6 report). Age in days since
    the business date. max_days NULL = open-ended."""

    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="ageing_buckets")
    order = models.PositiveSmallIntegerField()
    label = models.CharField(max_length=20)
    min_days = models.PositiveSmallIntegerField()
    max_days = models.PositiveSmallIntegerField(null=True, blank=True)
    boundary_reason = models.CharField(max_length=255)
    source = models.CharField(max_length=255)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["payment_module", "order"], name="unique_bucket_order_per_module"),
            models.UniqueConstraint(fields=["payment_module", "label"], name="unique_bucket_label_per_module"),
            models.CheckConstraint(
                condition=Q(max_days__isnull=True) | Q(max_days__gte=models.F("min_days")),
                name="bucket_max_gte_min",
            ),
        ]
        ordering = ["payment_module", "order"]

    def __str__(self) -> str:
        return f"{self.payment_module.code} {self.label}"


def validate_ageing_buckets(payment_module: PaymentModule) -> None:
    """Buckets must start at 0, be contiguous with no overlap, and end open-ended."""
    buckets = list(AgeingBucket.objects.filter(payment_module=payment_module).order_by("order"))
    if not buckets:
        raise ValidationError("No ageing buckets")
    expected_min = 0
    for b in buckets:
        if b.min_days != expected_min:
            raise ValidationError(f"Bucket {b.label} starts at {b.min_days}, expected {expected_min}")
        if b.max_days is None:
            if b is not buckets[-1]:
                raise ValidationError(f"Only the last bucket may be open-ended ({b.label})")
            return
        expected_min = b.max_days + 1
    raise ValidationError("The last bucket must be open-ended")


# ---------------------------------------------------------------------------
# Step 4: evaluation output
# ---------------------------------------------------------------------------
class RecheckState(models.TextChoices):
    """R13's next-day re-check (owner Q3) and its mirror on R12. Blank for
    every other rule. Stored on the result, not as extra rules: the rule set
    stays exactly the approved v3.1."""

    NONE = "", "-"
    AWAITING_NEXT_DAY = "awaiting_next_day", "Awaiting next business date's NPCI files"
    MATCHED_NEXT_DAY = "matched_next_day", "Found in next business date's NPCI files (late cycle)"
    ESCALATED = "escalated", "Not in next business date's NPCI files: escalated"
    MATCHED_PREVIOUS_DAY = "matched_previous_day", "CBS posted it on the previous business date (late match)"


class RuleEvaluationRun(models.Model):
    """One run of the evaluator over one (payment module, business date)."""

    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="rule_evaluation_runs")
    business_date = models.DateField()
    rule_set = models.ForeignKey(RuleSet, on_delete=models.PROTECT, related_name="evaluation_runs")
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    group_count = models.PositiveIntegerField(default=0)
    superseded_count = models.PositiveIntegerField(default=0, help_text="Previous results this run superseded")
    rule_counts = models.JSONField(default=dict, blank=True, help_text="{rule code: results}")
    recheck_counts = models.JSONField(default=dict, blank=True, help_text="{recheck state: results} after this run")
    calendar_incomplete_count = models.PositiveIntegerField(default=0)
    triggered_by = models.CharField(max_length=150, blank=True, help_text="Command / task / user that ran it")

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["payment_module", "business_date"], name="ruleeval_module_date_idx")]

    def __str__(self) -> str:
        return f"{self.payment_module.code} {self.business_date} {self.rule_set.version_label} @ {self.started_at:%Y-%m-%d %H:%M}"


class RuleResultQuerySet(models.QuerySet):
    def current(self):
        return self.filter(is_superseded=False)

    def with_ageing(self, today: date | None = None, now: datetime | None = None):
        """Annotates age_days, ageing_bucket and is_overdue. Derived at read
        time, never stored (they change daily). `now` defaults to the current
        local time and `today` to its date. Age counts from T (tat_start_date)."""
        now = timezone.localtime(now) if now else timezone.localtime()
        today = today or now.date()
        age = models.Func(
            models.Value(today, output_field=models.DateField()),
            models.F("tat_start_date"),
            template="(%(expressions)s)",
            arg_joiner=" - ",
            output_field=models.IntegerField(),
        )
        whens = []
        for bucket in AgeingBucket.objects.order_by("payment_module_id", "order"):
            cond = Q(payment_module_id=bucket.payment_module_id, age_days__gte=bucket.min_days)
            if bucket.max_days is not None:
                cond &= Q(age_days__lte=bucket.max_days)
            whens.append(models.When(cond, then=models.Value(bucket.label)))
        overdue = Q(due_date__lt=today) | Q(
            due_date=today, due_cutoff_time__isnull=False, due_cutoff_time__lt=now.time()
        )
        qs = self.annotate(age_days=age).annotate(
            is_overdue=models.ExpressionWrapper(Q(due_date__isnull=False) & overdue, output_field=models.BooleanField())
        )
        bucket_expr = (
            models.Case(*whens, default=models.Value(""), output_field=models.CharField())
            if whens
            else models.Value("", output_field=models.CharField())
        )
        return qs.annotate(ageing_bucket=bucket_expr)


class RuleResult(models.Model):
    """
    The outcome of the active rule set for one TransactionMatchGroup in one
    evaluation run. Rules only RECOMMEND: nothing here moves money or
    produces an NPCI file; every action still goes through maker-checker
    (Phase 3).

    History: re-running a date marks that date's current results superseded
    (is_superseded + superseded_at) and creates new ones -- kept, never
    deleted (same rule as the presence matrix, owner 2026-09-30). At most
    one current result per match group (DB constraint).

    status_category / priority / due_* / rbi_compensation are the EFFECTIVE
    values: the fired rule's defaults, except where the R13 / R12 next-day
    re-check (recheck_state) overrides them. `rule` always stays the rule
    that fired.

    Deadline: due_date + due_cutoff_time (R05: 16:00 on the due date) rather
    than one datetime, so date-level work (calendar, ageing, "due today")
    stays plain date arithmetic and a deadline with no time is not given an
    arbitrary end-of-day time. due_datetime combines them (local time; a
    date-only deadline runs to the end of the day).
    """

    # Index policy: every index costs ~79K inserts per evaluation and as many
    # on a re-run's supersede (indexed column changes -> no HOT update).
    # Only the indexes queries use are kept (see Meta); FKs whose targets
    # are never deleted (immutable rules/sets, runs, modules) get none, and
    # the sparse re-check FKs get partial indexes.
    payment_module = models.ForeignKey(
        PaymentModule, on_delete=models.PROTECT, related_name="rule_results", db_index=False
    )
    match_group = models.ForeignKey(TransactionMatchGroup, on_delete=models.PROTECT, related_name="rule_results")
    business_date = models.DateField(help_text="The match group's business date (denormalized)")
    rrn = models.CharField(max_length=20, help_text="The match group's RRN (denormalized for cross-date lookup)")
    run = models.ForeignKey(RuleEvaluationRun, on_delete=models.PROTECT, related_name="results", db_index=False)

    rule_set = models.ForeignKey(RuleSet, on_delete=models.PROTECT, related_name="results", db_index=False)
    rule = models.ForeignKey(Rule, on_delete=models.PROTECT, related_name="results", db_index=False)
    rule_code = models.CharField(max_length=10, help_text="rule.code (denormalized for filtering)")

    status_category = models.CharField(max_length=40, choices=StatusCategory.choices)
    priority = models.CharField(max_length=10, choices=Priority.choices)
    action = models.TextField(help_text="The fired rule's action text")
    response_code = models.ForeignKey(
        ResponseCode,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="results",
        db_index=False,
        help_text="The rule's default code; the maker's alternatives (R05) are rule.alternative_codes",
    )
    reason = models.TextField(blank=True, help_text="The rule's reason_template with placeholders filled")

    tat_start_date = models.DateField(
        help_text="T, the date the clock starts: the group's business date, or the Adjustment's own date "
        "(Adjdate) for rules that fire on the Adjustment overlay"
    )
    tat_label = models.CharField(max_length=200, blank=True, help_text="Which deadline applied")
    tat_unresolved = models.BooleanField(
        default=False, help_text="BY_ADJTYPE rule whose Adjtype matched no TAT row: no deadline known"
    )
    due_date = models.DateField(null=True, blank=True)
    due_cutoff_time = models.TimeField(null=True, blank=True, help_text="Time on the due date (R05: 16:00)")
    calendar_incomplete = models.BooleanField(
        default=False, help_text="The working-day count crossed a year with no bank holidays loaded"
    )
    rbi_compensation = models.BooleanField(
        default=False, help_text="RBI Rs 100/day customer compensation after the due date (R02, R05, R12)"
    )

    facts = models.JSONField(help_text="Snapshot of the fact values the conditions saw (audit)")
    evaluated_at = models.DateTimeField()
    is_superseded = models.BooleanField(default=False)
    superseded_at = models.DateTimeField(null=True, blank=True)

    # R13 next-day re-check and its R12 mirror (owner Q3)
    recheck_state = models.CharField(max_length=30, choices=RecheckState.choices, blank=True, default="")
    recheck_checked_at = models.DateTimeField(null=True, blank=True)
    escalated_on = models.DateField(
        null=True, blank=True, help_text="R13: date first escalated; carried across re-runs so the deadline can't creep"
    )
    next_day_fact = models.ForeignKey(
        TransactionFact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        db_index=False,
        help_text="R13 matched_next_day: the D+1 NPCI row that cleared it (code 00 or RB)",
    )
    next_day_npci_codes = models.CharField(
        max_length=50,
        blank=True,
        help_text="R13: response codes of every D+1 NPCI row for the RRN (comma-separated; only 00 / RB clear it)",
    )
    next_day_group = models.ForeignKey(
        TransactionMatchGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        db_index=False,
        help_text="R13 matched_next_day: D+1's match group for the RRN, if built",
    )
    linked_result = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        db_index=False,
        help_text="Cross-date link: R13 on D <-> R12 on D+1 (the other date's current result)",
    )

    objects = RuleResultQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["match_group"],
                condition=Q(is_superseded=False),
                name="one_current_rule_result_per_group",
            ),
        ]
        indexes = [
            models.Index(fields=["payment_module", "business_date", "is_superseded"], name="ruleresult_module_date_idx"),
            models.Index(fields=["rrn", "business_date"], name="ruleresult_rrn_date_idx"),
            models.Index(fields=["rule_code", "business_date"], name="ruleresult_rule_date_idx"),
            # Sparse FKs (re-check only): partial indexes keep SET_NULL on
            # fact / group / result deletes from scanning the table.
            models.Index(
                fields=["next_day_fact"], condition=Q(next_day_fact__isnull=False), name="ruleresult_nextday_fact_idx"
            ),
            models.Index(
                fields=["next_day_group"], condition=Q(next_day_group__isnull=False), name="ruleresult_nextday_grp_idx"
            ),
            models.Index(
                fields=["linked_result"], condition=Q(linked_result__isnull=False), name="ruleresult_linked_idx"
            ),
        ]
        ordering = ["business_date", "rrn", "-evaluated_at"]

    def __str__(self) -> str:
        return f"{self.rrn} ({self.business_date}) {self.rule_code} {self.status_category}"

    # -- derived at read time ------------------------------------------------
    @property
    def alternative_codes(self):
        """Codes the maker may choose instead of the default (R05's RET reasons)."""
        return self.rule.alternative_codes.all()

    @property
    def due_datetime(self) -> datetime | None:
        if self.due_date is None:
            return None
        return timezone.make_aware(datetime.combine(self.due_date, self.due_cutoff_time or time.max))

    def age_days(self, today: date | None = None) -> int:
        return ((today or timezone.localdate()) - self.tat_start_date).days

    def ageing_bucket(self, today: date | None = None) -> str:
        age = self.age_days(today)
        for bucket in AgeingBucket.objects.filter(payment_module_id=self.payment_module_id).order_by("order"):
            if age >= bucket.min_days and (bucket.max_days is None or age <= bucket.max_days):
                return bucket.label
        return ""

    def is_overdue(self, now: datetime | None = None) -> bool:
        due = self.due_datetime
        return due is not None and timezone.localtime(now or timezone.now()) > due
