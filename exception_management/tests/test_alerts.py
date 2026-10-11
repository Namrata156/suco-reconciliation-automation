"""Case alerts (exception_management.alerts): recipients per event, one
alert per bulk request / sync run, digest contents."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from io import StringIO

from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from core.models import AlertType, Notification, NotificationStatus
from exception_management import services
from exception_management.actions import ActionType
from exception_management.alerts import send_digest
from exception_management.models import ExceptionCase
from exception_management.roles import Role
from exception_management.tests.base import D, CaseTestBase


class AlertTestBase(CaseTestBase):
    def setUp(self):
        super().setUp()
        self.m1 = self.user(Role.MAKER)
        self.m2 = self.user(Role.MAKER)
        self.c1 = self.user(Role.CHECKER)
        self.c2 = self.user(Role.CHECKER)
        self.aud = self.user(Role.AUDITOR)
        self.gone = self.user(Role.CHECKER, active=False)
        for u in (self.m1, self.m2, self.c1, self.c2, self.aud):
            u.email = f"{u.username}@bank.example"
            u.save()

    def alerts(self, alert_type):
        return sorted(Notification.objects.filter(alert_type=alert_type).values_list("recipient_username", flat=True))

    def names(self, *users):
        return sorted(u.username for u in users)


class EventAlertTests(AlertTestBase):
    def test_r07x_case_created(self):
        self.result("100000000001", "R07x")
        self.result("100000000002", "R01")
        with self.captureOnCommitCallbacks(execute=True):
            self.sync()
        self.assertEqual(self.alerts(AlertType.R07X_CASE), self.names(self.m1, self.m2, self.c1, self.c2))
        n = Notification.objects.filter(alert_type=AlertType.R07X_CASE).first()
        self.assertIn("100000000001", n.body)
        self.assertEqual((n.content_type, n.object_id),
                         (ContentType.objects.get_for_model(ExceptionCase), self.case("100000000001").pk))
        with self.captureOnCommitCallbacks(execute=True):
            self.sync()  # not again
        self.assertEqual(Notification.objects.filter(alert_type=AlertType.R07X_CASE).count(), 4)

    def r07x_alerts(self):
        return list(Notification.objects.filter(alert_type=AlertType.R07X_CASE, recipient=self.m1))

    def test_case_changed_to_r07x_alerts_once(self):
        self.result("100000000001", "R07b")
        with self.captureOnCommitCallbacks(execute=True):
            self.sync()
        self.assertEqual(self.r07x_alerts(), [])
        self.rerun({"100000000001": "R07x"})
        with self.captureOnCommitCallbacks(execute=True):
            self.sync()
        self.assertEqual(self.alerts(AlertType.R07X_CASE), self.names(self.m1, self.m2, self.c1, self.c2))
        (n,) = self.r07x_alerts()
        self.assertIn("100000000001", n.body)
        self.assertIn("changed to R07x", n.body)
        self.rerun({"100000000001": "R07x"})  # still R07x: no new alert
        with self.captureOnCommitCallbacks(execute=True):
            self.sync()
        self.assertEqual(len(self.r07x_alerts()), 1)

    def test_unchanged_r07x_does_not_realert(self):
        self.result("100000000001", "R07x")
        with self.captureOnCommitCallbacks(execute=True):
            self.sync()
        for _ in range(2):
            self.rerun({"100000000001": "R07x"})
            with self.captureOnCommitCallbacks(execute=True):
                self.sync()
        self.assertEqual(len(self.r07x_alerts()), 1)  # the creation alert only

    def test_new_and_changed_in_one_run_one_summary(self):
        self.result("100000000001", "R07b")
        self.result("100000000003", "R07x")  # already R07x: must not be listed again
        with self.captureOnCommitCallbacks(execute=True):
            self.sync()
        self.rerun({"100000000001": "R07x", "100000000002": "R07x", "100000000003": "R07x"})
        with self.captureOnCommitCallbacks(execute=True):
            s = self.sync()
        self.assertEqual(s.created, 1)
        alerts = self.r07x_alerts()
        self.assertEqual(len(alerts), 2)  # creation alert for ...003, then ONE summary for this run
        latest = max(alerts, key=lambda n: n.pk)
        self.assertIn("2 chargeback cases", latest.subject)
        self.assertIn("100000000001", latest.body)
        self.assertIn("100000000002", latest.body)
        self.assertNotIn("100000000003", latest.body)
        self.assertEqual(Notification.objects.filter(alert_type=AlertType.R07X_CASE).count(), 8)  # 2 alerts x 4 people

    def test_needs_review_one_alert_per_sync_to_checkers(self):
        for i in range(2):
            c = self.make_case(f"20000000000{i}", "R01")
            services.take(c, self.m1)
            services.submit(c, self.m1)
            services.approve(c, self.c1)
        self.rerun({"200000000000": "R02", "200000000001": "R02"})
        with self.captureOnCommitCallbacks(execute=True):
            self.sync()
        self.assertEqual(self.alerts(AlertType.NEEDS_REVIEW), self.names(self.c1, self.c2))  # one alert, 2 cases
        body = Notification.objects.filter(alert_type=AlertType.NEEDS_REVIEW).first().body
        self.assertIn("200000000000", body)
        self.assertIn("200000000001", body)

    def test_submit_goes_to_checkers_except_submitter(self):
        both = self.user(Role.MAKER, Role.CHECKER)
        c = self.make_case()
        services.take(c, both)
        with self.captureOnCommitCallbacks(execute=True):
            services.submit(c, both)
        self.assertEqual(self.alerts(AlertType.SUBMITTED), self.names(self.c1, self.c2))  # not `both`, not inactive

    def test_submit_by_the_only_checker_goes_to_them(self):
        both = self.user(Role.MAKER, Role.CHECKER)
        from django.contrib.auth.models import Group

        Group.objects.get(name=Role.CHECKER).user_set.remove(self.c1, self.c2)
        c = self.make_case()
        services.take(c, both)
        with self.captureOnCommitCallbacks(execute=True):
            services.submit(c, both)
        self.assertEqual(self.alerts(AlertType.SUBMITTED), [both.username])

    def test_reject_goes_to_the_maker(self):
        c = self.make_case()
        services.take(c, self.m1)
        services.submit(c, self.m1)
        with self.captureOnCommitCallbacks(execute=True):
            services.reject(c, self.c1, reason="Use 103")
        self.assertEqual(self.alerts(AlertType.REJECTED), [self.m1.username])
        self.assertIn("Use 103", Notification.objects.get(alert_type=AlertType.REJECTED).body)

    def test_rolled_back_transition_no_alert(self):
        c = self.make_case("100000000009", "R13")
        services.take(c, self.m1)
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(services.MissingInput):
                services.submit(c, self.m1, action_type=ActionType.CLOSE_NO_ACTION)  # no remarks -> refused
        self.assertFalse(Notification.objects.exists())

    def test_bulk_submit_one_summary_alert(self):
        cases = [self.make_case(f"30000000000{i}", "R01") for i in range(3)]
        services.bulk_take(cases, self.m1)
        with self.captureOnCommitCallbacks(execute=True):
            services.bulk_submit(cases, self.m1)
        self.assertFalse(Notification.objects.filter(alert_type=AlertType.SUBMITTED).exists())
        self.assertEqual(self.alerts(AlertType.BULK_SUBMITTED), self.names(self.c1, self.c2))  # one per checker
        n = Notification.objects.filter(alert_type=AlertType.BULK_SUBMITTED).first()
        self.assertIn("3 cases submitted in bulk", n.subject)

    def test_refused_bulk_no_alert(self):
        cases = [self.make_case("300000000001", "R01"), self.make_case("300000000002", "R05")]
        services.bulk_take(cases, self.m1)
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(services.BulkRefused):
                services.bulk_submit(cases, self.m1)
        self.assertFalse(Notification.objects.exists())

    def test_user_without_email_skipped(self):
        self.m2.email = ""
        self.m2.save()
        self.result("100000000001", "R07x")
        with self.captureOnCommitCallbacks(execute=True):
            self.sync()
        self.assertEqual(Notification.objects.get(recipient=self.m2).status, NotificationStatus.SKIPPED_NO_EMAIL)


class DigestTests(AlertTestBase):
    def test_digest_contents_and_once_a_day(self):
        now = timezone.make_aware(datetime.combine(D + timedelta(days=1), time(9, 30)))  # D+1 09:30
        self.result("100000000001", "R05", due=D + timedelta(days=1), cutoff=time(16))  # HIGH, due today, not overdue
        self.result("100000000002", "R12", due=D)  # HIGH, overdue
        self.result("100000000003", "R01", due=D + timedelta(days=1))  # LOW, due today
        self.result("100000000004", "R01")
        self.sync()
        c4 = self.case("100000000004")
        services.take(c4, self.m1)
        services.submit(c4, self.m1)
        services.take(self.case("100000000002"), self.m1)

        s = send_digest(now, dry_run=True)
        self.assertEqual(s[Role.MAKER], {"recipients": 2, "high_open": 2, "high_overdue": 1, "overdue": 1,
                                         "due_today": 2, "approved_waiting": 0})
        self.assertEqual(s[Role.CHECKER], {"recipients": 2, "awaiting": 1, "awaiting_high": 0, "needs_review": 0})
        self.assertFalse(Notification.objects.exists())  # dry run

        with self.captureOnCommitCallbacks(execute=True):
            send_digest(now)
        self.assertEqual(self.alerts(AlertType.DIGEST_MAKER), self.names(self.m1, self.m2))
        self.assertEqual(self.alerts(AlertType.DIGEST_CHECKER), self.names(self.c1, self.c2))
        m1 = Notification.objects.get(alert_type=AlertType.DIGEST_MAKER, recipient=self.m1)
        self.assertIn("2 open HIGH, 1 overdue, 2 due today", m1.subject)
        self.assertIn("Assigned to you:              1 (of which overdue: 1)", m1.body)
        chk = Notification.objects.get(alert_type=AlertType.DIGEST_CHECKER, recipient=self.c1)
        self.assertIn("1 case awaiting approval", chk.subject)
        with self.captureOnCommitCallbacks(execute=True):
            send_digest(now)  # same day again: no duplicates
        self.assertEqual(Notification.objects.filter(alert_type__startswith="DIGEST").count(), 4)

    def test_command_dry_run(self):
        self.result("100000000001", "R05")
        self.sync()
        out = StringIO()
        call_command("send_alert_digest", "--dry-run", stdout=out)
        self.assertIn("Would record Maker digest for 2 recipient(s)", out.getvalue())
        self.assertIn("Would record Checker digest for 2 recipient(s)", out.getvalue())
        self.assertFalse(Notification.objects.exists())

    @override_settings(ALERTS_EMAIL_ENABLED=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_end_to_end_mail(self):
        from django.core import mail

        self.result("100000000001", "R07x")
        with self.captureOnCommitCallbacks(execute=True):
            self.sync()
        call_command("send_notifications", stdout=StringIO())
        self.assertEqual(sorted(m.to[0] for m in mail.outbox),
                         sorted(f"{u.username}@bank.example" for u in (self.m1, self.m2, self.c1, self.c2)))
