"""Synthetic fixtures for the NPCI file tests: cases built as in
exception_management.tests.base, plus the NPCI dispute row (DRC / Timeout /
Adjustment fact with its staging cells) attached to the case's match group as
the presence matrix would. Every RRN, account and amount is made up."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from itertools import count

from core.models import Direction, RegisterType
from exception_management import services
from exception_management.models import ExceptionCase
from exception_management.tests.base import D, CaseTestBase
from file_processing.models import FileTypeConfig, PayloadFormat, UploadedFile
from reconciliation.models import RegisterPresence
from transactions.models import TransactionFact
from transactions.models.staging import STAGING_MODEL_BY_REGISTER_TYPE

_seq = count(1)
REGISTER_BY_LEG = {"DRC": RegisterType.DRC, "TIMEOUT": RegisterType.TIMEOUT, "ADJUSTMENT": RegisterType.ADJUSTMENT}
D2 = D + timedelta(days=1)


class NpciTestBase(CaseTestBase):
    def setUp(self):
        super().setUp()
        self.mk, self.chk = self.maker(), self.checker()
        self._row = 0

    def dispute(self, case: ExceptionCase, leg: str, amount, account: str, *, d: date | None = None,
                txn_date: str | None = None, txn_amount=None, extra_cells: dict | None = None) -> TransactionFact:
        """The NPCI dispute row of `case` for `leg`, dated d (Adjdate for an
        Adjustment; default the case's date). Account in cell 11 (DRC /
        Timeout) or 12 (Adjustment); Txndate in Adjustment cell 8."""
        register_type = REGISTER_BY_LEG[leg]
        d = d or case.business_date
        n = next(_seq)
        config, _ = FileTypeConfig.objects.get_or_create(
            payment_module=self.module, file_type_code=f"NT_{register_type}",
            defaults={"register_type": register_type, "payload_format": PayloadFormat.CSV})
        uf = UploadedFile.objects.create(
            filename=f"{register_type}_{n}.csv", path=f"t/{n}.csv", payment_module=self.module,
            file_type_config=config, business_date=d, declared_format=".csv", size_bytes=1, sha256_hash=f"npci-{n}")
        self._row += 1
        if leg == "ADJUSTMENT":
            cells = {"4": "Credit Adjustment", "5": "SSK", "8": txn_date or f"{case.business_date:%d-%m-%Y}",
                     "12": account}
        else:
            cells = {"9": "SSK", "11": account}
        cells.update(extra_cells or {})
        staging = STAGING_MODEL_BY_REGISTER_TYPE[register_type].objects.create(
            uploaded_file=uf, row_number=self._row, row_reference=f":{self._row}", raw_cells=cells)
        fact = TransactionFact.objects.create(
            payment_module=self.module, source_register_type=register_type, uploaded_file=uf, business_date=d,
            rrn=case.rrn, rrn_valid=True, amount=Decimal(str(amount)), direction=Direction.DEBIT,
            row_reference=str(self._row), staging_row_id=staging.pk,
            txn_amount=Decimal(str(txn_amount)) if txn_amount is not None else None)
        RegisterPresence.objects.update_or_create(
            match_group=case.match_group, leg=leg,
            defaults={"present": True, "transaction_record": fact, "amount": fact.amount, "ssk_side": True})
        return fact

    def drc_case(self, rrn, rule="R01", amount="100", account="'000000001111", d=D):
        case = self.make_case(rrn, rule, d)
        self.dispute(case, "DRC", amount, account)
        return case

    def timeout_case(self, rrn, rule="R05", amount="100", account="000000002222", d=D):
        case = self.make_case(rrn, rule, d)
        self.dispute(case, "TIMEOUT", amount, account)
        return case

    def rrc_case(self, rrn, amount="100", account="9876543210", d=D, adj_date=None, txn_date=None, rule="R06"):
        case = self.make_case(rrn, rule, d)
        self.dispute(case, "ADJUSTMENT", amount, account, d=adj_date or d, txn_date=txn_date)
        return case

    def approve(self, case, *, action_type=None, response_code=None, **evidence):
        services.take(case, self.mk)
        services.submit(case, self.mk, action_type=action_type, response_code=response_code, **evidence)
        return services.approve(case, self.chk)

    def submit_only(self, case):
        services.take(case, self.mk)
        return services.submit(case, self.mk)
