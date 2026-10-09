"""
Data migration (2026-10-08): switch the existing ADJUSTMENT `FileTypeConfig`
row from `constant:CREDIT` to a per-row `ssk_party:` rule with the side ->
direction mapping MIRRORED from DRC/Timeout's (project owner approval in chat
2026-10-08, relayed by the coordinator): Remitter (col 5) SSK -> CREDIT,
Beneficiery (col 6) SSK -> DEBIT. See seed_file_type_configs.py's ADJUSTMENT
entry for the reasoning and real-data counts.

Update-only and guarded on the old value, same as 0007: a fresh DB gets the
rule from `manage.py seed_file_type_configs`, and a row whose rule was since
changed by hand is left alone. Already-loaded TransactionFact rows change
only when `manage.py recompute_ssk_party_directions` is run.

Values frozen here (no app-code import);
file_processing/tests/test_ssk_party_direction.py asserts they match the seed.
"""
from django.db import migrations

FILE_TYPE_CODE = "ADJUSTMENT"
OLD_RULE = "constant:CREDIT"
NEW_RULE = "ssk_party:remitter_col=5,beneficiary_col=6,bank_code=SSK,remitter=CREDIT,beneficiary=DEBIT"


def forwards(apps, schema_editor):
    FileTypeConfig = apps.get_model("file_processing", "FileTypeConfig")
    FileTypeConfig.objects.filter(file_type_code=FILE_TYPE_CODE, direction_derivation_rule=OLD_RULE).update(
        direction_derivation_rule=NEW_RULE
    )


def backwards(apps, schema_editor):
    FileTypeConfig = apps.get_model("file_processing", "FileTypeConfig")
    FileTypeConfig.objects.filter(file_type_code=FILE_TYPE_CODE, direction_derivation_rule=NEW_RULE).update(
        direction_derivation_rule=OLD_RULE
    )


class Migration(migrations.Migration):

    dependencies = [
        ("file_processing", "0007_drc_timeout_ssk_party_direction"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
