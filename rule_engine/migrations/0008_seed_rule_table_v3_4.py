"""
Data migration: activate UPI rule set v3.4 and retire v3.3 on an EXISTING
database (owner decision 2026-11-11, CBS credit file plan: new rule R07r,
Refund Reversal Confirmation for an SSK-remitter payment, credited through
the rev file; R06 replies RRC 501 first). Rule content from the frozen
plain-data module rule_engine/seed_data/v3_4.py.

Same guards as 0006 (v3.3), historical models. Acts only when the UPI
PaymentModule and its v3.3 rule set exist (fresh/test DBs get v3.4 from
`manage.py seed_rule_sets`). Create-only:
  - v3.4 already present -> nothing to do;
  - the ACTIVE set is v3.3 (v3_4.SUPERSEDES) with exactly v3.4's rules minus
    the new ones -> v3.3 is RETIRED and v3.4 created ACTIVE, in that order
    (one active set per module, DB constraint);
  - anything else -> nothing (never retire someone else's version).

Response codes: each rule v3.3 already had takes its response code and
alternative codes from the v3.3 rule with the same code (no lookup by
description, see 0006). The new rule R07r has no response code (no SSKUPI
reply), so no code is looked up or created here.

Results and cases are NOT touched: a date's Refund Reversal Confirmations
become R07r cases when the date is next re-run (`POST dates/<d>/run/` or
`manage.py evaluate_rules`). rule_engine/tests/test_seed_v3_4.py runs this
function and checks it gives the same rows as the validated command.

Backwards is a no-op (versions are never deleted).
"""
from django.db import migrations
from django.utils import timezone

from rule_engine.seed_data import v3_4 as data

CREATED_BY = "migration rule_engine.0008"
NEW_RULES = ("R07r",)


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
    if sorted(previous) != sorted(spec["code"] for spec in data.RULES if spec["code"] not in NEW_RULES):
        return  # not the v3.3 we copy from; leave it to a person

    now = timezone.now()
    RuleSet.objects.filter(pk=active.pk).update(status="RETIRED", retired_at=now)
    rule_set = RuleSet.objects.create(
        payment_module=module, version_label=data.VERSION_LABEL, status="ACTIVE",
        effective_from=data.EFFECTIVE_FROM, source_document=data.SOURCE_DOCUMENT,
        notes=data.RULE_SET_NOTES, created_by=CREATED_BY, activated_at=now,
    )
    for order, spec in enumerate(data.RULES, start=1):
        old = previous.get(spec["code"])
        rule = Rule.objects.create(
            rule_set=rule_set, evaluation_order=order,
            code=spec["code"], name=spec["name"], condition=spec["condition"],
            status_category=spec["status"], action=spec["action"], priority=spec["priority"],
            response_code_id=old.response_code_id if old is not None else None,
            tat_type=spec["tat_type"], tat_days=spec["tat_days"], tat_day_basis=spec["tat_day_basis"],
            tat_cutoff_time=spec.get("tat_cutoff_time"), tat_note=spec.get("tat_note", ""),
            rbi_compensation_after_tat=spec.get("rbi_compensation_after_tat", False),
            reason_template=spec.get("reason_template", ""),
            source=spec["source"], notes=spec.get("notes", ""),
        )
        alternatives = list(old.alternative_codes.all()) if old is not None else []
        if alternatives:
            rule.alternative_codes.add(*alternatives)
        for t_order, label, cond, days, basis, source, notes in spec.get("tat_rows", []):
            RuleTat.objects.create(
                rule=rule, evaluation_order=t_order, label=label, condition=cond,
                tat_days=days, tat_day_basis=basis, source=source, notes=notes,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("rule_engine", "0007_rename_manual_credit_description"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
