"""
Loads a frozen rule-table version (rule_engine/seed_data/v3_1.py) into the
DB through the live models, so every model check runs: condition
validation, TAT consistency, and RuleSet.activate()'s structural checks.
Used by `manage.py seed_rule_sets`. The data migration rule_engine/0002
does the same inserts with historical models (see its docstring);
rule_engine/tests/test_seed.py asserts both give identical rows.

Idempotent and create-only:
  - codes and ageing buckets: get_or_create on their natural keys; existing
    rows are left alone (differences are reported).
  - the rule set: if the version already exists it is left alone (versions
    are immutable); differences from the seed data are reported. If the
    module already has a DIFFERENT active version, nothing is created (we
    would otherwise retire someone's newer version).
  Everything runs in one transaction, so a failure leaves no half-built draft.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import ModuleType

from django.db import transaction

from core.models import PaymentModule
from rule_engine.models import AgeingBucket, ResponseCode, Rule, RuleSet, RuleSetStatus, RuleTat


@dataclass
class SeedReport:
    codes_created: int = 0
    codes_existing: int = 0
    buckets_created: int = 0
    buckets_existing: int = 0
    rule_set_created: bool = False
    rules_created: int = 0
    warnings: list[str] = field(default_factory=list)


def _seed_codes(module: PaymentModule, data: ModuleType, report: SeedReport) -> dict:
    by_key = {}
    for flag, code, description, source, notes in data.RESPONSE_CODES:
        obj, created = ResponseCode.objects.get_or_create(
            payment_module=module,
            flag=flag,
            description=description,
            defaults=dict(code=code, source=source, notes=notes),
        )
        if created:
            report.codes_created += 1
        else:
            report.codes_existing += 1
            if obj.code != code and not (code is None and obj.code is not None):
                report.warnings.append(f"Code {flag} '{description}' is {obj.code!r}, seed has {code!r} (left unchanged)")
        by_key[(flag, description)] = obj
    return by_key


def _seed_buckets(module: PaymentModule, data: ModuleType, report: SeedReport) -> None:
    for order, label, lo, hi, reason in data.AGEING_BUCKETS:
        obj, created = AgeingBucket.objects.get_or_create(
            payment_module=module,
            order=order,
            defaults=dict(label=label, min_days=lo, max_days=hi, boundary_reason=reason, source=data.AGEING_SOURCE),
        )
        if created:
            report.buckets_created += 1
        else:
            report.buckets_existing += 1
            if (obj.label, obj.min_days, obj.max_days) != (label, lo, hi):
                report.warnings.append(f"Ageing bucket {order} is {obj.label} {obj.min_days}-{obj.max_days}, seed has {label}")


def rule_fields(spec: dict, codes: dict) -> dict:
    """Rule model kwargs from one seed-data rule dict (shared with tests)."""
    rc = spec.get("response_code")
    return dict(
        code=spec["code"],
        name=spec["name"],
        condition=spec["condition"],
        status_category=spec["status"],
        action=spec["action"],
        priority=spec["priority"],
        response_code=codes[rc] if rc else None,
        tat_type=spec["tat_type"],
        tat_days=spec["tat_days"],
        tat_day_basis=spec["tat_day_basis"],
        tat_cutoff_time=spec.get("tat_cutoff_time"),
        tat_note=spec.get("tat_note", ""),
        rbi_compensation_after_tat=spec.get("rbi_compensation_after_tat", False),
        reason_template=spec.get("reason_template", ""),
        source=spec["source"],
        notes=spec.get("notes", ""),
    )


def _check_existing_rule_set(rule_set: RuleSet, data: ModuleType, report: SeedReport) -> None:
    have = list(rule_set.rules.order_by("evaluation_order").values_list("code", "condition"))
    want = [(r["code"], r["condition"]) for r in data.RULES]
    if have != want:
        report.warnings.append(
            f"Existing {rule_set} differs from the seed data (codes/order/conditions); left unchanged"
        )


@transaction.atomic
def seed_rule_table(data: ModuleType, created_by: str = "seed_rule_sets") -> SeedReport:
    report = SeedReport()
    module, _ = PaymentModule.objects.get_or_create(
        code=data.PAYMENT_MODULE_CODE, defaults={"name": data.PAYMENT_MODULE_CODE}
    )
    codes = _seed_codes(module, data, report)
    _seed_buckets(module, data, report)

    existing = RuleSet.objects.filter(payment_module=module, version_label=data.VERSION_LABEL).first()
    if existing:
        _check_existing_rule_set(existing, data, report)
        return report
    other_active = RuleSet.objects.filter(payment_module=module, status=RuleSetStatus.ACTIVE).first()
    if other_active:
        report.warnings.append(
            f"{other_active} is already active; {data.VERSION_LABEL} not created (would retire it)"
        )
        return report

    rule_set = RuleSet.objects.create(
        payment_module=module,
        version_label=data.VERSION_LABEL,
        effective_from=data.EFFECTIVE_FROM,
        source_document=data.SOURCE_DOCUMENT,
        notes=data.RULE_SET_NOTES,
        created_by=created_by,
    )
    for order, spec in enumerate(data.RULES, start=1):
        rule = Rule.objects.create(rule_set=rule_set, evaluation_order=order, **rule_fields(spec, codes))
        alternatives = [codes[k] for k in spec.get("alternative_codes", [])]
        if alternatives:
            rule.alternative_codes.add(*alternatives)
        for t_order, label, cond, days, basis, source, notes in spec.get("tat_rows", []):
            RuleTat.objects.create(
                rule=rule, evaluation_order=t_order, label=label, condition=cond,
                tat_days=days, tat_day_basis=basis, source=source, notes=notes,
            )
        report.rules_created += 1
    rule_set.activate()
    report.rule_set_created = True
    return report
