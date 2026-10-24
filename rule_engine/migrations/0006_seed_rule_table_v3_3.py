"""
Data migration: activate UPI rule set v3.3 and retire v3.2 on an EXISTING
database (docs/phase2_rule_table_v3_approved.txt "v3.3 AMENDMENT", decided
by the project owner 2026-10-23: deadlines count calendar days, same days
and cutoff times).

Same guards as 0005 (v3.2), historical models, rule content from the frozen
plain-data module rule_engine/seed_data/v3_3.py. Acts only when the UPI
PaymentModule and its v3.2 rule set exist (fresh/test DBs get v3.3 from
`manage.py seed_rule_sets`). Create-only:
  - v3.3 already present -> nothing to do;
  - the ACTIVE set is v3.2 (v3_3.SUPERSEDES) -> v3.2 is RETIRED and v3.3
    created ACTIVE, in that order (one active set per module, DB constraint);
  - any other ACTIVE set -> nothing (never retire someone else's version).

Response codes: v3.3 uses exactly v3.2's codes, so each rule takes its
response code and alternative codes from the v3.2 rule with the same code.
No code is looked up by description or created here: a description may
have been reworded through maker-checker since v3.2 (NPCI file design
Amendment 1), and a lookup by the seed wording would create a duplicate.

Results and cases are NOT touched: current results keep their v3.2 due
dates until their date is re-evaluated (`manage.py evaluate_rules`).
rule_engine/tests/test_seed_v3_3.py runs this function and checks it gives
the same rows as the validated command.

Backwards is a no-op (versions are never deleted).
"""
from django.db import migrations
from django.utils import timezone

from rule_engine.seed_data import v3_3 as data

CREATED_BY = "migration rule_engine.0006"


def forwards(apps, schema_editor):
    PaymentModule = apps.get_model("core", "PaymentModule")
    RuleSet = apps.get_model("rule_engine", "RuleSet")
    Rule = apps.get_model("rule_engine", "Rule")
    RuleTat = apps.get_model("rule_engine", "RuleTat")

    module = PaymentModule.objects.filter(code=data.PAYMENT_MODULE_CODE).first()
    if module is None:
        return
    if RuleSet.objects.filter(payment_module=module, version_label=data.VERSION_LABEL).exists():
        return
    active = RuleSet.objects.filter(payment_module=module, status="ACTIVE").first()
    if active is None or active.version_label != data.SUPERSEDES:
        return
    previous = {r.code: r for r in Rule.objects.filter(rule_set=active)}
    if sorted(previous) != sorted(spec["code"] for spec in data.RULES):
        return  # not the v3.2 we copy from; leave it to a person

    now = timezone.now()
    RuleSet.objects.filter(pk=active.pk).update(status="RETIRED", retired_at=now)
    rule_set = RuleSet.objects.create(
        payment_module=module, version_label=data.VERSION_LABEL, status="ACTIVE",
        effective_from=data.EFFECTIVE_FROM, source_document=data.SOURCE_DOCUMENT,
        notes=data.RULE_SET_NOTES, created_by=CREATED_BY, activated_at=now,
    )
    for order, spec in enumerate(data.RULES, start=1):
        old = previous[spec["code"]]
        rule = Rule.objects.create(
            rule_set=rule_set, evaluation_order=order,
            code=spec["code"], name=spec["name"], condition=spec["condition"],
            status_category=spec["status"], action=spec["action"], priority=spec["priority"],
            response_code_id=old.response_code_id,
            tat_type=spec["tat_type"], tat_days=spec["tat_days"], tat_day_basis=spec["tat_day_basis"],
            tat_cutoff_time=spec.get("tat_cutoff_time"), tat_note=spec.get("tat_note", ""),
            rbi_compensation_after_tat=spec.get("rbi_compensation_after_tat", False),
            reason_template=spec.get("reason_template", ""),
            source=spec["source"], notes=spec.get("notes", ""),
        )
        alternatives = list(old.alternative_codes.all())
        if alternatives:
            rule.alternative_codes.add(*alternatives)
        for t_order, label, cond, days, basis, source, notes in spec.get("tat_rows", []):
            RuleTat.objects.create(
                rule=rule, evaluation_order=t_order, label=label, condition=cond,
                tat_days=days, tat_day_basis=basis, source=source, notes=notes,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("rule_engine", "0005_seed_rule_table_v3_2"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
