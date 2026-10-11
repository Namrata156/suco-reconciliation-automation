"""User activity log (Phase 3 design section 5): login, logout and failed
login are recorded append-only; Administrator and Auditor can read it."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.test import TestCase

from core.append_only import AppendOnlyError
from core.models import UserActivity, UserActivityType


class UserActivityTests(TestCase):
    def user(self, name="ops1", *groups):
        u = get_user_model().objects.create_user(name, password="pw-123456", is_staff=True)
        for g in groups:
            u.groups.add(Group.objects.get(name=g))
        return u

    def test_login_logout_and_failed_login(self):
        u = self.user()
        self.assertTrue(self.client.login(username="ops1", password="pw-123456"))
        self.client.logout()
        self.assertFalse(self.client.login(username="ops1", password="wrong"))
        self.assertFalse(self.client.login(username="nobody", password="x"))
        rows = list(UserActivity.objects.order_by("id").values_list("activity_type", "user_id", "username"))
        self.assertEqual(rows, [
            (UserActivityType.LOGIN, u.pk, "ops1"),
            (UserActivityType.LOGOUT, u.pk, "ops1"),
            (UserActivityType.LOGIN_FAILED, u.pk, "ops1"),
            (UserActivityType.LOGIN_FAILED, None, "nobody"),
        ])

    def test_admin_login_records_ip(self):
        self.user()
        self.client.post("/admin/login/", {"username": "ops1", "password": "pw-123456"}, REMOTE_ADDR="10.1.2.3",
                         HTTP_USER_AGENT="test-agent")
        a = UserActivity.objects.get(activity_type=UserActivityType.LOGIN)
        self.assertEqual((a.ip_address, a.user_agent, a.path), ("10.1.2.3", "test-agent", "/admin/login/"))

    def test_append_only(self):
        self.user()
        self.client.login(username="ops1", password="pw-123456")
        a = UserActivity.objects.get()
        a.username = "x"
        for fn in (a.save, a.delete, lambda: UserActivity.objects.update(username="x"),
                   lambda: UserActivity.objects.all().delete()):
            with self.assertRaises(AppendOnlyError), transaction.atomic():
                fn()

    def test_admin_visibility_by_role(self):
        url = "/admin/core/useractivity/"
        for group, status in (("Auditor", 200), ("Administrator", 200), ("Maker", 403), ("Checker", 403),
                              ("Operations User", 403)):
            with self.subTest(group=group):
                self.client.force_login(self.user(f"u_{group.replace(' ', '_')}", group))
                self.assertEqual(self.client.get(url).status_code, status)
        self.client.force_login(self.user("aud2", "Auditor"))
        self.assertEqual(self.client.get(url + "add/").status_code, 403)
