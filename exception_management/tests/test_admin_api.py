"""Admin screens and the DRF API: pages load per role, Auditor read-only,
transitions only through the services, bulk refusal in the admin, code
change screens, API filters / detail / transitions / bulk, session + CSRF."""
from __future__ import annotations

from datetime import timedelta

from django.test import Client
from django.urls import reverse

from exception_management import services
from exception_management.models import CaseStatus, ExceptionCase, ProposedAction, ResponseCodeChangeRequest
from exception_management.roles import Role
from exception_management.tests.base import D, CaseTestBase
from rule_engine.models import CodeFlag, ResponseCode

QUEUE = "/admin/exception_management/exceptioncase/"


def transition_url(case, name):
    return reverse("admin:exception_management_exceptioncase_transition", args=[case.pk, name])


class AdminTests(CaseTestBase):
    def setUp(self):
        super().setUp()
        self.c1 = self.make_case("100000000001", "R01")
        self.c2 = self.make_case("100000000002", "R05")  # HIGH
        self.result("100000000003", "R13", D)
        self.result("100000000003", "R12", D - timedelta(days=1))
        self.sync(D)

    def login(self, *roles, **kw):
        u = self.user(*roles, **kw)
        self.client.force_login(u)
        return u

    def test_queue_and_detail_load_for_every_role(self):
        for role in Role.ALL:
            with self.subTest(role=role):
                self.login(role)
                r = self.client.get(QUEUE)
                self.assertEqual(r.status_code, 200)
                self.assertContains(r, "100000000003")
                self.assertContains(r, "R12 ON D-1")  # P5 hint flag
                for q in ("?queue=all", "?overdue=yes", "?mine=assigned", "?mine=approval", "?flag=previous_day_r12",
                          f"?business_date={D}", "?priority__exact=HIGH", "?rule_code=R05", "?q=100000000001"):
                    self.assertEqual(self.client.get(QUEUE + q).status_code, 200, q)
                r = self.client.get(f"{QUEUE}{self.c2.pk}/change/")
                self.assertEqual(r.status_code, 200)
                self.assertContains(r, "Dispute rows for this RRN")

    def test_queue_order_high_first(self):
        self.login(Role.AUDITOR)
        r = self.client.get(QUEUE)
        cases = list(r.context["cl"].result_list)
        self.assertEqual(cases[0].rrn, "100000000002")  # R05 HIGH

    def test_staff_without_role_is_denied(self):
        self.login()
        self.assertEqual(self.client.get(QUEUE).status_code, 403)

    def test_auditor_is_read_only(self):
        self.login(Role.AUDITOR)
        r = self.client.get(f"{QUEUE}{self.c1.pk}/change/")
        self.assertNotContains(r, "exception-transition-")
        r = self.client.post(transition_url(self.c1, "take"), {})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.case(self.c1.rrn).status, CaseStatus.OPEN)
        r = self.client.post(QUEUE, {"action": "bulk_take_action", "_selected_action": [self.c1.pk], "apply": "1"})
        self.assertEqual(self.case(self.c1.rrn).status, CaseStatus.OPEN)
        for url in (f"{QUEUE}add/", f"{QUEUE}{self.c1.pk}/delete/",
                    "/admin/exception_management/caseevent/add/"):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_maker_and_checker_flow_through_the_admin(self):
        maker = self.login(Role.MAKER)
        r = self.client.get(f"{QUEUE}{self.c1.pk}/change/")
        self.assertContains(r, "exception-transition-take")
        self.assertNotContains(r, "exception-transition-approve")
        self.assertEqual(self.client.get(transition_url(self.c1, "take")).status_code, 200)  # GET: confirm form only
        self.assertEqual(self.case(self.c1.rrn).status, CaseStatus.OPEN)
        self.client.post(transition_url(self.c1, "take"), {"remarks": ""})
        self.assertEqual(self.case(self.c1.rrn).assigned_to, maker)
        r = self.client.get(transition_url(self.c1, "submit"))
        self.assertContains(r, "DRC_102")
        self.client.post(transition_url(self.c1, "submit"), {"action_type": "DRC_102", "remarks": "portal"})
        self.assertEqual(self.case(self.c1.rrn).status, CaseStatus.PENDING_APPROVAL)
        self.login(Role.CHECKER)
        r = self.client.post(transition_url(self.c1, "reject"), {"reason": ""})
        self.assertEqual(r.status_code, 200)  # form error, nothing applied
        self.assertEqual(self.case(self.c1.rrn).status, CaseStatus.PENDING_APPROVAL)
        self.client.post(transition_url(self.c1, "approve"), {"remarks": "ok"})
        self.assertEqual(self.case(self.c1.rrn).status, CaseStatus.APPROVED)

    def test_service_error_shown_on_form(self):
        self.login(Role.MAKER)
        c = self.make_case("100000000009", "R13")
        self.client.post(transition_url(c, "take"), {})
        r = self.client.post(transition_url(c, "submit"), {"action_type": "CLOSE_NO_ACTION", "remarks": ""})
        self.assertContains(r, "needs remarks")
        self.assertFalse(ProposedAction.objects.filter(case=c).exists())

    def test_bulk_submit_refused_with_high_nothing_applied(self):
        maker = self.login(Role.MAKER)
        services.bulk_take([self.c1, self.c2], maker)
        r = self.client.post(QUEUE, {"action": "bulk_submit_action", "_selected_action": [self.c1.pk, self.c2.pk],
                                     "apply": "1", "action_type": "CLOSE_NO_ACTION", "remarks": "x"}, follow=True)
        self.assertContains(r, "refused, nothing was applied")
        self.assertFalse(ProposedAction.objects.exists())
        r = self.client.post(QUEUE, {"action": "bulk_submit_action", "_selected_action": [self.c1.pk]})
        self.assertContains(r, "Apply to all")  # intermediate form
        r = self.client.post(QUEUE, {"action": "bulk_submit_action", "_selected_action": [self.c1.pk], "apply": "1",
                                     "action_type": "", "remarks": ""}, follow=True)
        self.assertContains(r, "applied to 1 case")
        self.assertEqual(self.case(self.c1.rrn).status, CaseStatus.PENDING_APPROVAL)

    def test_bulk_actions_shown_by_role(self):
        self.login(Role.CHECKER)
        actions = self.client.get(QUEUE).context["action_form"].fields["action"].choices
        names = {n for n, _ in actions}
        self.assertIn("bulk_approve_action", names)
        self.assertNotIn("bulk_submit_action", names)

    def test_event_and_action_admin_read_only(self):
        self.login(Role.AUDITOR)
        for url in ("/admin/exception_management/caseevent/", "/admin/exception_management/proposedaction/",
                    "/admin/exception_management/responsecodechangerequest/",
                    "/admin/exception_management/responsecodechangeevent/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_code_change_screens(self):
        closed = ResponseCode.objects.get(payment_module=self.module, flag=CodeFlag.RET, description="Account closed")
        self.login(Role.CHECKER)
        r = self.client.get(reverse("admin:exception_management_codechange_propose"))
        self.assertEqual(r.status_code, 302)  # checker cannot propose
        self.login(Role.MAKER)
        r = self.client.get("/admin/exception_management/responsecodechangerequest/")
        self.assertContains(r, "Propose a code change")
        self.client.post(reverse("admin:exception_management_codechange_propose"),
                         {"response_code": closed.pk, "new_code": "114", "reason": "NPCI list"})
        req = ResponseCodeChangeRequest.objects.get()
        url = reverse("admin:exception_management_codechange_decide", args=[req.pk, "approve"])
        self.client.post(url, {})
        closed.refresh_from_db()
        self.assertIsNone(closed.code)  # maker cannot approve
        self.login(Role.CHECKER)
        self.assertContains(self.client.get(f"/admin/exception_management/responsecodechangerequest/{req.pk}/change/"),
                            "Approve and apply")
        self.client.post(url, {"remarks": ""})
        closed.refresh_from_db()
        self.assertEqual(closed.code, "114")


class ApiTests(CaseTestBase):
    LIST = "/api/exceptions/cases/"

    def setUp(self):
        super().setUp()
        self.c1 = self.make_case("100000000001", "R01")
        self.c2 = self.make_case("100000000002", "R05")

    def login(self, *roles):
        u = self.user(*roles)
        self.client.force_login(u)
        return u

    def test_access(self):
        self.assertEqual(self.client.get(self.LIST).status_code, 403)  # anonymous
        self.login()
        self.assertEqual(self.client.get(self.LIST).status_code, 403)  # no role
        self.login(Role.AUDITOR)
        r = self.client.get(self.LIST)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["count"], 2)
        self.assertEqual(r.json()["results"][0]["rrn"], "100000000002")  # HIGH first

    def test_filters_and_pagination(self):
        self.login(Role.AUDITOR)
        self.assertEqual(self.client.get(self.LIST, {"priority": "LOW"}).json()["count"], 1)
        self.assertEqual(self.client.get(self.LIST, {"rule_code": "R05"}).json()["results"][0]["rrn"], "100000000002")
        self.assertEqual(self.client.get(self.LIST, {"status": "COMPLETED"}).json()["count"], 0)
        self.assertEqual(self.client.get(self.LIST, {"status": "all", "page_size": 1}).json()["count"], 2)
        self.assertEqual(len(self.client.get(self.LIST, {"page_size": 1}).json()["results"]), 1)
        self.assertEqual(self.client.get(self.LIST, {"flag": "bogus"}).status_code, 400)
        self.assertEqual(self.client.get(self.LIST, {"flag": "needs_review"}).json()["count"], 0)
        self.assertEqual(self.client.get(self.LIST, {"mine": "approval"}).json()["count"], 0)  # auditor

    def test_detail(self):
        self.login(Role.MAKER)
        d = self.client.get(f"{self.LIST}{self.c2.pk}/").json()
        self.assertEqual((d["rule_code"], d["default_action_type"], d["available_transitions"]),
                         ("R05", "RET", ["take", "remark"]))
        self.assertEqual(d["rule_result"]["rule_code"], "R05")
        self.assertEqual([e["event_type"] for e in d["events"]], ["CREATED"])
        self.assertIn("flags", d)
        self.assertEqual(d["overlay_rows"], [])

    def test_transitions(self):
        maker = self.login(Role.MAKER)
        url = f"{self.LIST}{self.c1.pk}"
        r = self.client.post(f"{url}/take/", {}, content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["status"], r.json()["assigned_to"]), ("IN_PROGRESS", maker.username))
        self.assertEqual(self.client.post(f"{url}/take/", {}, content_type="application/json").status_code, 409)
        self.assertEqual(self.client.post(f"{url}/submit/", {"action_type": "NOPE"},
                                          content_type="application/json").status_code, 400)
        r = self.client.post(f"{url}/submit/", {"action_type": "DRC_102"}, content_type="application/json")
        self.assertEqual(r.json()["status"], "PENDING_APPROVAL")
        self.assertEqual(self.client.post(f"{url}/approve/", {}, content_type="application/json").status_code, 403)
        self.assertEqual(self.client.post(f"{url}/fly/", {}, content_type="application/json").status_code, 404)
        self.login(Role.CHECKER)
        self.assertEqual(self.client.post(f"{url}/reject/", {"reason": ""}, content_type="application/json").status_code,
                         400)
        r = self.client.post(f"{url}/approve/", {}, content_type="application/json")
        self.assertEqual(r.json()["status"], "APPROVED")

    def test_bulk(self):
        maker = self.login(Role.MAKER)
        r = self.client.post(f"{self.LIST}bulk/take/", {"case_ids": [self.c1.pk, self.c2.pk]},
                             content_type="application/json")
        self.assertEqual(r.json()["applied"], 2)
        r = self.client.post(f"{self.LIST}bulk/submit/", {"case_ids": [self.c1.pk, self.c2.pk]},
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("LOW/MEDIUM", r.json()["detail"])
        self.assertFalse(ProposedAction.objects.exists())
        self.assertEqual(self.client.post(f"{self.LIST}bulk/submit/", {"case_ids": []},
                                          content_type="application/json").status_code, 400)
        r = self.client.post(f"{self.LIST}bulk/submit/", {"case_ids": [self.c1.pk]}, content_type="application/json")
        self.assertEqual(r.json()["applied"], 1)
        self.assertEqual(ExceptionCase.objects.get(pk=self.c1.pk).assigned_to, maker)

    def test_session_auth_enforces_csrf(self):
        u = self.user(Role.MAKER)
        c = Client(enforce_csrf_checks=True)
        c.force_login(u)
        r = c.post(f"{self.LIST}{self.c1.pk}/take/", {}, content_type="application/json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.case(self.c1.rrn).status, CaseStatus.OPEN)
