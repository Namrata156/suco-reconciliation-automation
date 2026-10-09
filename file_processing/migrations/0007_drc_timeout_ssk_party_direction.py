"""
Data migration (2026-10-08): switch the existing DRC and TIMEOUT
`FileTypeConfig` rows from a whole-file `constant:` direction rule to the
per-row `ssk_party:` rule (project owner approval 2026-10-08, "option 1").
Both files list cases from both sides, so a constant direction mislabelled
the rows where SSK is the other party -- see seed_file_type_configs.py's
DRC / TIMEOUT entries for the real-data counts.

Update-only and guarded on the old value, like core 0003: it never INSERTs
(a fresh DB gets these rows from `manage.py seed_file_type_configs`, which
already carries the new rule), and it leaves alone any row whose rule was
since changed by hand. On a fresh/test DB this changes nothing.

Only the config changes here. TransactionFact rows already loaded keep
their old direction until `manage.py recompute_ssk_party_directions` is
run (staging for archived dates lives in history tables, so they are not
re-ingested).

Values are frozen here rather than imported from the seed command
(migrations must not depend on live app code);
file_processing/tests/test_ssk_party_direction.py asserts they agree.
"""
from django.db import migrations

NEW_RULE = "ssk_party:remitter_col=9,beneficiary_col=10,bank_code=SSK"
OLD_RULE_BY_FILE_TYPE = {
    "DRC": "constant:DEBIT",
    "TIMEOUT": "constant:CREDIT",
}


def forwards(apps, schema_editor):
    FileTypeConfig = apps.get_model("file_processing", "FileTypeConfig")
    for file_type_code, old_rule in OLD_RULE_BY_FILE_TYPE.items():
        FileTypeConfig.objects.filter(
            file_type_code=file_type_code, direction_derivation_rule=old_rule
        ).update(direction_derivation_rule=NEW_RULE)


def backwards(apps, schema_editor):
    FileTypeConfig = apps.get_model("file_processing", "FileTypeConfig")
    for file_type_code, old_rule in OLD_RULE_BY_FILE_TYPE.items():
        FileTypeConfig.objects.filter(
            file_type_code=file_type_code, direction_derivation_rule=NEW_RULE
        ).update(direction_derivation_rule=old_rule)


class Migration(migrations.Migration):

    dependencies = [
        ("file_processing", "0006_alter_uploadedfile_processing_status"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
