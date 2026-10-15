"""
/api/v1/ (Phase 5 Slice 1, design docs/phase5_design_draft_v1.txt 7.7): for
every endpoint the five roles allowed and refused, 401 when not logged in,
CSRF on every write, pagination, filters, error codes, and the audit record
(UserActivity for auth, CaseEvent for case changes).
"""
from __future__ import annotations

import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core.models import PaymentModule, UserActivity, UserActivityType
from exception_management.models import (
    CaseEvent,
    CaseStatus,
    CaseType,
    EventType,
    ExceptionCase,
    ProposedAction,
)
from exception_management.roles import Role
from exception_management.tests.base import D, CaseTestBase

V1 = "/api/v1"
CASES = f"{V1}/upi/cases/"
JSON = "application/json"
ALL_ROLES = Role.ALL


class V1TestBase(CaseTestBase):
    def setUp(self):
        super().setUp()
        self.users = {role: self.user(role) for role in ALL_ROLES}
        self.norole = self.user()

    def as_(self, who):
        """A fresh client logged in as `who` (a role name, a user, or None)."""
        c = Client()
        if who is not None:
            c.force_login(self.users[who] if isinstance(who, str) else who)
        return c

    def post(self, client, url, data=None):
        return client.post(url, data or {}, content_type=JSON)

    def assert_read_matrix(self, url, params=None):
        """GET: 200 for all five roles, 403 without a role, 401 anonymous."""
        for role in ALL_ROLES:
            with self.subTest(role=role, url=url):
                self.assertEqual(self.as_(role).get(url, params or {}).status_code, 200)
        with self.subTest(who="no role", url=url):
            r = self.as_(self.norole).get(url)
            self.assertEqual(r.status_code, 403)
        with self.subTest(who="anonymous", url=url):
            r = self.as_(None).get(url)
            self.assertEqual(r.status_code, 401)
            self.assertIn("detail", r.json())


def csrf_client(user=None):
    """A client that enforces CSRF, with its token fetched from /auth/csrf/."""
    c = Client(enforce_csrf_checks=True)
    if user is not None:
        c.force_login(user)
    token = c.get(f"{V1}/auth/csrf/").json()["csrf_token"]
    return c, token


