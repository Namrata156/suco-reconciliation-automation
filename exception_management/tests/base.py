"""Synthetic-data builders for the exception_management tests. RuleResults
are written directly (as the evaluator would) against the seeded v3.2 rule
set, so each test states exactly which rule fired for which RRN; one test
module also runs the real evaluator end to end."""
from __future__ import annotations

from datetime import date, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.models import PaymentModule
from exception_management.models import ExceptionCase
from exception_management.roles import Role
from exception_management.sync import sync_cases_for_date
from reconciliation.models import TransactionMatchGroup
from rule_engine.models import RuleEvaluationRun, RuleResult, RuleSet, RuleSetStatus, TatType

D = date(2026, 7, 14)  # Tuesday
AUTO = object()


class CaseTestBase(TestCase):
    def setUp(self):
        call_command("seed_rule_sets", stdout=StringIO())
        self.module = PaymentModule.objects.get(code="UPI")
        self.rule_set = RuleSet.objects.get(payment_module=self.module, status=RuleSetStatus.ACTIVE)
        self.rules = {r.code: r for r in self.rule_set.rules.all()}
        self._runs: dict = {}
        self._users = 0

    # -- users -----------------------------------------------------------------
    def user(self, *roles, superuser=False, active=True):
        self._users += 1
        model = get_user_model()
        u = model.objects.create_user(
            f"user{self._users}", password="x", is_superuser=superuser, is_active=active, is_staff=True
        )
        for role in roles:
            u.groups.add(Group.objects.get(name=role))
        return u

    def maker(self):
        return self.user(Role.MAKER)

    def checker(self):
        return self.user(Role.CHECKER)

    # -- results ---------------------------------------------------------------
    def new_run(self, d=D):
        run = RuleEvaluationRun.objects.create(
            payment_module=self.module, business_date=d, rule_set=self.rule_set, started_at=timezone.now()
        )
        self._runs[d] = run
        return run

    def result(self, rrn, rule_code, d=D, *, due=AUTO, cutoff=None, run=None, tat_unresolved=False) -> RuleResult:
        rule = self.rules[rule_code]
        group, _ = TransactionMatchGroup.objects.get_or_create(payment_module=self.module, rrn=rrn, business_date=d)
        if due is AUTO:
            due = d + timedelta(days=1) if rule.tat_type != TatType.NONE else None
        return RuleResult.objects.create(
            payment_module=self.module,
            match_group=group,
            business_date=d,
            rrn=rrn,
            run=run or self._runs.get(d) or self.new_run(d),
            rule_set=self.rule_set,
            rule=rule,
            rule_code=rule.code,
            status_category=rule.status_category,
            priority=rule.priority,
            action=rule.action,
            response_code=rule.response_code,
            tat_start_date=d,
            due_date=due,
            due_cutoff_time=cutoff,
            tat_unresolved=tat_unresolved,
            facts={},
            evaluated_at=timezone.now(),
        )

    def rerun(self, mapping: dict, d=D, **result_kw):
        """Simulates a re-evaluation of d: every current result superseded,
        new ones created for {rrn: rule_code}; an RRN missing from mapping
        (or mapped to None) gets no current result."""
        RuleResult.objects.filter(business_date=d, is_superseded=False).update(
            is_superseded=True, superseded_at=timezone.now()
        )
        run = self.new_run(d)
        for rrn, code in mapping.items():
            if code is not None:
                kw = result_kw.get(rrn, {})
                self.result(rrn, code, d, run=run, **kw)

    def sync(self, d=D):
        return sync_cases_for_date(self.module, d, source="test")

    def case(self, rrn, d=D) -> ExceptionCase:
        return ExceptionCase.objects.get(rrn=rrn, business_date=d)

    def make_case(self, rrn="100000000001", rule_code="R01", d=D) -> ExceptionCase:
        self.result(rrn, rule_code, d)
        self.sync(d)
        return self.case(rrn, d)
