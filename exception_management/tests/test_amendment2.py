"""Amendment 2 (owner 2026-10-11): B1 role members are staff (one-off data
migration), B2 creating a response code goes through maker-checker (seeding
an approved release still works), B3 Pending Investigation cases are never
handled in bulk."""
from __future__ import annotations

import importlib
from io import StringIO

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import transaction
from django.urls import reverse

from core.models import PaymentModule
from exception_management import code_changes, services
from exception_management.actions import ActionType
from exception_management.models import (
    CaseEvent,
    ChangeRequestKind,
    ChangeRequestStatus,
    ProposedAction,
    ResponseCodeChangeRequest,
)
from exception_management.roles import Role
from exception_management.services import BulkRefused, InvalidTransition, MissingInput, RoleRequired
from exception_management.tests.base import CaseTestBase
from rule_engine.models import CodeFlag, ResponseCode, RuleSetImmutableError, seeding_response_codes


class RoleMembersAreStaffMigrationTests(CaseTestBase):  # B1 (one-off for existing members)
    def test_existing_members_become_staff(self):
        model = get_user_model()
        member = model.objects.create_user("legacy_maker", password="x")
        outsider = model.objects.create_user("no_role", password="x")
        other_group = Group.objects.create(name="Something else")
        outsider.groups.add(other_group)
        model.objects.filter(pk=member.pk).update(is_staff=False)
        Group.objects.get(name=Role.MAKER).user_set.add(member)
        model.objects.filter(pk=member.pk).update(is_staff=False)  # as before Amendment 2
        migration = importlib.import_module("exception_management.migrations.0006_role_members_are_staff")
        migration.make_members_staff(django_apps, None)
        member.refresh_from_db()
        outsider.refresh_from_db()
        self.assertTrue(member.is_staff)
        self.assertFalse(outsider.is_staff)


class CodeCreationTests(CaseTestBase):  # B2
    def setUp(self):
        super().setUp()
        self.m, self.chk = self.maker(), self.checker()

    def kw(self, **over):
        return {"payment_module": self.module, "flag": CodeFlag.TCC, "code": "999", "description": "Test only",
                "source": "test", **over}

    def assertBlocked(self, fn):
        with self.assertRaises(RuleSetImmutableError), transaction.atomic():
            fn()

    def test_direct_creation_blocked(self):
        self.assertBlocked(lambda: ResponseCode.objects.create(**self.kw()))
        self.assertBlocked(lambda: ResponseCode(**self.kw()).save())
        self.assertBlocked(lambda: ResponseCode.objects.bulk_create([ResponseCode(**self.kw())]))
        self.assertBlocked(lambda: ResponseCode.objects.get_or_create(**self.kw()))
        self.assertFalse(ResponseCode.objects.filter(description="Test only").exists())

    def test_seeding_an_approved_release_still_creates_codes(self):
        with seeding_response_codes():
            ResponseCode.objects.create(**self.kw())
            ResponseCode.objects.bulk_create([ResponseCode(**self.kw(code="998", description="Test 2"))])
        self.assertEqual(ResponseCode.objects.filter(description__startswith="Test").count(), 2)
        self.assertBlocked(lambda: ResponseCode.objects.create(**self.kw(code="997", description="Test 3")))  # reset

    def test_seed_rule_sets_command_creates_codes_on_a_fresh_module(self):
        imps = PaymentModule.objects.create(code="IMPS", name="IMPS")
        before = ResponseCode.objects.count()
        call_command("seed_rule_sets", stdout=StringIO())  # UPI release: codes exist, nothing new
        self.assertEqual(ResponseCode.objects.count(), before)
        from rule_engine import seeding
        from rule_engine.seed_data import v3_2

        report = seeding.SeedReport()
        codes = seeding._seed_codes(imps, v3_2, report)  # the seeding path, for a module with no codes yet
        self.assertEqual((len(codes), report.codes_created), (len(v3_2.RESPONSE_CODES), len(v3_2.RESPONSE_CODES)))
        self.assertEqual(ResponseCode.objects.filter(payment_module=imps).count(), len(v3_2.RESPONSE_CODES))

    def test_create_request_flow(self):
        with self.assertRaises(RoleRequired):
            code_changes.propose_code_creation(self.module, self.chk, flag="RET", new_code="114",
                                               new_description="Test reason", new_source="NPCI", reason="x")
        req = code_changes.propose_code_creation(self.module, self.m, flag="RET", new_code=" 114 ",
                                                 new_description="Test reason", new_source="NPCI list",
                                                 reason="New RET reason")
        self.assertEqual((req.kind, req.status, req.response_code, req.new_code),
                         (ChangeRequestKind.CREATE, ChangeRequestStatus.PENDING, None, "114"))
        self.assertFalse(ResponseCode.objects.filter(description="Test reason").exists())  # not before approval
        with self.assertRaises(RoleRequired):
            code_changes.approve_code_change(req, self.m)
        code_changes.approve_code_change(req, self.chk)
        rc = ResponseCode.objects.get(payment_module=self.module, flag="RET", description="Test reason")
        req.refresh_from_db()
        self.assertEqual((rc.code, rc.source, req.response_code, req.status), ("114", "NPCI list", rc, "APPROVED"))
        events = list(req.events.values_list("event_type", "response_code_id"))
        self.assertEqual(events, [("PROPOSED", None), ("APPROVED", rc.pk)])
        self.assertEqual(req.events.first().changes["description"], [None, "Test reason"])

    def test_create_validation(self):
        p = dict(payment_module=self.module, user=self.m, reason="x")

        def propose(**kw):
            return code_changes.propose_code_creation(p["payment_module"], p["user"], reason=p["reason"], **kw)

        for kw, msg in (
            (dict(flag="NOPE", new_description="d", new_source="s"), "Unknown flag"),
            (dict(flag="RET", new_description="", new_source="s"), "description"),
            (dict(flag="RET", new_description="d", new_source=" "), "source"),
            (dict(flag="RET", new_code="115", new_description="d", new_source="s"), "already exists"),
            (dict(flag="RET", new_description="Account closed", new_source="s"), "already exists"),
        ):
            with self.subTest(kw=kw), self.assertRaisesRegex(MissingInput, msg):
                propose(**kw)
        propose(flag="RET", new_description="New one", new_source="s")
        with self.assertRaises(InvalidTransition):  # same description already pending
            propose(flag="RET", new_description="New one", new_source="s")

    def test_reject_and_duplicate_at_approval(self):
        a = code_changes.propose_code_creation(self.module, self.m, flag="TCC", new_code="777", new_description="A",
                                               new_source="s", reason="x")
        code_changes.reject_code_change(a, self.chk, reason="not in NPCI list")
        self.assertFalse(ResponseCode.objects.filter(flag="TCC", code="777").exists())
        b = code_changes.propose_code_creation(self.module, self.m, flag="TCC", new_code="777", new_description="B",
                                               new_source="s", reason="x")
        c = code_changes.propose_code_creation(self.module, self.m, flag="TCC", new_code="778", new_description="C",
                                               new_source="s", reason="x")
        code_changes.approve_code_change(b, self.chk)
        ResponseCodeChangeRequest.objects.filter(pk=c.pk).update(new_code="777")  # simulate a clash at approval
        with self.assertRaisesRegex(MissingInput, "already exists"):
            code_changes.approve_code_change(c, self.chk)
        self.assertEqual(ResponseCode.objects.filter(flag="TCC", code="777").count(), 1)

    def test_admin_propose_new_code(self):
        self.client.force_login(self.m)
        url = reverse("admin:exception_management_codechange_propose_new")
        self.assertContains(self.client.get("/admin/exception_management/responsecodechangerequest/"),
                            "Propose a new code")
        self.client.post(url, {"payment_module": self.module.pk, "flag": "TCC", "new_code": "555",
                               "new_description": "Admin test", "new_source": "s", "reason": "r"})
        req = ResponseCodeChangeRequest.objects.get(kind=ChangeRequestKind.CREATE)
        self.client.force_login(self.chk)
        self.assertEqual(self.client.get(url).status_code, 302)  # checker cannot propose
        self.client.post(reverse("admin:exception_management_codechange_decide", args=[req.pk, "approve"]), {"remarks": ""})
        self.assertTrue(ResponseCode.objects.filter(flag="TCC", code="555", description="Admin test").exists())


