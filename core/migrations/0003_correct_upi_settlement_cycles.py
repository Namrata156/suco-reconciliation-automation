"""
Data migration: correct existing UPI `SettlementCycle` rows to the NPCI
circular's "Annexure I -- Revised Settlement Cycles" (correction approved
by the project owner 2026-09-27), and move every row's `cycle_type` from
the old FINANCIAL / NON_FINANCIAL values to AUTH / DISPUTE.

Update-only by design: it never INSERTs. The UPI cycle rows in existing
DBs were created ad hoc (no seed/migration existed for them), and a fresh
DB gets them from `manage.py seed_settlement_cycles` instead -- inserting
here would put a UPI `PaymentModule` into the test DB baseline and collide
with every test that creates its own `PaymentModule(code="UPI")` (same
reason `seed_file_type_configs` is a command, see its docstring). On a
fresh/test DB this migration therefore changes nothing.

The values are deliberately frozen here rather than imported from the seed
command (migrations must not depend on live app code);
core/tests/test_settlement_cycles.py asserts the two tables agree.
"""
from datetime import time

from django.db import migrations

# cycle_code -> (cycle_type, start_time, end_time, rtgs_settlement_time)
ANNEXURE_I_UPI_CYCLES = {
    "1C": ("AUTH", time(21, 0), time(0, 0), time(4, 0)),
    "2C": ("AUTH", time(0, 0), time(5, 0), time(7, 0)),
    "3C": ("AUTH", time(5, 0), time(7, 0), time(9, 0)),
    "4C": ("AUTH", time(7, 0), time(9, 0), time(11, 0)),
    "5C": ("AUTH", time(9, 0), time(11, 0), time(13, 0)),
    "6C": ("AUTH", time(11, 0), time(13, 0), time(15, 0)),
    "7C": ("AUTH", time(13, 0), time(15, 0), time(17, 0)),
    "8C": ("AUTH", time(15, 0), time(17, 0), time(19, 0)),
    "9C": ("AUTH", time(17, 0), time(19, 0), time(21, 0)),
    "10C": ("AUTH", time(19, 0), time(21, 0), time(23, 0)),
    "DC1": ("DISPUTE", time(0, 0), time(16, 0), time(17, 0)),
    "DC2": ("DISPUTE", time(16, 0), time(0, 0), time(4, 0)),
}

_TYPE_FORWARD = {"FINANCIAL": "AUTH", "NON_FINANCIAL": "DISPUTE"}
_TYPE_BACKWARD = {v: k for k, v in _TYPE_FORWARD.items()}


def forwards(apps, schema_editor):
    SettlementCycle = apps.get_model("core", "SettlementCycle")
    for old, new in _TYPE_FORWARD.items():
        SettlementCycle.objects.filter(cycle_type=old).update(cycle_type=new)
    for code, (cycle_type, start, end, rtgs) in ANNEXURE_I_UPI_CYCLES.items():
        SettlementCycle.objects.filter(payment_module__code="UPI", cycle_code=code).update(
            cycle_type=cycle_type, start_time=start, end_time=end, rtgs_settlement_time=rtgs
        )


def backwards(apps, schema_editor):
    # Only the cycle_type rename is reversed (0002's reverse restores the old
    # choices). The pre-correction 1C-10C times were placeholders, not real
    # data, so they are not restored; rtgs_settlement_time is dropped by
    # reversing 0002.
    SettlementCycle = apps.get_model("core", "SettlementCycle")
    for new, old in _TYPE_BACKWARD.items():
        SettlementCycle.objects.filter(cycle_type=new).update(cycle_type=old)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_settlementcycle_rtgs_time_and_auth_dispute_types"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
