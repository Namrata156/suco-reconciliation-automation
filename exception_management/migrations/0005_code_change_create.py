"""
Amendment 2 B2 (owner 2026-10-11): creating a NEW response code goes through
the code change request too. A request now has a kind (EDIT / CREATE), the
payment module and flag it concerns, and a source for a new code; the code
FK is empty until a CREATE is approved. Existing (EDIT) requests get their
module and flag from their code.
"""
import django.db.models.deletion
from django.db import migrations, models


def fill_module_and_flag(apps, schema_editor):
    Request = apps.get_model("exception_management", "ResponseCodeChangeRequest")
    for req in Request.objects.select_related("response_code"):
        req.payment_module_id = req.response_code.payment_module_id
        req.flag = req.response_code.flag
        req.save(update_fields=["payment_module", "flag"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_useractivity"),
        ("rule_engine", "0005_seed_rule_table_v3_2"),
        ("exception_management", "0004_slice2_role_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="responsecodechangerequest",
            name="kind",
            field=models.CharField(
                choices=[("EDIT", "Edit an existing code"), ("CREATE", "Create a new code")],
                default="EDIT",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="responsecodechangerequest",
            name="payment_module",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.paymentmodule"
            ),
        ),
        migrations.AddField(
            model_name="responsecodechangerequest",
            name="flag",
            field=models.CharField(default="", max_length=20),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="responsecodechangerequest",
            name="new_source",
            field=models.CharField(blank=True, help_text="CREATE only: the new code's citation", max_length=255),
        ),
        migrations.AlterField(
            model_name="responsecodechangerequest",
            name="response_code",
            field=models.ForeignKey(
                blank=True,
                help_text="The code edited; for CREATE, the code created on approval",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="change_requests",
                to="rule_engine.responsecode",
            ),
        ),
        migrations.AlterField(
            model_name="responsecodechangeevent",
            name="response_code",
            field=models.ForeignKey(
                blank=True,
                help_text="NULL for a CREATE proposal (the code does not exist yet)",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="rule_engine.responsecode",
            ),
        ),
        migrations.RunPython(fill_module_and_flag, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="responsecodechangerequest",
            name="payment_module",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.paymentmodule"
            ),
        ),
        migrations.AddConstraint(
            model_name="responsecodechangerequest",
            constraint=models.UniqueConstraint(
                condition=models.Q(("kind", "CREATE"), ("status", "PENDING")),
                fields=("payment_module", "flag", "new_description"),
                name="one_pending_create_per_description",
            ),
        ),
    ]
