"""
Data migration: activate UPI rule set v3.2 and retire v3.1 on an EXISTING
database (docs/phase2_rule_table_v3_approved.txt "v3.2 AMENDMENT", approved
by the project owner 2026-10-10: no cross-day checks; R13 becomes a T+1
review item).

Same guards and approach as 0002: acts only when the UPI PaymentModule
exists (fresh/test DBs are unchanged; they get v3.2 from
`manage.py seed_rule_sets`), reads the frozen plain-data module
rule_engine/seed_data/v3_2.py, uses historical models. Create-only:
  - v3.2 already present -> nothing to do;
  - the ACTIVE set is v3.1 (v3_2.SUPERSEDES) -> v3.1 is RETIRED and v3.2
    created ACTIVE, in that order (one active set per module, DB constraint);
  - any other ACTIVE set -> nothing (never retire someone else's version).
rule_engine/tests/test_seed_v3_2.py runs this function and checks it gives
exactly what the validated command gives.

Backwards is a no-op (versions are never deleted).
"""
from django.db import migrations
from django.utils import timezone

from rule_engine.seed_data import v3_2 as data

CREATED_BY = "migration rule_engine.0005"


def forwards(apps, schema_editor):
    PaymentModule = apps.get_model("core", "PaymentModule")
    ResponseCode = apps.get_model("rule_engine", "ResponseCode")
    RuleSet = apps.get_model("rule_engine", "RuleSet")
    Rule = apps.get_model("rule_engine", "Rule")
    RuleTat = apps.get_model("rule_engine", "RuleTat")

    module = PaymentModule.objects.filter(code=data.PAYMENT_MODULE_CODE).first()
    if module is None:
        return
    if RuleSet.objects.filter(payment_module=module, version_label=data.VERSION_LABEL).exists():
        return
    active = RuleSet.objects.filter(payment_module=module, status="ACTIVE").first()
    if active is not None and active.version_label != data.SUPERSEDES:
        return

    codes = {}
    for flag, code, description, source, notes in data.RESPONSE_CODES:
        codes[(flag, description)], _ = ResponseCode.objects.get_or_create(
            payment_module=module, flag=flag, description=description,
            defaults=dict(code=code, source=source, notes=notes),
        )

    now = timezone.now()
    if active is not None:
        RuleSet.objects.filter(pk=active.pk).update(status="RETIRED", retired_at=now)
    rule_set = RuleSet.objects.create(
        payment_module=module, version_label=data.VERSION_LABEL, status="ACTIVE",
        effective_from=data.EFFECTIVE_FROM, source_document=data.SOURCE_DOCUMENT,
        notes=data.RULE_SET_NOTES, created_by=CREATED_BY, activated_at=now,
    )
    for order, spec in enumerate(data.RULES, start=1):
        rc = spec.get("response_code")
        rule = Rule.objects.create(
            rule_set=rule_set, evaluation_order=order,
            code=spec["code"], name=spec["name"], condition=spec["condition"],
            status_category=spec["status"], action=spec["action"], priority=spec["priority"],
            response_code=codes[rc] if rc else None,
            tat_type=spec["tat_type"], tat_days=spec["tat_days"], tat_day_basis=spec["tat_day_basis"],
            tat_cutoff_time=spec.get("tat_cutoff_time"), tat_note=spec.get("tat_note", ""),
            rbi_compensation_after_tat=spec.get("rbi_compensation_after_tat", False),
            reason_template=spec.get("reason_template", ""),
            source=spec["source"], notes=spec.get("notes", ""),
        )
        alternatives = [codes[k] for k in spec.get("alternative_codes", [])]
        if alternatives:
            rule.alternative_codes.add(*alternatives)
        for t_order, label, cond, days, basis, source, notes in spec.get("tat_rows", []):
            RuleTat.objects.create(
                rule=rule, evaluation_order=t_order, label=label, condition=cond,
                tat_days=days, tat_day_basis=basis, source=source, notes=notes,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("rule_engine", "0004_remove_next_day_recheck"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
