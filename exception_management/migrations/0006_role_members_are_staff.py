"""
Amendment 2 B1 (owner 2026-10-11): every member of the five role groups has
Django staff status, so they can open the admin (the only interface until
Phase 5). From now on exception_management.signals keeps this true when a
user is added to a role group or saved; this one-off covers users who were
already members. Reversing does nothing (staff status is never removed
automatically).
"""
from django.db import migrations

ROLE_GROUPS = ("Administrator", "Operations User", "Maker", "Checker", "Auditor")


def make_members_staff(apps, schema_editor):
    User = apps.get_model("auth", "User")  # the project uses Django's default user model
    User.objects.filter(groups__name__in=ROLE_GROUPS, is_staff=False).update(is_staff=True)


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("exception_management", "0005_code_change_create"),
    ]

    operations = [migrations.RunPython(make_members_staff, migrations.RunPython.noop)]
