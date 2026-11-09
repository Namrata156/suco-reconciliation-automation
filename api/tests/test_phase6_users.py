"""
Phase 6 Slice 6.4 (docs/phase6_api_contract.txt B7): the user activity
report, and its download logged with no module (PM decision 2026-11-08:
OperationLog.payment_module may be NULL). Every user and IP is made up.
"""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from api.tests.test_phase6_daily import D, Phase6Base, csv_lines
from api.tests.test_v1 import ALL_ROLES, V1
from core.models import OperationType, PaymentModule, UserActivity, UserActivityType
from core.operations import log_operation
from exception_management import services
from exception_management.roles import Role
from settlement import tolerance

A, O, M, C, U = Role.ADMINISTRATOR, Role.OPERATIONS_USER, Role.MAKER, Role.CHECKER, Role.AUDITOR
URL = f"{V1}/reports/user-activity/"


class UserActivityReportTests(Phase6Base):
    def setUp(self):
        super().setUp()
        self.mk, self.chk = self.users[M], self.users[C]
        today = timezone.localdate()
        self.range = {"date_from": (today - timedelta(days=1)).isoformat(), "date_to": today.isoformat()}
        for ip in ("10.0.0.1", "10.0.0.2"):
            UserActivity.objects.create(activity_type=UserActivityType.LOGIN, user=self.mk,
                                        username=self.mk.username, ip_address=ip)
        UserActivity.objects.create(activity_type=UserActivityType.LOGIN_FAILED, user=self.mk,
                                    username=self.mk.username)
        UserActivity.objects.create(activity_type=UserActivityType.LOGIN_FAILED, user=None,
                                    username=self.mk.username.upper())  # typed name, no user matched
        case = self.make_case("100000000001", "R01", D)
        services.take(case, self.mk)
        services.add_remark(case, self.mk, "looked")
        services.submit(case, self.mk)
        services.approve(case, self.chk)
        log_operation(OperationType.FILE_UPLOADED, user=self.ops, payment_module=self.module, source="api")
        log_operation(OperationType.RUN_STARTED, user=self.ops, payment_module=self.module, source="api")
        self.imps = PaymentModule.objects.create(code="IMPS", name="IMPS")
        log_operation(OperationType.FILE_UPLOADED, user=self.ops, payment_module=self.imps, source="api")
        t = tolerance.propose_tolerance_change(self.module, self.users[A], amount="1", txn_count=0, reason="r")
        tolerance.approve_tolerance_change(t, self.chk)

    def rows(self, who=U, **params):
        return {r["username"]: r for r in self.ok(who, URL, {**self.range, **params})["results"]}

    def test_roles(self):
        for role in ALL_ROLES:
            self.assertEqual(self.as_(role).get(URL).status_code, 200 if role in (A, U) else 403, role)
        self.assertEqual(self.as_(None).get(URL).status_code, 401)

    def test_counts(self):
        rows = self.rows()
        self.assertEqual(len(rows), len(self.users) + 1)  # every user, incl. no activity / no role
        mk = rows[self.mk.username]
        self.assertEqual((mk["logins"], mk["failed_logins"], mk["last_login_ip"], mk["roles"], mk["is_active"]),
                         (2, 2, "10.0.0.2", ["Maker"], True))
        self.assertEqual(mk["cases"], {"taken": 1, "submitted": 1, "approved": 0, "rejected": 0, "marked_done": 0,
                                       "remarks": 1})
        self.assertEqual(rows[self.chk.username]["cases"]["approved"], 1)
        self.assertEqual(rows[self.chk.username]["settings"], {"proposed": 0, "decided": 1})
        self.assertEqual(rows[self.users[A].username]["settings"], {"proposed": 1, "decided": 0})
        ops = rows[self.ops.username]
        self.assertEqual((ops["files"], ops["runs_started"], ops["logins"]),
                         ({"uploaded": 2, "refused": 0}, 1, 0))
        self.assertTrue(mk["links"]["case_events"].startswith(f"/audit/case-events?user={self.mk.username}&date_from="))
        # module filter: work counts only, never the logins
        upi = self.rows(module="upi")
        self.assertEqual((upi[self.ops.username]["files"]["uploaded"], upi[self.mk.username]["logins"]), (1, 2))
        imps = self.rows(module="imps")
        self.assertEqual((imps[self.ops.username]["files"]["uploaded"], imps[self.mk.username]["cases"]["taken"],
                          imps[self.chk.username]["settings"]["decided"]), (1, 0, 0))
        # other filters, and the range basis (event timestamp)
        self.assertEqual(list(self.rows(role="Checker")), [self.chk.username])
        self.assertEqual(list(self.rows(user=self.mk.username.upper())), [self.mk.username])
        self.users[O].is_active = False
        self.users[O].save()
        self.assertEqual(list(self.rows(active="false")), [self.ops.username])
        old = (timezone.localdate() - timedelta(days=30)).isoformat()
        self.assertEqual(self.rows(date_from=old, date_to=old)[self.mk.username]["logins"], 0)
        for params, field in (({"role": "Boss"}, "role"), ({"module": "xyz"}, "module"),
                              ({"active": "yes"}, "active")):
            self.bad(U, URL, {**self.range, **params}, field)
        body = self.ok(A, URL, self.range)
        self.assertEqual((body["report"]["key"], body["report"]["module"]), ("user_activity", None))

    def test_download_is_logged_with_no_module(self):
        r = self.as_(U).get(URL, {**self.range, "export": "csv"})
        self.assertEqual(r["Content-Disposition"],
                         f'attachment; filename="user_activity_{self.range["date_from"]}_to_'
                         f'{self.range["date_to"]}.csv"')
        lines = csv_lines(r)
        self.assertEqual(lines[1], "Module,All modules")
        self.assertTrue(lines[10].startswith("User,Name,Roles,Active,Logins,Failed logins"))
        log = self.exports().get()
        self.assertEqual((log.payment_module, log.details["report"], log.details["row_count"]),
                         (None, "user_activity", len(self.users) + 1))
        audit = self.ok(U, f"{V1}/audit/operations/", {"event_type": "REPORT_EXPORTED"})["results"]
        self.assertEqual((audit[0]["module"], audit[0]["link"]), (None, None))
        self.assertEqual(self.rows()[self.users[U].username]["report_downloads"], 1)
