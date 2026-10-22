"""
/api/v1/ Phase 5 Slice 4 (design docs/phase5_design_draft_v1.txt 4.17-4.19;
contract docs/phase5_slice4_api_contract.txt): the in-app alerts with a
per-user read status and SPA links, the read-only audit lists (case events,
settings events, user activity, operations), the read-only users-and-roles
list, and the Slice 3 fit items.

For each endpoint: the roles allowed and refused, 401 anonymous, CSRF on
writes, module isolation, filters and paging, and that reading alerts never
changes the append-only outbox.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from io import StringIO
from unittest import mock

from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.utils import timezone

from api.tests.test_v1 import ALL_ROLES, JSON, V1, V1TestBase, csrf_client
from core.models import (
    AlertType,
    Notification,
    NotificationRead,
    NotificationStatus,
    OperationLog,
    OperationType,
    PaymentModule,
    UserActivity,
    UserActivityType,
)
from core.operations import log_operation
from exception_management import code_changes, services
from exception_management.alerts import send_digest
from exception_management.models import CaseEvent, EventType, ExceptionCase, ResponseCodeChangeRequest
from exception_management.roles import Role
from exception_management.tests.base import D
from rule_engine.models import ResponseCode
from settlement import tolerance as tolerance_service

A, O, M, C, U = Role.ADMINISTRATOR, Role.OPERATIONS_USER, Role.MAKER, Role.CHECKER, Role.AUDITOR
ALERTS = f"{V1}/notifications/"
COUNT = f"{V1}/notifications/unread-count/"
READ_ALL = f"{V1}/notifications/read-all/"
AUDIT = f"{V1}/audit"
USERS = f"{V1}/users-and-roles/"
SETTINGS = f"{V1}/upi/settings"


def at(day, hour=10):
    """Patch "now" so auto_now_add timestamps land on a chosen local day."""
    return mock.patch("django.utils.timezone.now",
                      return_value=timezone.make_aware(datetime.combine(day, time(hour, 0))))


class Slice4Base(V1TestBase):
    def ok(self, who, url, params=None):
        r = self.as_(who).get(url, params or {})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def bad(self, who, url, params, field):
        r = self.as_(who).get(url, params)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn(field, r.json()["errors"])

    def alert(self, user, alert_type=AlertType.SUBMITTED, *, body="", related=None, link_path=""):
        return Notification.objects.create(
            alert_type=alert_type, recipient=user, recipient_username=user.username, subject=f"{alert_type}",
            body=body, status=NotificationStatus.HELD_NO_MAIL_SERVER, link_path=link_path,
            content_type=ContentType.objects.get_for_model(related) if related is not None else None,
            object_id=related.pk if related is not None else None)

    def assert_roles(self, url, allowed, params=None):
        for role in ALL_ROLES:
            with self.subTest(role=role, url=url):
                self.assertEqual(self.as_(role).get(url, params or {}).status_code, 200 if role in allowed else 403)
        with self.subTest(who="no role", url=url):
            self.assertEqual(self.as_(self.norole).get(url).status_code, 403)
        with self.subTest(who="anonymous", url=url):
            self.assertEqual(self.as_(None).get(url).status_code, 401)


# ---------------------------------------------------------------------------
# A. alerts
# ---------------------------------------------------------------------------
class AlertTests(Slice4Base):
    def test_role_matrix(self):
        for url in (ALERTS, COUNT):
            self.assert_read_matrix(url)
        n = self.alert(self.users[M])
        for url in (f"{V1}/notifications/{n.pk}/read/", READ_ALL):
            with self.subTest(url=url):
                self.assertEqual(self.post(self.as_(None), url).status_code, 401)
                self.assertEqual(self.post(self.as_(self.norole), url).status_code, 403)

    def test_only_my_alerts_and_test_alerts_hidden(self):
        mine = self.alert(self.users[M])
        other = self.alert(self.users[C])
        self.alert(self.users[M], AlertType.TEST)
        body = self.ok(M, ALERTS)
        self.assertEqual([r["id"] for r in body["results"]], [mine.pk])
        self.assertEqual(body["unread"], 1)
        self.assertEqual(self.ok(M, COUNT), {"unread": 1, "latest_id": mine.pk})
        r = self.post(self.as_(M), f"{V1}/notifications/{other.pk}/read/")
        self.assertEqual(r.status_code, 404)
        self.assertFalse(NotificationRead.objects.exists())

    def test_read_status_is_per_user(self):
        """One event, two checkers: each has their own alert and read mark."""
        c2 = self.user(C)
        case = self.make_case()
        services.take(case, self.users[M])
        with self.captureOnCommitCallbacks(execute=True):
            services.submit(case, self.users[M])
        a1 = Notification.objects.get(recipient=self.users[C])
        a2 = Notification.objects.get(recipient=c2)
        r = self.post(self.as_(C), f"{V1}/notifications/{a1.pk}/read/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["read"])
        self.assertEqual(self.ok(C, COUNT)["unread"], 0)
        self.assertEqual(self.ok(c2, COUNT), {"unread": 1, "latest_id": a2.pk})
        self.assertEqual([x["read"] for x in self.ok(c2, ALERTS)["results"]], [False])

    def test_mark_read_idempotent_and_outbox_unchanged(self):
        n = self.alert(self.users[M])
        before = Notification.objects.filter(pk=n.pk).values().get()
        url = f"{V1}/notifications/{n.pk}/read/"
        first = self.post(self.as_(M), url).json()
        second = self.post(self.as_(M), url).json()
        self.assertEqual(first["read_at"], second["read_at"])  # the first read_at stays
        self.assertEqual(NotificationRead.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(pk=n.pk).values().get(), before)

    def test_unread_count_writes_nothing(self):
        self.alert(self.users[M])
        c = self.as_(M)
        counts = (NotificationRead.objects.count(), UserActivity.objects.count(), OperationLog.objects.count(),
                  CaseEvent.objects.count())
        for _ in range(3):
            self.assertEqual(c.get(COUNT).json()["unread"], 1)
        self.assertEqual((NotificationRead.objects.count(), UserActivity.objects.count(),
                          OperationLog.objects.count(), CaseEvent.objects.count()), counts)

    def test_filters_and_paging(self):
        u = self.users[M]
        ns = [self.alert(u, AlertType.SUBMITTED) for _ in range(3)] + [self.alert(u, AlertType.REJECTED)]
        NotificationRead.objects.create(notification=ns[0], user=u, read_at=timezone.now())
        self.assertEqual(self.ok(M, ALERTS)["count"], 4)
        body = self.ok(M, ALERTS, {"unread": "true"})
        self.assertEqual((body["count"], body["unread"]), (3, 3))
        self.assertEqual([r["id"] for r in self.ok(M, ALERTS, {"unread": "false"})["results"]], [ns[0].pk])
        self.assertEqual(self.ok(M, ALERTS, {"alert_type": "REJECTED"})["count"], 1)
        page = self.ok(M, ALERTS, {"page_size": 2})
        self.assertEqual([r["id"] for r in page["results"]], [ns[3].pk, ns[2].pk])  # newest first
        self.assertIsNotNone(page["next"])
        self.bad(M, ALERTS, {"unread": "yes"}, "unread")
        self.bad(M, ALERTS, {"alert_type": "TEST"}, "alert_type")

    def test_read_all_and_up_to_id(self):
        u = self.users[M]
        ns = [self.alert(u) for _ in range(3)]
        r = self.post(self.as_(M), READ_ALL, {"up_to_id": ns[1].pk})
        self.assertEqual(r.json(), {"marked": 2, "unread": 1})
        r = self.post(self.as_(M), READ_ALL)
        self.assertEqual(r.json(), {"marked": 1, "unread": 0})
        self.assertEqual(self.post(self.as_(M), READ_ALL).json(), {"marked": 0, "unread": 0})
        r = self.post(self.as_(M), READ_ALL, {"up_to_id": "5"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("up_to_id", r.json()["errors"])
        other = self.alert(self.users[C])
        self.post(self.as_(M), READ_ALL)
        self.assertFalse(NotificationRead.objects.filter(notification=other).exists())  # never another's

    def test_csrf_on_writes(self):
        n = self.alert(self.users[M])
        for url in (f"{V1}/notifications/{n.pk}/read/", READ_ALL):
            with self.subTest(url=url):
                c, token = csrf_client(self.users[M])
                self.assertEqual(c.post(url, {}, content_type=JSON).status_code, 403)
                self.assertEqual(c.post(url, {}, content_type=JSON, HTTP_X_CSRFTOKEN=token).status_code, 200)

    # -- links -------------------------------------------------------------------
    def test_new_alerts_link_to_the_spa(self):
        case = self.make_case()
        services.take(case, self.users[M])
        with self.captureOnCommitCallbacks(execute=True):
            services.submit(case, self.users[M])
        n = Notification.objects.get(alert_type=AlertType.SUBMITTED)
        self.assertEqual(n.link_path, f"/upi/cases/{case.pk}")
        self.assertIn(f"/upi/cases/{case.pk}", n.body)
        self.assertNotIn("/admin/", n.body)
        row = self.ok(C, ALERTS)["results"][0]
        self.assertEqual((row["link"], row["module"], row["related"]),
                         (f"/upi/cases/{case.pk}", "upi", {"type": "case", "id": case.pk}))
        with self.captureOnCommitCallbacks(execute=True):
            services.reject(case, self.users[C], reason="Use 103")
        self.assertEqual(Notification.objects.get(alert_type=AlertType.REJECTED).link_path, f"/upi/cases/{case.pk}")

    def test_bulk_digest_and_multi_case_links(self):
        cases = [self.make_case(f"30000000000{i}", "R01") for i in range(2)]
        services.bulk_take(cases, self.users[M])
        with self.captureOnCommitCallbacks(execute=True):
            services.bulk_submit(cases, self.users[M])
        self.assertEqual(Notification.objects.get(alert_type=AlertType.BULK_SUBMITTED).link_path,
                         "/upi/cases?mine=approval")
        with self.captureOnCommitCallbacks(execute=True):
            send_digest()
        self.assertEqual(Notification.objects.get(alert_type=AlertType.DIGEST_MAKER).link_path,
                         "/upi/cases?mine=assigned")
        self.assertEqual(Notification.objects.get(alert_type=AlertType.DIGEST_CHECKER).link_path,
                         "/upi/cases?mine=approval")
        self.result("400000000001", "R07x")
        self.result("400000000002", "R07x")
        with self.captureOnCommitCallbacks(execute=True):
            self.sync()
        n = Notification.objects.filter(alert_type=AlertType.R07X_CASE).first()
        self.assertEqual(n.link_path, f"/upi/cases?flag=r07x&status=all&business_date={D.isoformat()}")

    def test_old_alerts_admin_links_mapped(self):
        case = self.make_case()
        u = self.users[M]
        admin_case = f"http://intranet/admin/exception_management/exceptioncase/{case.pk}/change/"
        related = self.alert(u, body=f"See {admin_case}\n", related=case)
        by_body = self.alert(u, AlertType.REJECTED, body=f"See {admin_case}\n")
        queue = self.alert(u, AlertType.BULK_SUBMITTED,
                           body="Queue: http://intranet/admin/exception_management/exceptioncase/?mine=approval\n")
        digest = self.alert(u, AlertType.DIGEST_MAKER, body="no link\n")
        links = {r["id"]: r["link"] for r in self.ok(M, ALERTS)["results"]}
        self.assertEqual(links, {related.pk: f"/upi/cases/{case.pk}", by_body.pk: f"/upi/cases/{case.pk}",
                                 queue.pk: "/upi/cases?mine=approval", digest.pk: "/upi/cases?mine=assigned"})


# ---------------------------------------------------------------------------
# B. audit
# ---------------------------------------------------------------------------
class AuditTests(Slice4Base):
    def setUp(self):
        super().setUp()
        call_command("seed_settlement_cycles", stdout=StringIO())
        call_command("seed_ntsl_category_map", stdout=StringIO())  # SEEDED mapping event (system)
        self.imps = PaymentModule.objects.create(code="IMPS", name="IMPS")

    def test_role_matrix_and_read_only(self):
        for name in ("meta", "case-events", "settings-events", "user-activity", "operations"):
            url = f"{AUDIT}/{name}/"
            self.assert_roles(url, {A, U})
            with self.subTest(write=url):
                self.assertEqual(self.post(self.as_(U), url).status_code, 405)

    def test_case_events_filters(self):
        maker = self.users[M]
        with at(D):
            case = self.make_case()  # CREATED by the system
        with at(D + timedelta(days=1)):
            services.take(case, maker)
            services.add_remark(case, maker, "checked")
        other = self.make_case("100000000002")
        ExceptionCase.objects.filter(pk=other.pk).update(payment_module=self.imps)
        CaseEvent.objects.create(case=other, payment_module_code="IMPS", business_date=D, rrn=other.rrn,
                                 event_type=EventType.REMARK, actor=maker, actor_username=maker.username)
        url = f"{AUDIT}/case-events/"
        body = self.ok(U, url, {"module": "upi"})
        self.assertTrue(all(r["module"] == "upi" for r in body["results"]))
        first = self.ok(U, url, {"case": case.pk})["results"][0]
        self.assertEqual(first["event_type"], EventType.REMARK)  # newest first
        self.assertEqual(first["link"], f"/upi/cases/{case.pk}")
        self.assertEqual(self.ok(U, url, {"module": "imps"})["count"], 1)
        self.assertEqual(self.ok(U, url, {"user": maker.username.upper(), "module": "upi"})["count"], 2)
        self.assertEqual(self.ok(U, url, {"event_type": "TAKEN,REMARK", "case": case.pk})["count"], 2)
        self.assertEqual(self.ok(U, url, {"date_from": D.isoformat(), "date_to": D.isoformat(),
                                          "case": case.pk})["count"], 1)
        self.assertEqual(self.ok(U, url, {"date_from": (D + timedelta(days=1)).isoformat(),
                                          "case": case.pk})["count"], 2)
        self.assertEqual(self.ok(U, url, {"rrn": case.rrn, "business_date": D.isoformat()})["count"], 3)
        oldest = self.ok(U, url, {"case": case.pk, "ordering": "created_at", "page_size": 1})
        self.assertEqual((oldest["count"], oldest["results"][0]["event_type"], oldest["results"][0]["actor_username"]),
                         (3, EventType.CREATED, None))
        for params, field in (({"date_from": "14-07-2026"}, "date_from"), ({"event_type": "NOPE"}, "event_type"),
                              ({"module": "xyz"}, "module"), ({"ordering": "rrn"}, "ordering"),
                              ({"case": "abc"}, "case"),
                              ({"date_from": "2026-07-15", "date_to": "2026-07-14"}, "date_to")):
            with self.subTest(params=params):
                self.bad(A, url, params, field)

    def test_self_approved_flag(self):
        both = self.user(M, C)
        case = self.make_case()
        services.take(case, both)
        services.submit(case, both)
        services.approve(case, both)
        rows = self.ok(U, f"{AUDIT}/case-events/", {"event_type": "APPROVED"})["results"]
        self.assertEqual([r["self_approved"] for r in rows], [True])

    def test_settings_events_union(self):
        rc = ResponseCode.objects.filter(payment_module=self.module, flag="RET", code__isnull=True).first()
        with at(D):
            req = code_changes.propose_code_change(rc, self.users[M], new_code="116", reason="NPCI")
        with at(D + timedelta(days=2)):
            t = tolerance_service.propose_tolerance_change(self.module, self.users[A], amount="1.00", txn_count=0,
                                                           reason="ops")
        url = f"{AUDIT}/settings-events/"
        body = self.ok(U, url)
        kinds = {r["kind"] for r in body["results"]}
        self.assertEqual(kinds, {"response_code", "ntsl_mapping", "tolerance"})
        self.assertEqual(len({r["id"] for r in body["results"]}), body["count"])  # ids unique across tables
        code_row = self.ok(U, url, {"kind": "response_code"})["results"][0]
        self.assertEqual((code_row["item_id"], code_row["event_type"], code_row["actor_username"], code_row["link"]),
                         (req.pk, "PROPOSED", self.users[M].username, "/upi/settings/response-codes"))
        self.assertIn(str(rc), code_row["item_label"])
        tol = self.ok(U, url, {"user": self.users[A].username})
        self.assertEqual([(r["kind"], r["item_id"], r["item_label"]) for r in tol["results"]],
                         [("tolerance", t.pk, f"Tolerance v{t.version}")])
        seeded = self.ok(U, url, {"event_type": "SEEDED"})["results"]
        self.assertTrue(seeded and all(r["actor_username"] == "seed" for r in seeded))  # settlement.seeding
        self.assertEqual(self.ok(U, url, {"date_from": D.isoformat(), "date_to": D.isoformat()})["count"], 1)
        newest_first = [r["created_at"] for r in self.ok(U, url)["results"]]
        oldest_first = [r["created_at"] for r in self.ok(U, url, {"ordering": "created_at"})["results"]]
        self.assertEqual(newest_first, sorted(newest_first, reverse=True))
        self.assertEqual(oldest_first, sorted(oldest_first))
        self.assertEqual(self.ok(U, url, {"ordering": "created_at"})["results"][0]["kind"], "response_code")  # D
        self.assertEqual(self.ok(U, url, {"module": "imps"})["count"], 0)
        paged = self.ok(U, url, {"page_size": 1, "page": 2})
        self.assertEqual(len(paged["results"]), 1)
        self.bad(U, url, {"kind": "users"}, "kind")
        self.bad(U, url, {"event_type": "TAKEN"}, "event_type")

    def test_user_activity(self):
        with at(D):
            UserActivity.objects.create(activity_type=UserActivityType.LOGIN_FAILED, username="Typo",
                                        ip_address="10.0.0.5")
        url = f"{AUDIT}/user-activity/"
        rows = self.ok(U, url, {"user": "typo"})["results"]
        self.assertEqual([(r["event_type"], r["user_id"], r["ip_address"]) for r in rows],
                         [("LOGIN_FAILED", None, "10.0.0.5")])
        self.assertEqual(self.ok(U, url, {"date_to": D.isoformat()})["count"], 1)
        # The Auditor's own logins (force_login sends Django's signal) are listed too.
        self.assertGreater(self.ok(U, url, {"event_type": "LOGIN"})["count"], 0)
        self.bad(U, url, {"event_type": "UPLOAD"}, "event_type")

    def test_operations(self):
        ops = self.users[O]
        log_operation(OperationType.FILE_UPLOADED, user=ops, payment_module=self.module, business_date=D,
                      uploaded_file_id=77, source="api", details={"name": "a.csv"})
        log_operation(OperationType.RUN_STARTED, user=ops, payment_module=self.module, business_date=D, run_id=5,
                      source="api")
        log_operation(OperationType.RUN_STARTED, user=ops, payment_module=self.imps, business_date=D, source="api")
        url = f"{AUDIT}/operations/"
        rows = self.ok(A, url, {"module": "upi"})["results"]
        self.assertEqual([(r["event_type"], r["link"]) for r in rows],
                         [("RUN_STARTED", f"/upi/dates/{D.isoformat()}"), ("FILE_UPLOADED", "/upi/files/77")])
        self.assertEqual(rows[1]["details"], {"name": "a.csv"})
        self.assertEqual(self.ok(A, url, {"event_type": "RUN_STARTED"})["count"], 2)
        self.assertEqual(self.ok(A, url, {"business_date": "2026-07-15"})["count"], 0)
        self.assertEqual(self.ok(A, url, {"user": ops.username, "module": "imps"})["count"], 1)

    def test_meta(self):
        body = self.ok(U, f"{AUDIT}/meta/")
        self.assertEqual([m["slug"] for m in body["modules"]], ["imps", "upi"])
        self.assertIn({"value": "LOGIN_FAILED", "label": "Failed login"}, body["user_activity_types"])
        self.assertEqual({k["value"] for k in body["settings_kinds"]}, {"response_code", "ntsl_mapping", "tolerance"})
        self.assertIn("SEEDED", [t["value"] for t in body["settings_event_types"]])

    def test_auditor_click_through_cannot_change_anything(self):
        """Slice 4 click-through: the Auditor sees the day's case events and the
        login log, and every write is refused."""
        case = self.make_case()
        self.assertGreater(self.ok(U, f"{AUDIT}/case-events/", {"business_date": D.isoformat()})["count"], 0)
        self.assertGreater(self.ok(U, f"{AUDIT}/user-activity/", {"event_type": "LOGIN"})["count"], 0)
        auditor = self.as_(U)
        self.assertEqual(self.post(auditor, f"{V1}/upi/cases/{case.pk}/take/").status_code, 403)
        self.assertEqual(self.post(auditor, f"{SETTINGS}/tolerance/", {"amount": "1.00", "txn_count": 0,
                                                                       "reason": "x"}).status_code, 403)


# ---------------------------------------------------------------------------
# C. users and roles
# ---------------------------------------------------------------------------
class UsersAndRolesTests(Slice4Base):
    def test_role_matrix(self):
        self.assert_roles(USERS, {A})

    def test_list_filters(self):
        gone = self.user(M, active=False)
        boss = self.user(superuser=True)
        boss.first_name = "Asha"
        boss.save()
        body = self.ok(A, USERS)
        by_name = {r["username"]: r for r in body["results"]}
        self.assertEqual(body["admin_users_url"], "/admin/auth/user/")
        self.assertEqual(by_name[self.norole.username]["roles"], [])
        self.assertEqual(by_name[gone.username]["is_active"], False)
        self.assertEqual((by_name[boss.username]["roles"], by_name[boss.username]["is_superuser"]),
                         (list(Role.ALL), True))
        self.assertIsNotNone(by_name[self.users[A].username]["last_login"])  # force_login set it
        makers = {r["username"] for r in self.ok(A, USERS, {"role": M})["results"]}
        self.assertEqual(makers, {self.users[M].username, gone.username, boss.username})
        self.assertEqual({r["username"] for r in self.ok(A, USERS, {"role": M, "active": "true"})["results"]},
                         {self.users[M].username, boss.username})
        self.assertEqual([r["username"] for r in self.ok(A, USERS, {"search": "asha"})["results"]], [boss.username])
        self.bad(A, USERS, {"role": "Boss"}, "role")
        self.bad(A, USERS, {"active": "1"}, "active")

    def test_admin_url_needs_staff(self):
        admin = self.users[A]
        admin.is_staff = False
        admin.save()
        self.assertIsNone(self.ok(admin, USERS)["admin_users_url"])


# ---------------------------------------------------------------------------
# D. Slice 3 fit items
# ---------------------------------------------------------------------------
class FitItemTests(Slice4Base):
    def setUp(self):
        super().setUp()
        self.rc = ResponseCode.objects.filter(payment_module=self.module, flag="RET", code__isnull=True).first()
        self.req = code_changes.propose_code_change(self.rc, self.users[M], new_code="116", reason="why")

    def test_meta_ntsl_choices(self):
        body = self.ok(M, f"{V1}/upi/meta/")
        self.assertEqual([t["value"] for t in body["ntsl_match_types"]], ["EXACT", "REGEX"])
        self.assertEqual([t["value"] for t in body["ntsl_txn_types"]], ["", "U2", "U3"])

    def test_requests_list_label_and_flags(self):
        url = f"{SETTINGS}/response-codes/requests/"
        for role in ALL_ROLES:
            with self.subTest(role=role):
                body = self.ok(role, url)
                self.assertEqual((body["can_propose"], body["can_decide"]), (role == M, role == C))
        row = self.ok(C, url)["results"][0]
        self.assertEqual(row["response_code_label"], str(self.rc))
        create = code_changes.propose_code_creation(self.module, self.users[M], flag=self.rc.flag,
                                                    new_description="Brand new", new_source="circular",
                                                    new_code=None, new_notes="", reason="new")
        rows = {r["id"]: r for r in self.ok(C, url)["results"]}
        self.assertIsNone(rows[create.pk]["response_code_label"])

    def test_request_detail_with_events(self):
        url = f"{SETTINGS}/response-codes/requests/{self.req.pk}/"
        self.assert_read_matrix(url)
        body = self.ok(C, url)
        self.assertEqual((body["id"], body["response_code_label"]), (self.req.pk, str(self.rc)))
        self.assertEqual([e["event_type"] for e in body["events"]], ["PROPOSED"])
        self.assertEqual(self.as_(C).get(f"{SETTINGS}/response-codes/requests/999999/").status_code, 404)
        imps = PaymentModule.objects.create(code="IMPS", name="IMPS")
        ResponseCodeChangeRequest.objects.filter(pk=self.req.pk).update(payment_module=imps)
        self.assertEqual(self.as_(C).get(url).status_code, 404)  # another module's request

    def test_pending_list_code_events(self):
        items = self.ok(C, f"{SETTINGS}/pending/", {"kind": "response_code"})["results"]
        self.assertEqual([e["event_type"] for e in items[0]["detail"]["events"]], ["PROPOSED"])
        self.assertEqual(items[0]["detail"]["response_code_label"], str(self.rc))
