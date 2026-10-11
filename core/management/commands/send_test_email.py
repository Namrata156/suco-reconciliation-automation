"""
Records a TEST alert to the given address and delivers just that one now,
to check the mail settings.

    manage.py send_test_email ops@bank.example
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

from core.models import AlertType, Notification, NotificationStatus
from core.notifications import deliver_pending, mail_enabled, render


class Command(BaseCommand):
    help = "Sends one test email through the alert outbox."

    def add_arguments(self, parser):
        parser.add_argument("address")

    def handle(self, *args, **options):
        try:
            validate_email(options["address"])
        except ValidationError as exc:
            raise CommandError(f"Not an email address: {options['address']}") from exc
        subject, body = render(AlertType.TEST, {
            "host": settings.EMAIL_HOST or "(none)", "backend": settings.EMAIL_BACKEND,
        })
        enabled = mail_enabled()
        n = Notification.objects.create(
            alert_type=AlertType.TEST, recipient_email=options["address"], subject=subject, body=body,
            status=NotificationStatus.QUEUED if enabled else NotificationStatus.HELD_NO_MAIL_SERVER,
        )
        if not enabled:
            raise CommandError(
                f"Mail is not enabled (ALERTS_EMAIL_ENABLED={settings.ALERTS_EMAIL_ENABLED}, "
                f"EMAIL_HOST={settings.EMAIL_HOST or 'blank'}); test alert {n.pk} recorded as HELD")
        r = deliver_pending(ids=[n.pk])
        n.refresh_from_db()
        if n.status == NotificationStatus.SENT:
            self.stdout.write(self.style.SUCCESS(f"Test email {n.pk} sent to {options['address']}"))
        else:
            raise CommandError(f"Test email {n.pk} not sent ({n.status}): {n.last_error}")
