"""
Data migration: seed UPI rule set v3.1 (ACTIVE), the response/reason code
table and the ageing buckets on an EXISTING database (Phase 2 Step 3,
rule table v3.1 approved by the project owner 2026-10-09).

Guarded like core 0003 / file_processing 0007: it only acts when the UPI
PaymentModule already exists and never creates one, so a fresh/test DB is
unchanged (tests create their own PaymentModule(code="UPI"); see
seed_file_type_configs' docstring). Fresh DBs get the same rows from
`manage.py seed_rule_sets`. Create-only: anything already present is left
alone, so it is safe after the command has run.

Deviation from the "values frozen in the migration" convention, on purpose:
the 24 rules are read from rule_engine/seed_data/v3_1.py instead of being
copied here. That module is itself frozen (it IS published version v3.1;
a rule change is a new module and a new version) and holds only plain data
with no app imports, so the migration still does not depend on live app
code. Historical models are used, so the live models' save() checks don't
run here; rule_engine/tests/test_seed.py runs this function and asserts it
produces exactly what the validated command produces.

Backwards is a no-op: reversing 0001 drops the tables.
"""
from django.db import migrations
from django.utils import timezone

from rule_engine.seed_data import v3_1 as data

CREATED_BY = "migration rule_engine.0002"


def forwards(apps, schema_editor):
    PaymentModule = apps.get_model("core", "PaymentModule")
    ResponseCode = apps.get_model("rule_engine", "ResponseCode")
    AgeingBucket = apps.get_model("rule_engine", "AgeingBucket")
    RuleSet = apps.get_model("rule_engine", "RuleSet")
    Rule = apps.get_model("rule_engine", "Rule")
    RuleTat = apps.get_model("rule_engine", "RuleTat")

    module = PaymentModule.objects.filter(code=data.PAYMENT_MODULE_CODE).first()
    if module is None:
        return

    codes = {}
    for flag, code, description, source, notes in data.RESPONSE_CODES:
        codes[(flag, description)], _ = ResponseCode.objects.get_or_create(
            payment_module=module, flag=flag, description=description,
            defaults=dict(code=code, source=source, notes=notes),
        )
    for order, label, lo, hi, reason in data.AGEING_BUCKETS:
        AgeingBucket.objects.get_or_create(
            payment_module=module, order=order,
            defaults=dict(label=label, min_days=lo, max_days=hi, boundary_reason=reason, source=data.AGEING_SOURCE),
        )

    if RuleSet.objects.filter(payment_module=module, version_label=data.VERSION_LABEL).exists():
        return
    if RuleSet.objects.filter(payment_module=module, status="ACTIVE").exists():
        return  # never retire someone else's active version from a migration

    now = timezone.now()
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
        ("rule_engine", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
