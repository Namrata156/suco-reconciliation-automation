"""
Data migration: seed UPI NTSL category mapping v1 (ACTIVE) on an EXISTING
database. Acts only when the UPI PaymentModule exists, so fresh/test DBs are
unchanged and get v1 from `manage.py seed_ntsl_category_map` (same pattern
as rule_engine 0002/0005). Create-only: nothing happens if UPI already has
any mapping version. Uses historical models and the frozen plain-data module
settlement/seed_data/ntsl_category_map_v1.py.

Backwards is a no-op (mapping versions are never deleted).
"""
from django.db import migrations

from settlement.seed_data import ntsl_category_map_v1 as data
from settlement.seeding import seed_with


def forwards(apps, schema_editor):
    PaymentModule = apps.get_model("core", "PaymentModule")
    if not PaymentModule.objects.filter(code=data.PAYMENT_MODULE_CODE).exists():
        return
    seed_with(
        {
            "PaymentModule": PaymentModule,
            "NtslCategoryMapVersion": apps.get_model("settlement", "NtslCategoryMapVersion"),
            "NtslCategoryMapEntry": apps.get_model("settlement", "NtslCategoryMapEntry"),
            "NtslMappingEvent": apps.get_model("settlement", "NtslMappingEvent"),
        },
        data,
    )


class Migration(migrations.Migration):
    dependencies = [("settlement", "0001_initial")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
