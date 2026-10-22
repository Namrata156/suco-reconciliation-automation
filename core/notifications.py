"""
Email-ready alert outbox (owner-approved plan, 2026-10-11).

  notify(alert_type, recipients, context, related=None, link_path="")
      Renders core/templates/notifications/<alert_type lower>.txt (first line
      = subject, the rest = body) and writes one core.Notification per
      recipient WHEN THE CURRENT TRANSACTION COMMITS (transaction.on_commit):
      a rolled-back case transition leaves no alert. Never sends mail itself.

  deliver_pending()
      Sends HELD/QUEUED rows with Django's send_mail when mail is enabled
      (mail_enabled()). Up to settings.ALERT_MAX_ATTEMPTS attempts per row,
      waiting ALERT_RETRY_BACKOFF_MINUTES between runs; then FAILED with the
      error. Run by `manage.py send_notifications` (or the Celery wrapper
      core.tasks.deliver_notifications) -- nothing needs a running worker.

  drop_held_older_than(days)
      Marks HELD rows older than `days` DROPPED_STALE, so turning mail on
      after a long time does not flood inboxes with old alerts.

Status on creation: SKIPPED_NO_EMAIL if the user has no address; else
QUEUED when mail is enabled, HELD_NO_MAIL_SERVER when not. Recipients:
active users only, each once.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.mail import send_mail
from django.db import transaction
from django.template import Context, Engine
from django.utils import timezone

from core.models import (
    PENDING_NOTIFICATION_STATUSES,
    Notification,
    NotificationStatus,
)

logger = logging.getLogger(__name__)

_SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"


def mail_enabled() -> bool:
    """True when alerts may be mailed: ALERTS_EMAIL_ENABLED and a mail
    server (EMAIL_HOST), or a non-SMTP backend (console / locmem for
    checking and tests)."""
    if not getattr(settings, "ALERTS_EMAIL_ENABLED", False):
        return False
    return bool(settings.EMAIL_HOST) or settings.EMAIL_BACKEND != _SMTP_BACKEND


_ENGINE = None


def _engine() -> Engine:
    # Plain-text mail: no HTML escaping (the project's DjangoTemplates engine
    # autoescapes, which would turn ' into &#x27; in the email).
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = Engine(app_dirs=True, autoescape=False)
    return _ENGINE


def render(alert_type: str, context: dict) -> tuple[str, str]:
    template = _engine().get_template(f"notifications/{alert_type.lower()}.txt")
    text = template.render(Context(context, autoescape=False))
    first, _, rest = text.strip().partition("\n")
    subject = " ".join(first.removeprefix("Subject:").split())[:255]
    return subject, rest.strip() + "\n"


def link(path: str) -> str:
    return f"{settings.ALERT_LINK_BASE_URL}{path}"


def _rows(alert_type, recipients, subject, body, related, dedupe_key, link_path="") -> list[Notification]:
    held_or_queued = NotificationStatus.QUEUED if mail_enabled() else NotificationStatus.HELD_NO_MAIL_SERVER
    ct = ContentType.objects.get_for_model(related) if related is not None else None
    rows, seen = [], set()
    for user in recipients:
        if user is None or user.pk in seen or not user.is_active:
            continue
        seen.add(user.pk)
        email = (user.email or "").strip()
        rows.append(Notification(
            alert_type=alert_type, recipient=user, recipient_username=user.get_username(), recipient_email=email,
            subject=subject, body=body, content_type=ct, object_id=related.pk if related is not None else None,
            dedupe_key=dedupe_key, link_path=link_path,
            status=held_or_queued if email else NotificationStatus.SKIPPED_NO_EMAIL,
        ))
    return rows


def notify(alert_type: str, recipients, context: dict, related=None, *, dedupe_key: str = "",
           link_path: str = "") -> None:
    """Records the alert for every recipient once the current transaction
    commits (immediately when not in a transaction). Rendered now, written
    later; a dedupe_key makes a second identical alert to the same user a
    no-op (e.g. the daily digest run twice). link_path is the SPA route the
    alert is about (/upi/cases/123), kept for the in-app alert list."""
    subject, body = render(alert_type, context)
    recipients = list(recipients)

    def write():
        rows = _rows(alert_type, recipients, subject, body, related, dedupe_key, link_path)
        if rows:
            Notification.objects.bulk_create(rows, ignore_conflicts=bool(dedupe_key))

    transaction.on_commit(write)


@dataclass
class DeliveryReport:
    enabled: bool
    sent: int = 0
    retry_later: int = 0
    failed: int = 0
    dropped: int = 0
    errors: list = field(default_factory=list)


def drop_held_older_than(days: int) -> int:
    cutoff = timezone.now() - timedelta(days=days)
    return Notification.objects.filter(
        status=NotificationStatus.HELD_NO_MAIL_SERVER, created_at__lt=cutoff
    ).update(status=NotificationStatus.DROPPED_STALE, last_error=f"Held more than {days} day(s); not sent")


def deliver_pending(*, ids=None, now=None, limit: int = 500) -> DeliveryReport:
    report = DeliveryReport(enabled=mail_enabled())
    if not report.enabled:
        return report
    now = now or timezone.now()
    max_attempts = settings.ALERT_MAX_ATTEMPTS
    backoff = settings.ALERT_RETRY_BACKOFF_MINUTES
    due = Notification.objects.filter(status__in=PENDING_NOTIFICATION_STATUSES).exclude(next_attempt_at__gt=now)
    if ids is not None:
        due = due.filter(pk__in=list(ids))
    for pk in list(due.order_by("created_at", "id").values_list("pk", flat=True)[:limit]):
        with transaction.atomic():
            n = (Notification.objects.select_for_update(skip_locked=True)
                 .filter(pk=pk, status__in=PENDING_NOTIFICATION_STATUSES).first())
            if n is None:  # taken by a concurrent run
                continue
            try:
                send_mail(n.subject, n.body, settings.DEFAULT_FROM_EMAIL, [n.recipient_email], fail_silently=False)
            except Exception as exc:  # noqa: BLE001 -- any delivery error is recorded, never raised
                attempts = n.attempts + 1
                error = f"{type(exc).__name__}: {exc}"[:2000]
                if attempts >= max_attempts:
                    status, retry_at = NotificationStatus.FAILED, None
                    report.failed += 1
                else:
                    status = NotificationStatus.QUEUED
                    retry_at = now + timedelta(minutes=backoff[min(attempts, len(backoff)) - 1])
                    report.retry_later += 1
                Notification.objects.filter(pk=pk).update(
                    status=status, attempts=attempts, last_error=error, next_attempt_at=retry_at
                )
                report.errors.append(f"{pk}: {error}")
                logger.warning("Notification %s: attempt %s failed: %s", pk, attempts, error)
            else:
                Notification.objects.filter(pk=pk).update(
                    status=NotificationStatus.SENT, attempts=n.attempts + 1, sent_at=timezone.now(),
                    last_error="", next_attempt_at=None,
                )
                report.sent += 1
    return report
