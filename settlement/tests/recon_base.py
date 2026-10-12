"""End-to-end fixture for the Slice 2 settlement tests: real facts (CBS, NPCI
Raw Data with the response code in staging cell 5, Adjustment rows with
Uid / Adjtype / Remitter / Beneficiary cells), the real presence matrix and
the real rule evaluator (rule set v3.2), and NTSL lines built so the S2
self-check passes. The gap rules then run against exactly what production
would see.

The base day (D, 1C + DC1 + DC2) ties exactly:
  A  matched, SSK remitter       CBS deposit 1000 / NPCI DEBIT 1000 00     R09 -> G01 remitter
  B  matched, SSK beneficiary    CBS withdrawal 600 / NPCI CREDIT 600 00   R09 -> G01 beneficiary
  C  on-us                       CBS 50 + 50 / NPCI DEBIT 50 + CREDIT 50   R09 -> G01 rem + G02 ben
  D  NPCI only, success          NPCI DEBIT 20 00                          R12 -> G05 remitter
  E  NPCI only, declined         NPCI DEBIT 30 U9                          R11 (not in NTSL)
  F  CBS only                    CBS deposit 5                             R13 -> G08 (SOP)
  H  Credit Adjustment, SSK rem  Adjustment 35, raised 10:00 IST           G09 -> DC1 +35
  J  DRC confirmation            Adjustment 7, no money                    Z
NTSL 1C: remitter 1070 / 3 (switching fee count 3), beneficiary 650 / 2.
DC1 Net Adjusted Amount credit 35, DC2 0.
SOP: CBS 1055 - 650 = 405; NTSL 1070 - 650 = 420; difference -15 = R12 -20 + R13 +5.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone as dt_timezone
from decimal import Decimal
from io import StringIO
from itertools import count

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase

from core.models import Direction, PaymentModule, RegisterType, SettlementCycle
from exception_management.roles import Role
from file_processing.models import FileTypeConfig, PayloadFormat, UploadedFile
from reconciliation.presence import build_presence_matrix
from rule_engine.evaluator import evaluate_business_date
from settlement.gap_rules import IST
from transactions.models import NtslSettlementLine, TransactionFact
from transactions.models.staging import STAGING_MODEL_BY_REGISTER_TYPE

D = date(2026, 7, 14)
_seq = count(1)


def uid_at(d: date, hh: int, mm: int = 0) -> str:
    """An Adjustment Uid whose last 13 digits are the epoch ms of d hh:mm IST."""
    ms = int(datetime.combine(d, time(hh, mm), tzinfo=IST).astimezone(dt_timezone.utc).timestamp() * 1000)
    return f"4189888{ms}"


def ntsl_lines(remitter="0", remitter_n=0, beneficiary="0", beneficiary_n=0, adjustment="0", *,
               switching_n=None, remitter_rb="0", remitter_rb_n=0, extra=()):
    """A cycle's NTSL lines whose own totals are consistent (self-check PASS).
    adjustment: Net Adjusted Amount, credit - debit (+ = SSK receives)."""
    remitter, beneficiary, adjustment = Decimal(remitter), Decimal(beneficiary), Decimal(adjustment)
    remitter_rb = Decimal(remitter_rb)
    switching_n = remitter_n + remitter_rb_n if switching_n is None else switching_n
    switching = Decimal("0.10") * switching_n
    detail = [
        ("Remitter U3 Approved Transaction Amount", remitter_n, remitter, Decimal(0)),
        ("Remitter U3 Approved NPCI Switching Fee", switching_n, switching, Decimal(0)),
        ("Beneficiary U3 Approved Transaction Amount", beneficiary_n, Decimal(0), beneficiary),
    ]
    if remitter_rb or remitter_rb_n:
        detail.append(("Remitter U3 RB Approved Transaction Amount", remitter_rb_n, remitter_rb, Decimal(0)))
    detail += [(d, n, Decimal(dr), Decimal(cr)) for d, n, dr, cr in extra]
    sub_dr = sum(x[2] for x in detail)
    sub_cr = sum(x[3] for x in detail)
    settle = sub_cr - sub_dr
    final = settle + adjustment
    split = lambda v: (-v, Decimal(0)) if v < 0 else (Decimal(0), v)  # noqa: E731 -> (debit, credit)
    return detail + [
        ("Beneficiary / Remitter Sub Totals", None, sub_dr, sub_cr),
        ("Settlement Amount", None, *split(settle)),
        ("Net Adjusted Amount", None, *split(adjustment)),
        ("Final Settlement Amount", None, *split(final)),
    ]


class SettlementTestBase(TestCase):
    def setUp(self):
        call_command("seed_settlement_cycles", stdout=StringIO())
        call_command("seed_rule_sets", stdout=StringIO())
        call_command("seed_ntsl_category_map", stdout=StringIO())
        self.module = PaymentModule.objects.get(code="UPI")
        self._files: dict = {}
        self._row = 0
        self._users = 0

    # -- users -----------------------------------------------------------------------
    def user(self, *roles):
        self._users += 1
        u = get_user_model().objects.create_user(f"user{self._users}", password="x", is_staff=True,
                                                 email=f"user{self._users}@example.com")
        for role in roles:
            u.groups.add(Group.objects.get(name=role))
        return u

    # -- files and facts ----------------------------------------------------------------
    def cycle(self, code) -> SettlementCycle:
        return SettlementCycle.objects.get(payment_module=self.module, cycle_code=code)

    def file(self, register_type, d=D, *, cycle=None, fresh=False) -> UploadedFile:
        key = (register_type, d, cycle)
        if fresh or key not in self._files:
            n = next(_seq)
            config, _ = FileTypeConfig.objects.get_or_create(
                payment_module=self.module, file_type_code=f"T_{register_type}",
                defaults={"register_type": register_type, "payload_format": PayloadFormat.CSV},
            )
            self._files[key] = UploadedFile.objects.create(
                filename=f"{register_type}_{n}.csv", path=f"t/{n}.csv", payment_module=self.module,
                file_type_config=config, business_date=d, declared_format=".csv", size_bytes=1,
                sha256_hash=f"settle-{n}", settlement_cycle=self.cycle(cycle) if cycle else None,
            )
        return self._files[key]

    def fact(self, register_type, rrn, direction, amount, *, d=D, cells=None, cycle=None, **kw) -> TransactionFact:
        self._row += 1
        f = self.file(register_type, d)
        staging_id = None
        if cells is not None:
            staging_id = STAGING_MODEL_BY_REGISTER_TYPE[register_type].objects.create(
                uploaded_file=f, row_number=self._row, row_reference=f":{self._row}", raw_cells=cells,
            ).id
        return TransactionFact.objects.create(
            payment_module=self.module, source_register_type=register_type, uploaded_file=f, business_date=d,
            rrn=rrn, rrn_valid=kw.pop("rrn_valid", True), amount=Decimal(amount), direction=direction,
            row_reference=str(self._row), staging_row_id=staging_id,
            settlement_cycle=self.cycle(cycle) if cycle else None, **kw,
        )

    def cbs(self, rrn, direction, amount, **kw):
        return self.fact(RegisterType.CBS_GL_EXTRACT, rrn, direction, amount, **kw)

    def npci(self, rrn, direction, amount, code="00", cycle="1C", **kw):
        return self.fact(RegisterType.RAW_DATA, rrn, direction, amount, cells={"4": rrn, "5": code}, cycle=cycle, **kw)

    def adjustment(self, rrn, adjtype, amount, *, raised=(10, 0), remitter="SSK", beneficiary="AXB", d=D,
                   txn_date="13-07-2026"):
        cells = {"2": uid_at(d, *raised) if raised else "not-a-uid", "4": adjtype, "5": remitter, "6": beneficiary,
                 "7": "00", "8": txn_date, "10": f"'{rrn}"}
        return self.fact(RegisterType.ADJUSTMENT, rrn, Direction.CREDIT, amount, d=d, cells=cells)

    def load_ntsl(self, code, lines, d=D) -> UploadedFile:
        uf = self.file(RegisterType.NTSL, d, cycle=code, fresh=True)
        UploadedFile.objects.filter(payment_module=self.module, business_date=d, settlement_cycle__cycle_code=code,
                                    file_type_config__register_type=RegisterType.NTSL).exclude(pk=uf.pk) \
            .update(is_current=False)
        for i, (desc, n, dr, cr) in enumerate(lines, start=6):
            NtslSettlementLine.objects.create(
                payment_module=self.module, uploaded_file=uf, settlement_cycle=self.cycle(code), business_date=d,
                row_reference=f"s:{i}", description=desc, txn_count=n, debit=Decimal(dr), credit=Decimal(cr),
                is_final_settlement=desc == "Final Settlement Amount",
            )
        return uf

    # -- the base day ---------------------------------------------------------------------
    def build_day(self, *, beneficiary_ntsl="650", dc1="35"):
        self.cbs("100000000001", Direction.CREDIT, "1000")
        self.npci("100000000001", Direction.DEBIT, "1000")
        self.cbs("100000000002", Direction.DEBIT, "600")
        self.npci("100000000002", Direction.CREDIT, "600")
        self.cbs("100000000003", Direction.CREDIT, "50")
        self.cbs("100000000003", Direction.DEBIT, "50")
        self.npci("100000000003", Direction.DEBIT, "50")
        self.npci("100000000003", Direction.CREDIT, "50")
        self.npci("100000000004", Direction.DEBIT, "20")
        self.npci("100000000005", Direction.DEBIT, "30", code="U9")
        self.cbs("100000000006", Direction.CREDIT, "5")
        self.adjustment("100000000008", "Credit Adjustment", "35")
        self.adjustment("100000000009", "Debit Reversal Confirmation", "7")
        self.load_all_ntsl(beneficiary_ntsl=beneficiary_ntsl, dc1=dc1)
        self.evaluate()

    def load_all_ntsl(self, *, beneficiary_ntsl="650", dc1="35", d=D):
        self.load_ntsl("1C", ntsl_lines("1070", 3, beneficiary_ntsl, 2), d)
        for code in ("2C", "3C", "4C", "5C", "6C", "7C", "8C", "9C", "10C", "DC2"):
            self.load_ntsl(code, ntsl_lines(), d)
        self.load_ntsl("DC1", ntsl_lines(adjustment=dc1), d)

    def evaluate(self, d=D):
        build_presence_matrix(self.module, d)
        evaluate_business_date(self.module, d, triggered_by="test")

    def reconcile(self, d=D):
        from settlement.reconcile import reconcile_settlement_date

        return reconcile_settlement_date(self.module, d, source="test")