# ---------------------------------------------------------------------------
# auth, me, session
# ---------------------------------------------------------------------------
class AuthTests(V1TestBase):
    def login(self, c, token, username, password="x"):
        return c.post(f"{V1}/auth/login/", {"username": username, "password": password}, content_type=JSON,
                      HTTP_X_CSRFTOKEN=token)

    def test_csrf_bootstrap_sets_cookie(self):
        c = Client(enforce_csrf_checks=True)
        r = c.get(f"{V1}/auth/csrf/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["csrf_token"])
        self.assertIn(settings.CSRF_COOKIE_NAME, c.cookies)

    def test_login_needs_csrf(self):
        c = Client(enforce_csrf_checks=True)
        r = c.post(f"{V1}/auth/login/", {"username": self.users[Role.MAKER].username, "password": "x"},
                   content_type=JSON)
        self.assertEqual(r.status_code, 403)
        self.assertIn("CSRF", r.json()["detail"])
        self.assertEqual(c.get(f"{V1}/me/").status_code, 401)
        self.assertFalse(UserActivity.objects.filter(activity_type=UserActivityType.LOGIN).exists())

    def test_login_each_role_logs_activity_and_returns_me(self):
        for role in ALL_ROLES:
            with self.subTest(role=role):
                c, token = csrf_client()
                u = self.users[role]
                r = self.login(c, token, u.username)
                self.assertEqual(r.status_code, 200)
                body = r.json()
                self.assertEqual((body["username"], body["roles"]), (u.username, [role]))
                self.assertEqual(body["modules"], [{"code": "UPI", "slug": "upi", "name": self.module.name}])
                self.assertTrue(UserActivity.objects.filter(activity_type=UserActivityType.LOGIN, user=u).exists())
                self.assertEqual(c.get(f"{V1}/me/").status_code, 200)

    def test_login_failures(self):
        c, token = csrf_client()
        maker = self.users[Role.MAKER]
        r = self.login(c, token, maker.username, "wrong")
        self.assertEqual((r.status_code, r.json()["detail"]), (400, "Invalid credentials"))
        self.assertTrue(UserActivity.objects.filter(activity_type=UserActivityType.LOGIN_FAILED,
                                                    username=maker.username).exists())
        r = self.login(c, token, "nobody-here", "x")
        self.assertEqual((r.status_code, r.json()["detail"]), (400, "Invalid credentials"))  # same message
        r = c.post(f"{V1}/auth/login/", {"username": ""}, content_type=JSON, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.json()["errors"]), {"username", "password"})
        inactive = self.user(Role.MAKER, active=False)
        self.assertEqual(self.login(c, token, inactive.username).status_code, 400)
        self.assertEqual(c.get(f"{V1}/me/").status_code, 401)

    def test_login_without_role_refused_and_logged(self):
        c, token = csrf_client()
        r = self.login(c, token, self.norole.username)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(c.get(f"{V1}/me/").status_code, 401)  # no session was created
        self.assertTrue(UserActivity.objects.filter(activity_type=UserActivityType.LOGIN_FAILED,
                                                    user=self.norole).exists())
        self.assertFalse(UserActivity.objects.filter(activity_type=UserActivityType.LOGIN, user=self.norole).exists())

    def test_logout_needs_csrf_and_is_logged(self):
        u = self.users[Role.CHECKER]
        c, _ = csrf_client(u)
        self.assertEqual(c.post(f"{V1}/auth/logout/", content_type=JSON).status_code, 403)
        self.assertEqual(c.get(f"{V1}/me/").status_code, 200)  # still logged in
        token = c.cookies[settings.CSRF_COOKIE_NAME].value
        self.assertEqual(c.post(f"{V1}/auth/logout/", content_type=JSON, HTTP_X_CSRFTOKEN=token).status_code, 204)
        self.assertTrue(UserActivity.objects.filter(activity_type=UserActivityType.LOGOUT, user=u).exists())
        self.assertEqual(c.get(f"{V1}/me/").status_code, 401)

    def test_logout_without_session_is_a_no_op(self):
        c, token = csrf_client()
        self.assertEqual(c.post(f"{V1}/auth/logout/", content_type=JSON, HTTP_X_CSRFTOKEN=token).status_code, 204)
        self.assertFalse(UserActivity.objects.filter(activity_type=UserActivityType.LOGOUT).exists())

    def test_me_matrix_and_permissions(self):
        self.assert_read_matrix(f"{V1}/me/")
        expected_true = {
            Role.ADMINISTRATOR: {"cases.reassign", "settings.propose_tolerance", "audit.view", "users.admin"},
            Role.OPERATIONS_USER: {"files.upload"},
            Role.MAKER: {"cases.work", "settings.propose_codes", "settings.propose_mapping"},
            Role.CHECKER: {"cases.approve", "settings.approve"},
            Role.AUDITOR: {"audit.view"},
        }
        everyone = {"cases.view", "files.view", "results.view", "settings.view", "alerts.view"}
        for role, extra in expected_true.items():
            with self.subTest(role=role):
                perms = self.as_(role).get(f"{V1}/me/").json()["permissions"]
                self.assertEqual({k for k, v in perms.items() if v}, everyone | extra)
        body = self.as_(Role.ADMINISTRATOR).get(f"{V1}/me/").json()
        self.assertEqual(body["admin_url"], "/admin/")
        self.assertEqual(body["session"], {"idle_timeout_seconds": 900, "warning_seconds": 120,
                                           "expires_at_browser_close": True})
        self.assertIsNone(self.as_(Role.MAKER).get(f"{V1}/me/").json()["admin_url"])
        both = self.user(Role.MAKER, Role.CHECKER)
        self.assertEqual(self.as_(both).get(f"{V1}/me/").json()["roles"], [Role.MAKER, Role.CHECKER])

    def test_session_timeout_policy(self):
        """P7: 15 minutes idle, renewed on each request, ends at browser close."""
        self.assertEqual(settings.SESSION_COOKIE_AGE, 15 * 60)
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)
        self.assertTrue(settings.SESSION_EXPIRE_AT_BROWSER_CLOSE)
        c, token = csrf_client()
        r = self.login(c, token, self.users[Role.MAKER].username)
        cookie = r.cookies[settings.SESSION_COOKIE_NAME]
        self.assertEqual(cookie["max-age"], "")  # a browser-session cookie
        self.assertTrue(cookie["httponly"])
        session = Session.objects.get(session_key=cookie.value)
        self.assertLessEqual(session.expire_date, timezone.now() + timedelta(seconds=901))
        # renewed by the next request
        Session.objects.filter(pk=session.pk).update(expire_date=timezone.now() + timedelta(seconds=60))
        self.assertEqual(c.get(f"{V1}/me/").status_code, 200)
        self.assertGreater(Session.objects.get(pk=session.pk).expire_date, timezone.now() + timedelta(seconds=800))
        # an expired session is "not logged in": 401, so the SPA returns to login
        Session.objects.filter(pk=session.pk).update(expire_date=timezone.now() - timedelta(seconds=1))
        self.assertEqual(c.get(f"{V1}/me/").status_code, 401)
        self.assertEqual(c.get(CASES).status_code, 401)


