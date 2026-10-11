"""Alert outbox (core.notifications): held when mail is off, sent when on,
failure + retry with backoff, rollback leaves nothing, users without email,
append-only content, drop-held, dedupe, commands."""
from __future__ import annotations

import smtplib
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail.backends.base import BaseEmailBackend
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import AlertType, Notification, NotificationImmutableError, NotificationStatus
from core.notifications import deliver_pending, drop_held_older_than, mail_enabled, notify, render

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
SMTP = "django.core.mail.backends.smtp.EmailBackend"
ON = dict(ALERTS_EMAIL_ENABLED=True, EMAIL_BACKEND=LOCMEM)


class FailingBackend(BaseEmailBackend):
    def send_messages(self, messages):
        raise smtplib.SMTPServerDisconnected("connection refused (test)")


class NotificationTests(TestCase):
    def setUp(self):
        model = get_user_model()
        self.u1 = model.objects.create_user("ops1", email="ops1@bank.example", password="x")
        self.u2 = model.objects.create_user("ops2", email="", password="x")
        self.inactive = model.objects.create_user("gone", email="gone@bank.example", password="x", is_active=False)

    def notify_test(self, recipients, **kw):
        with self.captureOnCommitCallbacks(execute=True):
            notify(AlertType.TEST, recipients, {"host": "h", "backend": "b"}, **kw)

    def test_render_subject_and_plain_text_body(self):
        subject, body = render(AlertType.REJECTED, {
            "rrn": "1", "business_date": "2026-09-25", "rule_code": "R01", "action_type": "DRC_102",
            "action_label": "DRC reply 102", "reason": "Wrong code: use 103 'manual'", "rejected_by": "chk",
            "link": "/x/",
        })
        self.assertEqual(subject, "[Reconciliation] Rejected: DRC_102 for RRN 1 (2026-09-25)")
        self.assertIn("Wrong code: use 103 'manual'", body)  # no HTML escaping in a text email

    @override_settings(ALERTS_EMAIL_ENABLED=False, EMAIL_HOST="")
    def test_held_when_disabled_and_nothing_sent(self):
        self.notify_test([self.u1, self.u2, self.inactive, self.u1])
        rows = {n.recipient_username: n.status for n in Notification.objects.all()}
        self.assertEqual(rows, {"ops1": NotificationStatus.HELD_NO_MAIL_SERVER, "ops2": NotificationStatus.SKIPPED_NO_EMAIL})
        r = deliver_pending()
        self.assertFalse(r.enabled)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Notification.objects.get(recipient=self.u1).status, NotificationStatus.HELD_NO_MAIL_SERVER)

    def test_mail_enabled_rules(self):
        with override_settings(ALERTS_EMAIL_ENABLED=True, EMAIL_BACKEND=SMTP, EMAIL_HOST=""):
            self.assertFalse(mail_enabled())  # enabled but no server
        with override_settings(ALERTS_EMAIL_ENABLED=True, EMAIL_BACKEND=SMTP, EMAIL_HOST="smtp.bank.local"):
            self.assertTrue(mail_enabled())
        with override_settings(ALERTS_EMAIL_ENABLED=False, EMAIL_BACKEND=SMTP, EMAIL_HOST="smtp.bank.local"):
            self.assertFalse(mail_enabled())

    def test_held_rows_sent_once_enabled(self):
        with override_settings(ALERTS_EMAIL_ENABLED=False):
            self.notify_test([self.u1])
        with override_settings(**ON):
            r = deliver_pending()
        self.assertEqual(r.sent, 1)
        n = Notification.objects.get()
        self.assertEqual((n.status, n.attempts), (NotificationStatus.SENT, 1))
        self.assertIsNotNone(n.sent_at)
        self.assertEqual((mail.outbox[0].to, mail.outbox[0].subject), (["ops1@bank.example"],
                                                                        "[Reconciliation] Test email"))
        with override_settings(**ON):
            self.assertEqual(deliver_pending().sent, 0)  # not twice

    @override_settings(**ON)
    def test_queued_when_enabled(self):
        self.notify_test([self.u1])
        self.assertEqual(Notification.objects.get().status, NotificationStatus.QUEUED)
        self.assertEqual(len(mail.outbox), 0)  # never sent inline

    @override_settings(ALERTS_EMAIL_ENABLED=True, EMAIL_BACKEND="core.tests.test_notifications.FailingBackend")
    def test_failure_retry_with_backoff_then_failed(self):
        self.notify_test([self.u1])
        t0 = timezone.now()
        r = deliver_pending(now=t0)
        n = Notification.objects.get()
        self.assertEqual((r.retry_later, n.status, n.attempts), (1, NotificationStatus.QUEUED, 1))
        self.assertIn("connection refused", n.last_error)
        self.assertEqual(n.next_attempt_at, t0 + timedelta(minutes=5))
        self.assertEqual(deliver_pending(now=t0 + timedelta(minutes=1)).retry_later, 0)  # backoff respected
        deliver_pending(now=t0 + timedelta(minutes=6))
        n.refresh_from_db()
        self.assertEqual((n.attempts, n.next_attempt_at), (2, t0 + timedelta(minutes=36)))
        r = deliver_pending(now=t0 + timedelta(minutes=40))
        n.refresh_from_db()
        self.assertEqual((r.failed, n.status, n.attempts, n.next_attempt_at), (1, NotificationStatus.FAILED, 3, None))
        self.assertEqual(deliver_pending(now=t0 + timedelta(days=1)).failed, 0)  # FAILED is final

    @override_settings(ALERTS_EMAIL_ENABLED=True, EMAIL_BACKEND="core.tests.test_notifications.FailingBackend")
    def test_success_after_a_failure(self):
        self.notify_test([self.u1])
        t0 = timezone.now()
        deliver_pending(now=t0)
        with override_settings(EMAIL_BACKEND=LOCMEM):
            deliver_pending(now=t0 + timedelta(minutes=6))
        n = Notification.objects.get()
        self.assertEqual((n.status, n.attempts, n.last_error), (NotificationStatus.SENT, 2, ""))

    def test_rolled_back_transaction_leaves_no_alert(self):
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    notify(AlertType.TEST, [self.u1], {})
                    raise RuntimeError("transition failed")
            except RuntimeError:
                pass
        self.assertFalse(Notification.objects.exists())

    def test_dedupe_key(self):
        self.notify_test([self.u1], dedupe_key="digest:maker:2026-10-11")
        self.notify_test([self.u1], dedupe_key="digest:maker:2026-10-11")
        self.notify_test([self.u1], dedupe_key="digest:maker:2026-10-12")
        self.assertEqual(Notification.objects.count(), 2)

    def test_content_append_only_delivery_fields_may_change(self):
        self.notify_test([self.u1])
        n = Notification.objects.get()
        Notification.objects.filter(pk=n.pk).update(attempts=1, last_error="x")  # delivery fields: allowed
        for fn in (
            lambda: Notification.objects.filter(pk=n.pk).update(subject="edited"),
            lambda: Notification.objects.filter(pk=n.pk).update(status="SENT", body="edited"),
            lambda: Notification.objects.all().delete(),
            n.delete,
        ):
            with self.assertRaises(NotificationImmutableError), transaction.atomic():
                fn()
        n.refresh_from_db()
        n.body = "edited"
        with self.assertRaises(NotificationImmutableError), transaction.atomic():
            n.save()
        n.refresh_from_db()
        n.status = NotificationStatus.DROPPED_STALE
        n.save()  # delivery-only change through save(): allowed
        self.assertEqual(Notification.objects.get().status, NotificationStatus.DROPPED_STALE)

    @override_settings(ALERTS_EMAIL_ENABLED=False)
    def test_drop_held_older_than(self):
        self.notify_test([self.u1])
        old = Notification.objects.get()
        Notification.objects.filter(pk=old.pk).update(next_attempt_at=None)
        from django.db import connection
        with connection.cursor() as cur:  # backdate (created_at is content; only a test may do this)
            cur.execute("UPDATE core_notification SET created_at = %s WHERE id = %s",
                        [timezone.now() - timedelta(days=5), old.pk])
        self.notify_test([self.u1, ])  # a fresh one (no dedupe key, so allowed)
        self.assertEqual(drop_held_older_than(2), 1)
        statuses = sorted(Notification.objects.values_list("status", flat=True))
        self.assertEqual(statuses, [NotificationStatus.DROPPED_STALE, NotificationStatus.HELD_NO_MAIL_SERVER])
        out = StringIO()
        with override_settings(**ON):
            call_command("send_notifications", "--drop-held-older-than", "0", stdout=out)
        self.assertIn("Dropped 1", out.getvalue())
        self.assertEqual(len(mail.outbox), 0)  # dropped, never mailed

    def test_send_notifications_command(self):
        with override_settings(ALERTS_EMAIL_ENABLED=False):
            self.notify_test([self.u1])
            out = StringIO()
            call_command("send_notifications", stdout=out)
            self.assertIn("Mail is not enabled", out.getvalue())
        with override_settings(**ON):
            out = StringIO()
            call_command("send_notifications", stdout=out)
        self.assertIn("Sent 1", out.getvalue())
        self.assertIn("SENT 1", out.getvalue())

    def test_send_test_email(self):
        with override_settings(ALERTS_EMAIL_ENABLED=False):
            with self.assertRaisesRegex(CommandError, "not enabled"):
                call_command("send_test_email", "it@bank.example", stdout=StringIO())
        with override_settings(**ON):
            out = StringIO()
            call_command("send_test_email", "it@bank.example", stdout=out)
        self.assertIn("sent to it@bank.example", out.getvalue())
        self.assertEqual(mail.outbox[-1].to, ["it@bank.example"])
        self.assertEqual(Notification.objects.filter(alert_type=AlertType.TEST, status="SENT").count(), 1)
        with self.assertRaises(CommandError):
            call_command("send_test_email", "not-an-address", stdout=StringIO())

    def test_admin_read_only_for_admin_and_auditor(self):
        from django.contrib.auth.models import Group

        self.notify_test([self.u1])
        url = "/admin/core/notification/"
        for group, code in (("Auditor", 200), ("Administrator", 200), ("Maker", 403), ("Checker", 403)):
            u = get_user_model().objects.create_user(f"a_{group}", password="x", is_staff=True)
            u.groups.add(Group.objects.get(name=group))
            self.client.force_login(u)
            self.assertEqual(self.client.get(url).status_code, code, group)
        n = Notification.objects.get()
        self.client.force_login(get_user_model().objects.get(username="a_Auditor"))
        self.assertEqual(self.client.get(f"{url}{n.pk}/delete/").status_code, 403)
