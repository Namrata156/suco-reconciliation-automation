"""
Owner decision 2026-11-12 (Plan B item 4): roles switched off
(settings.ROLES_ENFORCED = False, the default outside tests). Every active,
signed-in user holds every role and permission; every step is still logged
by username and self-approval is still recorded. Inactive users are still
refused. The rest of the suite runs with ROLES_ENFORCED = True
(config.test_runner). Synthetic data.
"""
from __future__ import annotations

from django.test import override_settings

from api.tests.test_v1 import CASES, JSON, V1, V1TestBase, csrf_client
from core.models import UserActivity, UserActivityType
from exception_management.alerts import role_members
from exception_management.models import CaseEvent, CaseStatus, EventType, ExceptionCase, ProposedAction
from exception_management.roles import Role, has_role, listed_roles, roles_enforced, user_roles


@override_settings(ROLES_ENFORCED=False)
class RolesOffTests(V1TestBase):
    def setUp(self):
        super().setUp()
        self.plain = self.user()  # no group at all
        self.case = self.make_case("100000000001", "R02")

    def test_every_active_user_holds_every_role(self):
        self.assertFalse(roles_enforced())
        self.assertEqual(user_roles(self.plain), set(Role.ALL))
        self.assertTrue(has_role(self.plain, Role.CHECKER))
        self.assertEqual(listed_roles(self.plain), [])
        inactive = self.user(active=False)
        self.assertEqual(user_roles(inactive), set())
        self.assertIn(self.plain, role_members(Role.CHECKER))  # alerts go to every active user
        self.assertNotIn(inactive, role_members(Role.CHECKER))

    def test_login_and_me(self):
        c, token = csrf_client()
        r = c.post(f"{V1}/auth/login/", {"username": self.plain.username, "password": "x"}, content_type=JSON,
                   HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 200, r.content[:300])
        me = r.json()
        self.assertFalse(me["roles_enforced"])
        self.assertTrue(all(me["permissions"].values()))
        self.assertTrue(me["permissions"]["credit_vouchers.upload"])
        self.assertTrue(UserActivity.objects.filter(username=self.plain.username,
                                                    activity_type=UserActivityType.LOGIN).exists())

    def test_inactive_user_still_refused(self):
        inactive = self.user(active=False)
        c, token = csrf_client()
        r = c.post(f"{V1}/auth/login/", {"username": inactive.username, "password": "x"}, content_type=JSON,
                   HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 400)  # Django's authenticate() refuses an inactive user
        self.assertEqual(self.as_(inactive).get(f"{V1}/me/").status_code, 401)  # its session is not accepted

    def test_one_user_takes_submits_and_approves_own_case_self_approved(self):
        c = self.as_(self.plain)
        detail = c.get(f"{CASES}{self.case.pk}/").json()
        self.assertIn("take", detail["available_transitions"])
        self.assertEqual(self.post(c, f"{CASES}{self.case.pk}/take/").status_code, 200)
        r = self.post(c, f"{CASES}{self.case.pk}/submit/", {"action_type": "DRC_103"})
        self.assertEqual(r.status_code, 200, r.content[:300])
        r = self.post(c, f"{CASES}{self.case.pk}/approve/")
        self.assertEqual(r.status_code, 400)  # own submission: the self-approval confirmation still applies
        self.assertIn("confirm_self_approval", r.json()["errors"])
        r = self.post(c, f"{CASES}{self.case.pk}/approve/", {"confirm_self_approval": True})
        self.assertEqual(r.status_code, 200, r.content[:300])
        a = ProposedAction.objects.get(case=self.case)
        self.assertEqual((a.submitted_by, a.decided_by, a.self_approved), (self.plain, self.plain, True))
        self.assertEqual(ExceptionCase.objects.get(pk=self.case.pk).status, CaseStatus.APPROVED)
        self.assertEqual(set(CaseEvent.objects.filter(case=self.case, event_type__in=(
            EventType.TAKEN, EventType.SUBMITTED, EventType.APPROVED)).values_list("actor_username", flat=True)),
            {self.plain.username})

    def test_user_lists_show_no_role_names_and_ignore_the_role_filter(self):
        c = self.as_(self.plain)
        users = c.get(f"{V1}/users/", {"role": Role.CHECKER}).json()["results"]
        self.assertIn(self.plain.username, [u["username"] for u in users])
        self.assertTrue(all(u["roles"] == [] for u in users))
        body = c.get(f"{V1}/users-and-roles/", {"role": Role.AUDITOR}).json()
        self.assertIn(self.plain.username, [u["username"] for u in body["results"]])
        self.assertTrue(all(u["roles"] == [] for u in body["results"]))
        report = c.get(f"{V1}/reports/user-activity/", {"role": Role.AUDITOR}).json()
        self.assertIn(self.plain.username, [u["username"] for u in report["results"]])
        self.assertTrue(all(u["roles"] == [] for u in report["results"]))


class RolesOnTests(V1TestBase):
    """The test runner's default: roles enforced, as before."""

    def test_me_says_roles_enforced_and_a_user_without_role_is_refused(self):
        self.assertTrue(roles_enforced())
        self.assertTrue(self.as_(Role.MAKER).get(f"{V1}/me/").json()["roles_enforced"])
        self.assertEqual(user_roles(self.norole), set())
        self.assertEqual(listed_roles(self.users[Role.MAKER]), [Role.MAKER])
        self.assertEqual(self.as_(self.norole).get(f"{V1}/me/").status_code, 403)
