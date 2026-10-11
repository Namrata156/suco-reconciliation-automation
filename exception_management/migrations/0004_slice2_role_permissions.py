"""
Slice 2 permissions for the five role groups (created by 0002_roles):
  - code change requests (P3): Maker proposes, Checker approves; everyone
    with case access may view them and their log.
  - user activity log (design section 5): Administrator and Auditor only.
0002's "all project view permissions" was resolved when it ran, so the new
models' view permissions are added here explicitly. Reversing removes only
what this migration added.
"""
from django.contrib.auth.management import create_permissions
from django.db import migrations

CODE_CHANGE_VIEW = [
    "exception_management.view_responsecodechangerequest",
    "exception_management.view_responsecodechangeevent",
]
ADDED = {
    "Administrator": CODE_CHANGE_VIEW + ["core.view_useractivity"],
    "Auditor": CODE_CHANGE_VIEW + ["core.view_useractivity"],
    "Operations User": CODE_CHANGE_VIEW,
    "Maker": CODE_CHANGE_VIEW + ["exception_management.propose_code_change"],
    "Checker": CODE_CHANGE_VIEW + ["exception_management.approve_code_change"],
}


def _perms(apps, names):
    Permission = apps.get_model("auth", "Permission")
    out = []
    for name in names:
        app_label, codename = name.split(".")
        out.append(Permission.objects.get(content_type__app_label=app_label, codename=codename))
    return out


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
        ("core", "0007_useractivity"),
        ("exception_management", "0003_slice2_flags_code_changes"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
