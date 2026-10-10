"""
Creates the five role groups of the Phase 3 design (section 4, approved
2026-10-10) and gives them model permissions. The workflow service
(exception_management.services) checks group MEMBERSHIP; these permissions
are what the Slice 2 admin screens will rely on.

  Administrator   users and roles, settings (bank holidays), reassign cases,
                  view everything
  Operations User upload files and run reconciliation (as in Phase 1-2),
                  view the queue and results
  Maker           view cases/results; take, submit, add remarks, mark done
  Checker         view cases/results; approve / reject
  Auditor         read-only everything, incl. full case history

No group may change or delete case events, actions or rule results: those
change only through the service / sync / evaluator. Re-applying is safe
(get_or_create, permissions added not replaced); reversing removes the
groups.
"""
from django.contrib.auth.management import create_permissions
from django.db import migrations

PROJECT_APPS = ("core", "file_processing", "transactions", "reconciliation", "rule_engine", "exception_management")

CASE_VIEW = [
    "exception_management.view_exceptioncase",
    "exception_management.view_proposedaction",
    "exception_management.view_caseevent",
]
RESULT_VIEW = [
    "rule_engine.view_ruleresult",
    "rule_engine.view_ruleevaluationrun",
    "rule_engine.view_rule",
    "rule_engine.view_ruleset",
    "rule_engine.view_responsecode",
    "rule_engine.view_ageingbucket",
    "reconciliation.view_transactionmatchgroup",
    "reconciliation.view_registerpresence",
    "transactions.view_transactionfact",
]
ALL_PROJECT_VIEWS = "__all_project_views__"

ROLE_PERMISSIONS = {
    "Administrator": [
        ALL_PROJECT_VIEWS,
        "auth.view_user", "auth.add_user", "auth.change_user",
        "auth.view_group", "auth.change_group",
        "core.add_bankholiday", "core.change_bankholiday",
        "exception_management.reassign_case",
    ],
    "Operations User": CASE_VIEW + RESULT_VIEW + [
        "file_processing.view_uploadedfile", "file_processing.add_uploadedfile", "file_processing.change_uploadedfile",
        "file_processing.view_filetypeconfig", "file_processing.view_filevalidationexception",
        "transactions.view_ntslsettlementline",
    ],
    "Maker": CASE_VIEW + RESULT_VIEW + ["exception_management.work_case"],
    "Checker": CASE_VIEW + RESULT_VIEW + ["exception_management.approve_case"],
    "Auditor": [ALL_PROJECT_VIEWS, "auth.view_user", "auth.view_group"],
}


def create_roles(apps, schema_editor):
    # Model permissions are normally created after migrate (post_migrate);
    # create them now so this migration works on a fresh database too.
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, verbosity=0, apps=apps)
        app_config.models_module = None

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    def perms(names):
        out = []
        for name in names:
            if name == ALL_PROJECT_VIEWS:
                out += list(Permission.objects.filter(
                    content_type__app_label__in=PROJECT_APPS, codename__startswith="view_"
                ))
                continue
            app_label, codename = name.split(".")
            out.append(Permission.objects.get(content_type__app_label=app_label, codename=codename))
        return out

    for group_name, names in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        group.permissions.add(*perms(names))


def remove_roles(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name__in=list(ROLE_PERMISSIONS)).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0006_confirm_bank_holidays_2026"),
        ("file_processing", "0008_adjustment_ssk_party_direction"),
        ("transactions", "0002_ntsl_staging_and_settlement_line"),
        ("reconciliation", "0003_on_us_and_dispute_overlays"),
        ("rule_engine", "0005_seed_rule_table_v3_2"),
        ("exception_management", "0001_initial"),
    ]

    operations = [migrations.RunPython(create_roles, remove_roles)]