# ---------------------------------------------------------------------------
# modules, users, meta
# ---------------------------------------------------------------------------
class ReferenceTests(V1TestBase):
    def setUp(self):
        super().setUp()
        PaymentModule.objects.create(code="NEFT", name="NEFT", is_active=False)

    def test_modules(self):
        self.assert_read_matrix(f"{V1}/modules/")
        body = self.as_(Role.AUDITOR).get(f"{V1}/modules/").json()
        self.assertEqual(body["count"], 1)  # inactive NEFT not listed
        self.assertEqual(body["results"][0]["slug"], "upi")
        self.assertEqual(set(body), {"count", "next", "previous", "results"})

    def test_users_maker_list(self):
        self.assert_read_matrix(f"{V1}/users/", {"role": Role.MAKER})
        maker2 = self.user(Role.MAKER, Role.CHECKER)
        self.user(Role.MAKER, active=False)
        su = self.user(superuser=True)
        body = self.as_(Role.ADMINISTRATOR).get(f"{V1}/users/", {"role": Role.MAKER}).json()
        ids = {u["id"] for u in body["results"]}
        self.assertEqual(ids, {self.users[Role.MAKER].pk, maker2.pk, su.pk})
        row = next(u for u in body["results"] if u["id"] == maker2.pk)
        self.assertEqual(row["roles"], [Role.MAKER, Role.CHECKER])
        r = self.as_(Role.ADMINISTRATOR).get(f"{V1}/users/", {"role": "Boss"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("role", r.json()["errors"])
        everyone = self.as_(Role.ADMINISTRATOR).get(f"{V1}/users/").json()
        self.assertNotIn(self.norole.pk, {u["id"] for u in everyone["results"]})
        self.assertEqual(len(self.as_(Role.ADMINISTRATOR).get(f"{V1}/users/", {"page_size": 2}).json()["results"]), 2)

    def test_meta(self):
        url = f"{V1}/upi/meta/"
        self.assert_read_matrix(url)
        body = self.as_(Role.MAKER).get(url).json()
        self.assertEqual(body["module"]["code"], "UPI")
        actions = {a["value"]: a for a in body["action_types"]}
        self.assertTrue(actions["RRC_501_NO_CREDIT"]["requires_cbs_reversal_evidence"])
        self.assertTrue(actions["CLOSE_NO_ACTION"]["completes_on_approval"])
        self.assertTrue(actions["SETTLEMENT_EXPLANATION"]["settlement_only"])
        rules = {r["code"]: r for r in body["rules"]}
        self.assertEqual(rules["R01"]["default_action_type"], "DRC_102")
        self.assertEqual(rules["R13"]["default_action_type"], "")
        self.assertTrue(body["response_codes"])
        self.assertTrue(all(rc["flag"] in ("DRC", "TCC", "RET", "RRC") for rc in body["response_codes"]))
        self.assertIn("needs_review", {f["value"] for f in body["flags"]})
        self.assertIn("priority", body["case_orderings"])
        self.assertEqual([t["value"] for t in body["transitions"]][:2], ["take", "release"])
        self.assertTrue(body["ageing_buckets"])

    def test_unknown_or_inactive_module_is_404_after_auth(self):
        for url in (f"{V1}/neft/meta/", f"{V1}/imps/meta/", f"{V1}/neft/cases/", f"{V1}/neft/summary/"):
            with self.subTest(url=url):
                self.assertEqual(self.as_(Role.AUDITOR).get(url).status_code, 404)
                self.assertEqual(self.as_(None).get(url).status_code, 401)  # nothing leaks before login
        self.assertEqual(self.as_(Role.AUDITOR).get(f"{V1}/UPI/meta/").status_code, 200)  # case-insensitive


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------
class CaseReadTests(V1TestBase):
    def setUp(self):
        super().setUp()
        self.c1 = self.make_case("100000000001", "R01")  # LOW
        self.c2 = self.make_case("100000000002", "R05")  # HIGH
        self.c3 = self.make_case("100000000003", "R13")  # MEDIUM, Pending Investigation
        imps = PaymentModule.objects.create(code="IMPS", name="IMPS")
        self.other = ExceptionCase.objects.create(
            payment_module=imps, business_date=D, case_type=CaseType.SETTLEMENT, rrn="", rule_code="",
            status_category="EXCEPTION", priority="HIGH",
        )

    def test_list_matrix_and_shape(self):
        self.assert_read_matrix(CASES)
        body = self.as_(Role.AUDITOR).get(CASES).json()
        self.assertEqual(set(body), {"count", "next", "previous", "results"})
        self.assertEqual(body["count"], 3)  # the IMPS case is not in the UPI queue
        self.assertEqual(body["results"][0]["rrn"], "100000000002")  # queue order: HIGH first
        row = body["results"][0]
        self.assertEqual((row["payment_module"], row["self_approval"]), ("UPI", False))
        for key in ("flags", "ageing_bucket", "age_days", "is_overdue", "assigned_to", "due_date"):
            self.assertIn(key, row)
        imps = self.as_(Role.AUDITOR).get(f"{V1}/imps/cases/").json()
        self.assertEqual([r["id"] for r in imps["results"]], [self.other.pk])

    def test_pagination(self):
        c = self.as_(Role.MAKER)
        p1 = c.get(CASES, {"page_size": 2}).json()
        self.assertEqual((p1["count"], len(p1["results"])), (3, 2))
        self.assertIsNotNone(p1["next"])
        p2 = c.get(CASES, {"page_size": 2, "page": 2}).json()
        self.assertEqual(len(p2["results"]), 1)
        self.assertIsNone(p2["next"])
        self.assertEqual(c.get(CASES, {"page": 99}).status_code, 404)
        self.assertEqual(len(c.get(CASES, {"page_size": 10_000}).json()["results"]), 3)  # capped at 500, not refused

    def test_filters(self):
        c = self.as_(Role.AUDITOR)
        count = lambda **q: c.get(CASES, q).json()["count"]  # noqa: E731
        self.assertEqual(count(priority="LOW"), 1)
        self.assertEqual(count(rule_code="R13"), 1)
        self.assertEqual(count(rrn="100000000002"), 1)
        self.assertEqual(count(business_date=D.isoformat()), 3)
        self.assertEqual(count(business_date=(D + timedelta(days=1)).isoformat()), 0)
        self.assertEqual(count(due_date=self.c1.due_date.isoformat()), 3)
        self.assertEqual(count(status="COMPLETED"), 0)
        self.assertEqual(count(status="all"), 3)
        self.assertEqual(count(case_type="SETTLEMENT"), 0)
        self.assertEqual(count(flag="needs_review"), 0)
        self.assertEqual(count(mine="approval"), 0)  # an auditor approves nothing
        for bad in ({"flag": "bogus"}, {"business_date": "14-07-2026"}, {"due_date": "x"}, {"ordering": "amount"}):
            with self.subTest(bad=bad):
                r = c.get(CASES, bad)
                self.assertEqual(r.status_code, 400)
                self.assertIn("detail", r.json())

    def test_ordering(self):
        c = self.as_(Role.AUDITOR)
        rrns = lambda o: [r["rrn"] for r in c.get(CASES, {"ordering": o}).json()["results"]]  # noqa: E731
        self.assertEqual(rrns("priority"), ["100000000002", "100000000003", "100000000001"])
        self.assertEqual(rrns("-priority"), ["100000000001", "100000000003", "100000000002"])
        self.assertEqual(rrns("-rrn"), ["100000000003", "100000000002", "100000000001"])
        self.assertEqual(rrns("rule_code,-business_date"), ["100000000001", "100000000002", "100000000003"])

    def test_detail_and_history(self):
        url = f"{CASES}{self.c2.pk}/"
        self.assert_read_matrix(url)
        d = self.as_(Role.MAKER).get(url).json()
        self.assertEqual((d["payment_module"], d["rule_code"], d["available_transitions"], d["self_approval"]),
                         ("UPI", "R05", ["take", "remark"], False))
        self.assertEqual(d["rule_result"]["rule_code"], "R05")
        self.assertEqual(self.as_(Role.CHECKER).get(url).json()["available_transitions"], [])
        self.assertEqual(self.as_(Role.MAKER).get(f"{CASES}{self.other.pk}/").status_code, 404)  # other module
        self.assertEqual(self.as_(Role.MAKER).get(f"{CASES}999999/").status_code, 404)
        h = f"{url}history/"
        self.assert_read_matrix(h)
        body = self.as_(Role.AUDITOR).get(h).json()
        self.assertEqual((body["count"], body["results"][0]["event_type"]), (1, "CREATED"))
        self.assertEqual(self.as_(Role.AUDITOR).get(h, {"ordering": "-created_at"}).status_code, 200)
        self.assertEqual(self.as_(Role.AUDITOR).get(h, {"ordering": "rrn"}).status_code, 400)
        self.assertEqual(self.as_(Role.AUDITOR).get(f"{CASES}{self.other.pk}/history/").status_code, 404)

    def test_settlement_case_detail(self):
        d = self.as_(Role.AUDITOR).get(f"{V1}/imps/cases/{self.other.pk}/").json()
        self.assertEqual((d["case_type"], d["rule_result"], d["overlay_rows"]), ("SETTLEMENT", None, []))


class CaseWriteTests(V1TestBase):
    def setUp(self):
        super().setUp()
        self.c1 = self.make_case("100000000001", "R01")  # LOW, DRC 102
        self.c2 = self.make_case("100000000002", "R05")  # HIGH
        self.maker = self.users[Role.MAKER]

    def url(self, case, name):
        return f"{CASES}{case.pk}/{name}/"

    def refused_for(self, case, name, data, allowed_roles):
        """Every role outside allowed_roles gets 403 and nothing changes."""
        before = (ExceptionCase.objects.get(pk=case.pk).status, CaseEvent.objects.count())
        for role in ALL_ROLES:
            if role in allowed_roles:
                continue
            with self.subTest(transition=name, role=role):
                r = self.post(self.as_(role), self.url(case, name), data)
                self.assertEqual(r.status_code, 403, r.content)
        self.assertEqual((ExceptionCase.objects.get(pk=case.pk).status, CaseEvent.objects.count()), before)
        with self.subTest(transition=name, who="no role"):
            self.assertEqual(self.post(self.as_(self.norole), self.url(case, name), data).status_code, 403)
        with self.subTest(transition=name, who="anonymous"):
            self.assertEqual(self.post(self.as_(None), self.url(case, name), data).status_code, 401)

    def last_event(self, case):
        return CaseEvent.objects.filter(case=case).order_by("-id").first()

    def test_full_lifecycle_role_matrix_and_audit(self):
        """take -> submit -> reject -> submit -> approve -> complete, each
        refused for every other role and audited with source "api"."""
        maker, checker = self.maker, self.users[Role.CHECKER]
        c1 = self.c1
        self.refused_for(c1, "take", {}, {Role.MAKER})
        r = self.post(self.as_(maker), self.url(c1, "take"))
        self.assertEqual((r.status_code, r.json()["status"], r.json()["assigned_to"]),
                         (200, "IN_PROGRESS", maker.username))
        ev = self.last_event(c1)
        self.assertEqual((ev.event_type, ev.actor, ev.source), (EventType.TAKEN, maker, "api"))
        self.assertEqual(self.post(self.as_(maker), self.url(c1, "take")).status_code, 409)  # already taken

        self.refused_for(c1, "submit", {"action_type": "DRC_102"}, {Role.MAKER})
        r = self.post(self.as_(maker), self.url(c1, "submit"), {"action_type": "DRC_102", "remarks": "reversed"})
        self.assertEqual(r.json()["status"], "PENDING_APPROVAL")
        self.assertEqual(self.last_event(c1).event_type, EventType.SUBMITTED)

        self.refused_for(c1, "reject", {"reason": "no"}, {Role.CHECKER})
        r = self.post(self.as_(checker), self.url(c1, "reject"), {"reason": ""})
        self.assertEqual(r.status_code, 400)
        self.assertIn("reason", r.json()["errors"])
        r = self.post(self.as_(checker), self.url(c1, "reject"), {"reason": "check CBS"})
        self.assertEqual(r.json()["status"], "IN_PROGRESS")
        self.assertEqual((self.last_event(c1).event_type, self.last_event(c1).remarks), (EventType.REJECTED, "check CBS"))

        self.post(self.as_(maker), self.url(c1, "submit"), {"action_type": "DRC_102"})
        self.refused_for(c1, "approve", {}, {Role.CHECKER})
        r = self.post(self.as_(checker), self.url(c1, "approve"))
        self.assertEqual(r.json()["status"], "APPROVED")
        self.assertFalse(r.json()["actions"][-1]["self_approved"])
        self.assertEqual(self.post(self.as_(checker), self.url(c1, "approve")).status_code, 409)

        self.refused_for(c1, "complete", {"external_reference": "NPCI-1"}, {Role.MAKER})
        self.assertEqual(self.post(self.as_(maker), self.url(c1, "complete")).status_code, 400)  # reference required
        r = self.post(self.as_(maker), self.url(c1, "complete"), {"external_reference": "NPCI-1"})
        self.assertEqual(r.json()["status"], "COMPLETED")
        ev = self.last_event(c1)
        self.assertEqual((ev.event_type, ev.source, ev.changes["external_reference"]),
                         (EventType.COMPLETED, "api", [None, "NPCI-1"]))
        h = self.as_(Role.AUDITOR).get(f"{CASES}{c1.pk}/history/").json()
        self.assertEqual([e["event_type"] for e in h["results"]],
                         ["CREATED", "TAKEN", "SUBMITTED", "REJECTED", "SUBMITTED", "APPROVED", "COMPLETED"])

    def test_release_reassign_remark_clear_review(self):
        admin, maker = self.users[Role.ADMINISTRATOR], self.maker
        self.refused_for(self.c2, "reassign", {"to_user": maker.pk}, {Role.ADMINISTRATOR})
        r = self.post(self.as_(admin), self.url(self.c2, "reassign"), {"to_user": self.users[Role.AUDITOR].pk})
        self.assertEqual(r.status_code, 400)  # not a Maker: not in the form's choices
        r = self.post(self.as_(admin), self.url(self.c2, "reassign"), {"to_user": maker.pk})
        self.assertEqual((r.json()["status"], r.json()["assigned_to"]), ("IN_PROGRESS", maker.username))
        self.assertEqual(self.last_event(self.c2).event_type, EventType.REASSIGNED)

        self.refused_for(self.c2, "release", {}, {Role.MAKER, Role.ADMINISTRATOR})
        r = self.post(self.as_(maker), self.url(self.c2, "release"))
        self.assertEqual(r.json()["status"], "OPEN")

        self.refused_for(self.c2, "remark", {"text": "called the customer"}, {Role.MAKER})
        r = self.post(self.as_(maker), self.url(self.c2, "remark"), {"text": "called the customer"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((self.last_event(self.c2).event_type, self.last_event(self.c2).remarks),
                         (EventType.REMARK, "called the customer"))

        ExceptionCase.objects.filter(pk=self.c2.pk).update(needs_review=True, needs_review_reason="re-run")
        self.refused_for(self.c2, "clear_review", {"remarks": "fine"}, {Role.CHECKER})
        r = self.post(self.as_(Role.CHECKER), self.url(self.c2, "clear_review"), {"remarks": "fine"})
        self.assertFalse(r.json()["needs_review"])
        self.assertEqual(self.last_event(self.c2).event_type, EventType.REVIEW_CLEARED)

    def test_unknown_transition_and_other_module(self):
        self.assertEqual(self.post(self.as_(self.maker), self.url(self.c1, "fly")).status_code, 404)
        self.assertEqual(self.post(self.as_(self.maker), f"{CASES}999999/take/").status_code, 404)
        self.assertEqual(self.as_(self.maker).get(self.url(self.c1, "take")).status_code, 405)

    def test_csrf_on_transition(self):
        c, token = csrf_client(self.maker)
        self.assertEqual(c.post(self.url(self.c1, "take"), {}, content_type=JSON).status_code, 403)
        self.assertEqual(ExceptionCase.objects.get(pk=self.c1.pk).status, CaseStatus.OPEN)
        r = c.post(self.url(self.c1, "take"), {}, content_type=JSON, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 200)

    def test_self_approval_needs_confirmation(self):
        both = self.user(Role.MAKER, Role.CHECKER)
        c = self.as_(both)
        self.post(c, self.url(self.c1, "take"))
        self.post(c, self.url(self.c1, "submit"), {"action_type": "DRC_102"})
        d = c.get(f"{CASES}{self.c1.pk}/").json()
        self.assertTrue(d["self_approval"])
        self.assertIn("approve", d["available_transitions"])
        self.assertTrue([r for r in c.get(CASES).json()["results"] if r["id"] == self.c1.pk][0]["self_approval"])
        self.assertFalse(self.as_(Role.CHECKER).get(f"{CASES}{self.c1.pk}/").json()["self_approval"])
        r = self.post(c, self.url(self.c1, "approve"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("confirm_self_approval", r.json()["errors"])
        self.assertEqual(r.json()["self_approval_case_ids"], [self.c1.pk])
        self.assertEqual(ExceptionCase.objects.get(pk=self.c1.pk).status, CaseStatus.PENDING_APPROVAL)
        r = self.post(c, self.url(self.c1, "approve"), {"confirm_self_approval": True, "remarks": "own check"})
        self.assertEqual(r.json()["status"], "APPROVED")
        self.assertTrue(r.json()["actions"][-1]["self_approved"])
        ev = self.last_event(self.c1)
        self.assertEqual((ev.event_type, ev.action_snapshot["self_approved"]), (EventType.APPROVED, True))


class BulkTests(V1TestBase):
    def setUp(self):
        super().setUp()
        self.low = [self.make_case(f"20000000000{i}", "R01") for i in range(1, 4)]
        self.high = self.make_case("200000000009", "R05")
        self.r13 = self.make_case("200000000008", "R13")
        self.maker = self.users[Role.MAKER]

    def bulk(self, who, name, ids, **data):
        return self.post(self.as_(who), f"{CASES}bulk/{name}/", {"case_ids": ids, **data})

    def ids(self, cases):
        return [c.pk for c in cases]

    def test_bulk_take_submit_approve_and_roles(self):
        ids = self.ids(self.low)
        for role in set(ALL_ROLES) - {Role.MAKER}:
            with self.subTest(role=role):
                self.assertEqual(self.bulk(role, "take", ids).status_code, 403)
        self.assertEqual(self.bulk(None, "take", ids).status_code, 401)
        self.assertEqual(self.bulk(self.norole, "take", ids).status_code, 403)
        self.assertFalse(ExceptionCase.objects.filter(pk__in=ids).exclude(status=CaseStatus.OPEN).exists())

        r = self.bulk(Role.MAKER, "take", ids)
        self.assertEqual(r.json(), {"applied": 3, "case_ids": sorted(ids)})
        for role in set(ALL_ROLES) - {Role.MAKER}:
            with self.subTest(role=role):
                self.assertEqual(self.bulk(role, "submit", ids).status_code, 403)
        r = self.bulk(Role.MAKER, "submit", ids)
        self.assertEqual(r.json()["applied"], 3)
        self.assertEqual(CaseEvent.objects.filter(case_id__in=ids, event_type=EventType.SUBMITTED,
                                                  source="api bulk").count(), 3)
        for role in set(ALL_ROLES) - {Role.CHECKER}:
            with self.subTest(role=role):
                self.assertIn(self.bulk(role, "approve", ids).status_code, (403,))
        r = self.bulk(Role.CHECKER, "approve", ids)
        self.assertEqual(r.json()["applied"], 3)
        self.assertEqual(ExceptionCase.objects.filter(pk__in=ids, status=CaseStatus.APPROVED).count(), 3)

    def test_bulk_refusals_apply_nothing(self):
        self.bulk(Role.MAKER, "take", self.ids(self.low) + [self.high.pk, self.r13.pk])
        r = self.bulk(Role.MAKER, "submit", self.ids(self.low) + [self.high.pk])
        self.assertEqual(r.status_code, 400)
        self.assertIn("LOW/MEDIUM", r.json()["detail"])
        r = self.bulk(Role.MAKER, "submit", self.ids(self.low) + [self.r13.pk])
        self.assertEqual(r.status_code, 400)
        self.assertIn("Pending Investigation", r.json()["detail"])
        self.assertFalse(ProposedAction.objects.exists())
        for bad in ([], ["x"], None):
            with self.subTest(case_ids=bad):
                self.assertEqual(self.bulk(Role.MAKER, "submit", bad).status_code, 400)
        self.assertEqual(self.bulk(Role.MAKER, "reject", self.ids(self.low)).status_code, 404)

    def test_bulk_other_module_case_refused(self):
        imps = PaymentModule.objects.create(code="IMPS", name="IMPS")
        other = ExceptionCase.objects.create(payment_module=imps, business_date=D, case_type=CaseType.SETTLEMENT,
                                             rrn="", rule_code="", status_category="EXCEPTION", priority="LOW")
        r = self.bulk(Role.MAKER, "take", self.ids(self.low) + [other.pk])
        self.assertEqual(r.status_code, 400)
        self.assertFalse(ExceptionCase.objects.filter(status=CaseStatus.IN_PROGRESS).exists())

    def test_bulk_csrf(self):
        c, token = csrf_client(self.maker)
        url = f"{CASES}bulk/take/"
        self.assertEqual(c.post(url, {"case_ids": self.ids(self.low)}, content_type=JSON).status_code, 403)
        self.assertEqual(c.post(url, {"case_ids": self.ids(self.low)}, content_type=JSON,
                                HTTP_X_CSRFTOKEN=token).status_code, 200)

    def test_bulk_self_approval_needs_confirmation(self):
        both = self.user(Role.MAKER, Role.CHECKER)
        ids = self.ids(self.low)
        self.bulk(both, "take", ids[:2])
        self.bulk(both, "submit", ids[:2])
        self.bulk(Role.MAKER, "take", ids[2:])
        self.bulk(Role.MAKER, "submit", ids[2:])
        r = self.bulk(both, "approve", ids)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["self_approval_case_ids"], ids[:2])
        self.assertFalse(ExceptionCase.objects.filter(status=CaseStatus.APPROVED).exists())
        r = self.bulk(both, "approve", ids, confirm_self_approval=True)
        self.assertEqual(r.json()["applied"], 3)
        self.assertEqual(sorted(ProposedAction.objects.filter(self_approved=True).values_list("case_id", flat=True)),
                         ids[:2])


# ---------------------------------------------------------------------------
# home "my work"
# ---------------------------------------------------------------------------
class SummaryTests(V1TestBase):
    URL = f"{V1}/upi/summary/"

    def setUp(self):
        super().setUp()
        self.make_case("300000000001", "R05")  # HIGH
        self.make_case("300000000002", "R01")

    def test_matrix_and_blocks_per_role(self):
        self.assert_read_matrix(self.URL)
        blocks = {"maker", "checker", "operations"}
        expected = {
            Role.MAKER: {"maker"}, Role.CHECKER: {"checker"}, Role.OPERATIONS_USER: {"operations"},
            Role.ADMINISTRATOR: blocks, Role.AUDITOR: blocks,
        }
        for role, want in expected.items():
            with self.subTest(role=role):
                body = self.as_(role).get(self.URL).json()
                self.assertEqual(blocks & set(body), want)

    def test_counts_and_links(self):
        maker = self.users[Role.MAKER]
        c = self.as_(maker)
        case = ExceptionCase.objects.get(rrn="300000000002")
        self.post(c, f"{CASES}{case.pk}/take/")
        body = c.get(self.URL).json()["maker"]
        self.assertEqual(body["high_open"], {"count": 1, "query": {"priority": "HIGH", "status": "OPEN,IN_PROGRESS"}})
        self.assertEqual(body["mine"]["count"], 1)
        # the link reproduces the count in the queue
        self.assertEqual(c.get(CASES, body["mine"]["query"]).json()["count"], 1)
        self.assertEqual(c.get(CASES, body["high_open"]["query"]).json()["count"], 1)
        self.assertEqual(c.get(CASES, body["due_today"]["query"]).json()["count"], body["due_today"]["count"])
        self.post(c, f"{CASES}{case.pk}/submit/", {"action_type": "DRC_102"})
        chk = self.as_(Role.CHECKER).get(self.URL).json()["checker"]
        self.assertEqual(chk["awaiting"]["count"], 1)
        self.assertEqual(chk["settings_awaiting"], {"response_codes": 0, "ntsl_mapping": 0, "tolerance": 0})
        ops = self.as_(Role.OPERATIONS_USER).get(self.URL).json()["operations"]
        self.assertEqual((ops["latest_business_date"], ops["files_received"], ops["files_missing"]), (None, 0, None))


# ---------------------------------------------------------------------------
# legacy /api/exceptions/ is unchanged (P2)
# ---------------------------------------------------------------------------
class LegacyApiUnchangedTests(V1TestBase):
    def test_legacy_still_403_for_anonymous_and_has_no_module_field(self):
        self.make_case("400000000001", "R01")
        self.assertEqual(self.as_(None).get("/api/exceptions/cases/").status_code, 403)
        row = self.as_(Role.AUDITOR).get("/api/exceptions/cases/").json()["results"][0]
        self.assertNotIn("payment_module", row)
        self.assertNotIn("self_approval", row)


# ---------------------------------------------------------------------------
# serving the production SPA build
# ---------------------------------------------------------------------------
class SpaServingTests(TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_inert_without_a_build(self):
        with override_settings(FRONTEND_DIST_DIR=self.dir / "missing"):
            for url in ("/", "/upi/cases/1", "/assets/index.js"):
                self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_serves_index_and_assets(self):
        (self.dir / "assets").mkdir()
        (self.dir / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
        (self.dir / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
        with override_settings(FRONTEND_DIST_DIR=self.dir):
            for url in ("/", "/upi/cases/123", "/login"):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 200, url)
                self.assertIn(b"id=root", b"".join(r.streaming_content))
                self.assertEqual(r["Cache-Control"], "no-cache")
            r = self.client.get("/assets/index-abc.js")
            self.assertEqual(r.status_code, 200)
            self.assertIn(b"console.log", b"".join(r.streaming_content))
            self.assertEqual(self.client.get("/assets/missing.js").status_code, 404)
            self.assertEqual(self.client.get("/../settings.py").status_code, 404)
            self.assertEqual(self.client.get("/api/v1/no-such/").status_code, 404)  # API paths never get the SPA
            self.assertEqual(self.client.post("/").status_code, 405)
