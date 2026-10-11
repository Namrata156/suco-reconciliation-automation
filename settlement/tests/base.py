"""Synthetic NTSL fixtures for the settlement tests: the UPI module, its 12
cycles, mapping v1 (seeded), and NtslSettlementLine rows per cycle built
directly (as the Phase 1 loader would leave them)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import StringIO
from itertools import count

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase

from core.models import PaymentModule, RegisterType, SettlementCycle
from exception_management.roles import Role
from file_processing.models import FileTypeConfig, PayloadFormat, UploadedFile
from transactions.models import NtslSettlementLine

D = date(2026, 7, 14)
_seq = count(1)

# A balanced AUTH cycle: debits 1,002.43, credits 601.40 -> SSK pays 401.03.
AUTH_LINES = [
    ("Remitter U3 Approved Fee", 3, "1.00", "0"),
    ("Remitter U3 Approved Fee Gst", 3, "0.18", "0"),
    ("U3 Approved Payer PSP Fee -Paid", 3, "0.90", "0"),
    ("Remitter U3 Approved Transaction Amount", 3, "1000.00", "0"),
    ("Remitter U3 Approved NPCI Switching Fee", 3, "0.30", "0"),
    ("Remitter U3 Approved NPCI Switching Fee Gst", 0, "0.05", "0"),
    ("Beneficiary U3 Approved Fee", 2, "0", "0.80"),
    ("Beneficiary U3 Approved Transaction Amount", 2, "0", "600.00"),
    ("Switching Fee Rebate for U2 transactions", 0, "0", "0.10"),
    ("Remitter Intent-SI U2 Approved Fee", 1, "0", "0.50"),
    ("Settlement Charges", None, "0", "0"),
    ("Beneficiary / Remitter Sub Totals", None, "1002.43", "601.40"),
    ("Settlement Amount", None, "401.03", "0"),
    ("Net Adjusted Amount", None, "0", "0"),
    ("NRP Raise Fees", 0, "0", "0"),
    ("Final Settlement Amount", None, "401.03", "0"),
]

# A dispute cycle (DC1/DC2): only Net Adjusted Amount moves money.
DC_LINES = [
    ("Settlement Charges", None, "0", "0"),
    ("Beneficiary / Remitter Sub Totals", None, "0", "0"),
    ("Settlement Amount", None, "0", "0"),
    ("Net Adjusted Amount", None, "0", "435.00"),
    ("Pre-Arbitration Deemed Acceptance Penalty", None, "0", "0"),
    ("Final Settlement Amount", None, "0", "435.00"),
]


def replace(lines, description, debit=None, credit=None):
    out = []
    for d, n, dr, cr in lines:
        if d == description:
            dr = dr if debit is None else debit
            cr = cr if credit is None else credit
        out.append((d, n, dr, cr))
    return out


class NtslTestBase(TestCase):
    def setUp(self):
        call_command("seed_settlement_cycles", stdout=StringIO())
        call_command("seed_ntsl_category_map", stdout=StringIO())
        self.module = PaymentModule.objects.get(code="UPI")
        self.config = FileTypeConfig.objects.create(
            payment_module=self.module, file_type_code="NTSL_TEST", register_type=RegisterType.NTSL,
            payload_format=PayloadFormat.SPREADSHEETML_XML,
        )
        self._users = 0

    def cycle(self, code) -> SettlementCycle:
        return SettlementCycle.objects.get(payment_module=self.module, cycle_code=code)

    def load_cycle(self, code, lines, d=D, *, final_flag=True, is_current=True) -> UploadedFile:
        n = next(_seq)
        uf = UploadedFile.objects.create(
            filename=f"UPI_NTSL_{code}_{n}.xls", path=f"test/{n}.xls", payment_module=self.module,
            file_type_config=self.config, business_date=d, declared_format=".xls", size_bytes=1,
            sha256_hash=f"ntsl-test-{n}", settlement_cycle=self.cycle(code), is_current=is_current,
        )
        for i, (desc, cnt, dr, cr) in enumerate(lines, start=6):
            NtslSettlementLine.objects.create(
                payment_module=self.module, uploaded_file=uf, settlement_cycle=self.cycle(code),
                business_date=d, row_reference=f"sheet1:{i}", description=desc, txn_count=cnt,
                debit=Decimal(dr), credit=Decimal(cr),
                is_final_settlement=final_flag and desc == "Final Settlement Amount",
            )
        return uf

    def user(self, *roles):
        self._users += 1
        u = get_user_model().objects.create_user(f"user{self._users}", password="x", is_staff=True)
        for role in roles:
            u.groups.add(Group.objects.get(name=role))
        return u

    def maker(self):
        return self.user(Role.MAKER)

    def checker(self):
        return self.user(Role.CHECKER)
