"""
The operations log (core.OperationLog, read-only admin; Phase 5 Slice 2) is
visible to the Administrator and Auditor roles, like the user activity log
(design 3.5: "Audit / user activity" R for Administrator and Auditor).
"""
from django.contrib.auth.management import create_permissions
from django.db import migrations

ADDED = {"Administrator": ["core.view_operationlog"], "Auditor": ["core.view_operationlog"]}


def _perms(apps, names):
    Permission = apps.get_model("auth", "Permission")
    return [Permission.objects.get(content_type__app_label=n.split(".")[0], codename=n.split(".")[1]) for n in names]


def forwards(apps, schema_editor):
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, verbosity=0, apps=apps)
        app_config.models_module = None
    Group = apps.get_model("auth", "Group")
    for name, perms in ADDED.items():
        Group.objects.get(name=name).permissions.add(*_perms(apps, perms))


def backwards(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for name, perms in ADDED.items():
        group = Group.objects.filter(name=name).first()
        if group is not None:
            group.permissions.remove(*_perms(apps, perms))


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_operationlog"),
        ("exception_management", "0008_settlement_case_type"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
