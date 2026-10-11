"""
The alert outbox (core.Notification, read-only admin) is visible to the
Administrator and Auditor roles (owner-approved plan, 2026-10-11).
"""
from django.contrib.auth.management import create_permissions
from django.db import migrations

ADDED = {"Administrator": ["core.view_notification"], "Auditor": ["core.view_notification"]}


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
        ("core", "0008_notification"),
        ("exception_management", "0006_role_members_are_staff"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
