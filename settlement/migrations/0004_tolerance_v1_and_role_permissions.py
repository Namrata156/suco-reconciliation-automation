"""
Phase 4 Slice 2 data migration.

1. Settlement tolerance v1 (Q4, owner 2026-10-11): Rs 0.00 / 0 txn, ACTIVE,
   logged SEEDED, for the UPI module on an EXISTING database (fresh/test DBs
   get it on first use, settlement.tolerance.active_tolerance). Create-only.

2. Role permissions on the settlement tables (Slice 1 left them
   superuser-only). Every one of the five roles may VIEW the mapping, the
   self-check results, the settlement results, the tolerance and their logs.
   Writes go only through the services (settlement.mapping_changes,
   settlement.tolerance, settlement.reconcile / cases); the admin screens
   are read-only. Tolerance changes: Administrator proposes, Checker
   approves (the custom permissions mirror that; the service checks the
   role groups).

Backwards removes only the permissions added here; tolerance versions are
never deleted.
"""
from django.contrib.auth.management import create_permissions
from django.db import migrations

from settlement.tolerance import seed_with

SETTLEMENT_VIEW = [
    "settlement.view_ntslcategorymapversion",
    "settlement.view_ntslcategorymapentry",
    "settlement.view_ntslmappingevent",
    "settlement.view_ntslcyclecheck",
    "settlement.view_settlementresult",
    "settlement.view_settlementcycleresult",
    "settlement.view_settlementtolerance",
    "settlement.view_settlementtoleranceevent",
    "settlement.view_settlementcase",
]
ADDED = {
    "Administrator": SETTLEMENT_VIEW + ["settlement.propose_tolerance_change"],
    "Operations User": SETTLEMENT_VIEW,
    "Maker": SETTLEMENT_VIEW,
    "Checker": SETTLEMENT_VIEW + ["settlement.approve_tolerance_change"],
    "Auditor": SETTLEMENT_VIEW,
}


def _perms(apps, names):
    Permission = apps.get_model("auth", "Permission")
    return [Permission.objects.get(content_type__app_label=n.split(".")[0], codename=n.split(".")[1]) for n in names]


def forwards(apps, schema_editor):
    PaymentModule = apps.get_model("core", "PaymentModule")
    module = PaymentModule.objects.filter(code="UPI").first()
    if module is not None:
        seed_with({
            "SettlementTolerance": apps.get_model("settlement", "SettlementTolerance"),
            "SettlementToleranceEvent": apps.get_model("settlement", "SettlementToleranceEvent"),
        }, module)

    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, verbosity=0, apps=apps)
        app_config.models_module = None
    Group = apps.get_model("auth", "Group")
    for name, perms in ADDED.items():
        group = Group.objects.filter(name=name).first()
        if group is not None:
            group.permissions.add(*_perms(apps, perms))


def backwards(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for name, perms in ADDED.items():
        group = Group.objects.filter(name=name).first()
        if group is not None:
            group.permissions.remove(*_perms(apps, perms))


class Migration(migrations.Migration):
    dependencies = [
        ("exception_management", "0008_settlement_case_type"),
        ("settlement", "0003_settlement_results_cases_tolerance"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
