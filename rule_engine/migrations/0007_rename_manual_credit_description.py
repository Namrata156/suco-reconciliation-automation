"""
Owner decision 2026-11-09 (made in code, not through the Settings
maker-checker path): the wording of response codes DRC 103 and RRC 501 is
"Manual credit" (was "Manually credited"; on the dev database it had been
reworded to "Manually Credited" through an approved change request on
2026-10-26).

Wording only. The rows are found by flag + numeric code (as
rule_engine.seeding does), and renamed only when their current wording is
some casing of "Manually credited": a wording someone changed to anything
else is left alone and reported. The note is appended to the code's notes,
the same text as the seed data (rule_engine/seed_data/v3_1.py
MANUAL_CREDIT_NOTE), so a database seeded afresh and one migrated here end
up identical. TCC 103 ("Credited manually post-reconciliation") is not
touched. NPCI files already generated keep their stored text; only files
generated from now on carry "Manual credit".

A database with no response codes yet (a brand-new one, seeded later by
`seed_rule_sets`) is a no-op: the seed data already has the new wording.

Historical models: the live ResponseCode guards (edits only through an
approved change request) do not apply in migrations (see
rule_engine.models.seeding_response_codes).
"""
from django.db import migrations

OLD = "manually credited"
NEW = "Manual credit"
NOTE = "Description renamed from 'Manually credited' to 'Manual credit': owner decision 2026-11-09."
CODES = (("DRC", "103"), ("RRC", "501"))


def forwards(apps, schema_editor):
    ResponseCode = apps.get_model("rule_engine", "ResponseCode")
    for flag, code in CODES:
        for rc in ResponseCode.objects.filter(flag=flag, code=code):
            if rc.description == NEW:
                continue
            if rc.description.strip().lower() != OLD:
                print(f"\n  rule_engine.0007: {flag} {code} (id {rc.pk}) reads {rc.description!r}; left unchanged")
                continue
            if ResponseCode.objects.filter(payment_module_id=rc.payment_module_id, flag=flag,
                                           description=NEW).exclude(pk=rc.pk).exists():
                print(f"\n  rule_engine.0007: another {flag} code is already {NEW!r}; {flag} {code} left unchanged")
                continue
            notes = rc.notes if NOTE in rc.notes else f"{rc.notes} {NOTE}".strip()
            ResponseCode.objects.filter(pk=rc.pk).update(description=NEW, notes=notes)


def backwards(apps, schema_editor):
    ResponseCode = apps.get_model("rule_engine", "ResponseCode")
    for flag, code in CODES:
        for rc in ResponseCode.objects.filter(flag=flag, code=code, description=NEW):
            notes = rc.notes.replace(f" {NOTE}", "").replace(NOTE, "").strip()
            ResponseCode.objects.filter(pk=rc.pk).update(description="Manually credited", notes=notes)


class Migration(migrations.Migration):
    dependencies = [
        ("rule_engine", "0006_seed_rule_table_v3_3"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
