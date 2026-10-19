"""
Data migration (Phase 5 Slice 2, 2026-10-15): fill `outer_date_format` --
the strptime format of the date written in each file name -- on the existing
UPI FileTypeConfig rows, so the upload detection can propose each file's
business date from its name (design 4.8, Amendment 1). The field existed
but was never filled. A fresh DB gets the same values from
`manage.py seed_file_type_configs`.

Fill-only: a row whose outer_date_format is already set is left alone. No
pipeline stage reads this field. CBS has no fixed file name and gets none.
"""
from django.db import migrations

FORMATS = {
    "RAW_DATA": "%d%m%y",  # UPIRAWDATAACQSSK140726_1C.csv
    "MERCHANT_RAW_DATA": "%d%m%y",
    "MERCHANT_RAW_DATA_BBP": "%d%m%y",
    "NTSL": "%d%m%y",  # UPI_NTSLSSK140726_DC1.xls
    "DRC": "%Y-%m-%d",  # Debit Reversal Confirmation Report__SSK_2026-07-14.csv
    "TIMEOUT": "%Y-%m-%d",
    "ADJUSTMENT": "%Y-%m-%d",
}


def forwards(apps, schema_editor):
    FileTypeConfig = apps.get_model("file_processing", "FileTypeConfig")
    for code, fmt in FORMATS.items():
        FileTypeConfig.objects.filter(payment_module__code="UPI", file_type_code=code, outer_date_format="").update(
            outer_date_format=fmt
        )


def backwards(apps, schema_editor):
    FileTypeConfig = apps.get_model("file_processing", "FileTypeConfig")
    for code, fmt in FORMATS.items():
        FileTypeConfig.objects.filter(payment_module__code="UPI", file_type_code=code, outer_date_format=fmt).update(
            outer_date_format=""
        )


class Migration(migrations.Migration):
    dependencies = [
        ("file_processing", "0009_uploadedfile_batch_business_date"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