class PendingInvestigationNeverBulkTests(CaseTestBase):  # B3
    def setUp(self):
        super().setUp()
        self.m, self.chk = self.maker(), self.checker()

    def taken(self, codes, prefix="5"):
        cs = [self.make_case(f"{prefix}0000000000{i}", code) for i, code in enumerate(codes)]
        services.bulk_take(cs, self.m)  # taking in bulk stays allowed
        return cs

    def state(self):
        return (list(self.module.exception_cases.order_by("pk").values_list("status", flat=True)),
                ProposedAction.objects.count(), CaseEvent.objects.count())

    def test_bulk_submit_refused(self):
        for n, codes in enumerate((["R13", "R13"], ["R07c"], ["R01", "R13"]), start=5):
            with self.subTest(codes=codes):
                cs = self.taken(codes, prefix=str(n))
                before = self.state()
                with self.assertRaisesRegex(BulkRefused, "Pending Investigation"):
                    services.bulk_submit(cs, self.m, action_type=ActionType.CLOSE_NO_ACTION, remarks="found")
                self.assertEqual(self.state(), before)

    def test_r06b_refused_too(self):
        cs = self.taken(["R06b"])  # HIGH and Pending Investigation
        with self.assertRaises(BulkRefused):
            services.bulk_submit(cs, self.m)

    def test_bulk_approve_refused(self):
        cs = self.taken(["R13", "R13"])
        for c in cs:  # submitted one by one (allowed)
            services.submit(c, self.m, action_type=ActionType.CLOSE_NO_ACTION, remarks="found next day")
        before = self.state()
        with self.assertRaisesRegex(BulkRefused, "Pending Investigation"):
            services.bulk_approve(cs, self.chk)
        self.assertEqual(self.state(), before)
        services.approve(cs[0], self.chk)  # individually: fine

    def test_other_low_medium_still_bulk(self):
        cs = self.taken(["R01", "R01"])
        self.assertEqual(len(services.bulk_submit(cs, self.m)), 2)
        self.assertEqual(len(services.bulk_approve(cs, self.chk)), 2)

    def test_admin_and_api_refuse(self):
        cs = self.taken(["R03", "R13"])
        self.client.force_login(self.m)
        r = self.client.post("/admin/exception_management/exceptioncase/",
                             {"action": "bulk_submit_action", "_selected_action": [c.pk for c in cs], "apply": "1",
                              "action_type": "CLOSE_NO_ACTION", "remarks": "x"}, follow=True)
        self.assertContains(r, "Pending Investigation cases (R13) are never handled in bulk")
        r = self.client.post("/api/exceptions/cases/bulk/submit/",
                             {"case_ids": [c.pk for c in cs], "action_type": "CLOSE_NO_ACTION", "remarks": "x"},
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Pending Investigation", r.json()["detail"])
        self.assertFalse(ProposedAction.objects.exists())
