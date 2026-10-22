"""
`core` app: shared reference data + enums used across payment modules and
(later) reconciliation phases.

Per the approved plan (Phase 1, "Database Design + File Processing
Framework"): `PaymentModule` and `SettlementCycle` are real DB tables
(admin-editable reference data). `RegisterType` and `Direction` are Django
`TextChoices` rather than tables, since they're a closed enum set, not
business-editable data.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q

from core.append_only import AppendOnlyError, AppendOnlyModel


class RegisterType(models.TextChoices):
    """
    Which source register/file-type a given uploaded file (and, later, a
    normalized transaction row) belongs to.

    NOTE: no "Bank Switch" source file exists anywhere in the spec or the
    real sample data (see TRACKER.md Open Questions / the approved plan's
    open question #7), yet the Transaction Processing Engine's Transaction
    Presence Matrix / 3-way-4-way recon design assumes a Switch register.
    SWITCH is included here only so the enum's shape is stable for Phase 2 --
    nothing in this app writes rows with it, and it should not be read as
    confirmation that a Switch source file has been identified.
    """

    CBS_GL_EXTRACT = "CBS_GL_EXTRACT", "CBS GL Extract"
    RAW_DATA = "RAW_DATA", "Raw Data"
    MERCHANT_RAW_DATA = "MERCHANT_RAW_DATA", "Merchant Raw Data"
    MERCHANT_RAW_DATA_BBP = (
        "MERCHANT_RAW_DATA_BBP",
        "Merchant Raw Data (BBP) -- kept separately identifiable from standard "
        "Merchant Raw Data ACQ for audit/reporting (project owner decision, 2026-09-10, "
        "see doubts.md); identical row shape/parsing to standard Merchant Raw Data ACQ, "
        "classification only",
    )
    NTSL = "NTSL", "NTSL"
    DRC = "DRC", "Debit Reversal Confirmation Report"
    TIMEOUT = "TIMEOUT", "Timeout Report"
    ADJUSTMENT = "ADJUSTMENT", "Adjustment Report"
    SWITCH = "SWITCH", "Bank Switch (no source file identified yet -- see Open Questions)"


class Direction(models.TextChoices):
    DEBIT = "DEBIT", "Debit"
    CREDIT = "CREDIT", "Credit"
    UNKNOWN = "UNKNOWN", "Unknown"


class PaymentModule(models.Model):
    """
    A payment rail the platform reconciles: UPI is the initial scope; IMPS,
    NEFT, RTGS, ATM are planned follow-ons on the same architecture (see
    claude.md).
    """

    code = models.CharField(max_length=20, unique=True, help_text="e.g. UPI, IMPS, NEFT, RTGS, ATM")
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.code


class SettlementCycleType(models.TextChoices):
    """
    What a settlement cycle settles, per the NPCI circular's "Annexure I --
    Revised Settlement Cycles" (approved by the project owner 2026-09-27):
    cycles 1-10 settle AUTH (authorised transactions) only, with no
    disputes; DC1/DC2 are dispute-only settlement cycles. Both kinds are
    real money settlements -- the previous FINANCIAL / NON_FINANCIAL
    labels wrongly implied DC1/DC2 moved no money (renamed by
    core/migrations/0003).
    """

    AUTH = "AUTH", "AUTH (authorised transactions only, no disputes)"
    DISPUTE = "DISPUTE", "Disputes only"


class SettlementCycle(models.Model):
    """
    A settlement cycle within a payment module's business day.

    Shared across Raw Data / Merchant Raw Data / NTSL, which are the only
    cycle-wise file types per the approved plan's sample-data findings (CBS
    GL Extract, DRC, Timeout, and Adjustment reports are single daily
    files, not cycle-wise).

    UPI has 12 settlement cycles per the NPCI circular's "Annexure I --
    Revised Settlement Cycles" (approved by the project owner 2026-09-27):
    1C-10C are AUTH-only settlement cycles (no disputes), and DC1
    (00:00-16:00) / DC2 (16:00-00:00) are dispute-only settlement cycles.
    Only NTSL comes in all 12 cycles (its filenames carry "DC1"/"DC2");
    the other cycle-wise NPCI files come in (a subset of) 1C-10C only -- see each
    FileTypeConfig.cycle_set. The spec/SOP's "8 cycles" is stale (see
    doubts.md / TRACKER.md).

    Cycle rows are admin-editable reference data, not hardcoded. A fresh
    DB gets them from `manage.py seed_settlement_cycles`; the 2026-09-27
    correction of existing rows was applied by core/migrations/0003.
    """

    payment_module = models.ForeignKey(
        PaymentModule, on_delete=models.PROTECT, related_name="settlement_cycles"
    )
    cycle_code = models.CharField(max_length=10, help_text="e.g. '1C'..'10C', 'DC1', 'DC2'")
    cycle_type = models.CharField(max_length=20, choices=SettlementCycleType.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    rtgs_settlement_time = models.TimeField(
        null=True,
        blank=True,
        help_text="RTGS settlement time for this cycle, per NPCI Annexure I (e.g. 1C -> 04:00).",
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["payment_module", "cycle_code"], name="unique_cycle_per_module"
            )
        ]
        ordering = ["payment_module", "cycle_code"]

    def __str__(self) -> str:
        return f"{self.payment_module.code}-{self.cycle_code}"


class BankHoliday(models.Model):
    """
    One bank holiday on the working-day calendar used for rule deadlines
    (T+n = the n-th working day after T; see core.working_days). Bank-wide,
    not per payment module -- the same calendar serves every rail and later
    phases (settlement, NPCI file generation).

    Working-day rule (project owner, 2026-10-10): a day is NOT a working day
    if it is a Sunday, the 2nd or 4th Saturday of the month, or a holiday in
    this table for the region. Only the holidays are stored; Sundays and
    2nd/4th Saturdays are computed.

    Seeded from RBI's holiday matrix (docs/bank_holidays_bengaluru_2026.txt)
    by data migration core/0005 and `manage.py seed_bank_holidays`. Ops
    confirms the list once a year and adds bank-specific or late-announced
    holidays. A seeded row starts with confirmed_by_ops=False; the 2026
    Karnataka list was confirmed by ops (project owner, 2026-10-10) and
    marked True by data migration core/0006. Re-seeding never resets it.
    """

    date = models.DateField()
    name = models.CharField(max_length=200)
    region = models.CharField(
        max_length=50,
        help_text="Calendar region, e.g. 'Karnataka' (RBI Bengaluru regional office)",
    )
    source_url = models.URLField(max_length=500)
    retrieved_on = models.DateField(help_text="Date the list was read from the source")
    confirmed_by_ops = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["region", "date"], name="unique_bank_holiday_per_region_date"),
        ]
        ordering = ["region", "date"]

    def __str__(self) -> str:
        return f"{self.date} {self.name} ({self.region})"


# ---------------------------------------------------------------------------
# User activity log (Phase 3 design section 5; report in Phase 6)
# ---------------------------------------------------------------------------
class UserActivityType(models.TextChoices):
    LOGIN = "LOGIN", "Login"
    LOGOUT = "LOGOUT", "Logout"
    LOGIN_FAILED = "LOGIN_FAILED", "Failed login"


class UserActivity(AppendOnlyModel):
    """
    One login, logout or failed login, written by core.signals from Django's
    auth signals (admin and API both log in through django.contrib.auth).
    Append-only (core.append_only). `user` is NULL for a failed login with
    an unknown username and for a logout of an already-expired session;
    `username` is what was typed / the user's name at the time. `ip_address`
    is REMOTE_ADDR only: X-Forwarded-For is not trusted until the deployment
    has a known reverse proxy.
    """

    activity_type = models.CharField(max_length=20, choices=UserActivityType.choices)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    username = models.CharField(max_length=150, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    path = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "user activity"

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} {self.activity_type} {self.username}"


# ---------------------------------------------------------------------------
# Operations log (Phase 5 design 3.6 + Amendment 1; Slice 2)
# ---------------------------------------------------------------------------
class OperationType(models.TextChoices):
    FILE_UPLOADED = "FILE_UPLOADED", "File uploaded"
    FILE_REFUSED = "FILE_REFUSED", "File upload refused"
    BUSINESS_DATE_SELECTED = "BUSINESS_DATE_SELECTED", "Business date selected (CBS)"
    RUN_STARTED = "RUN_STARTED", "Reconciliation run started"


class OperationLog(AppendOnlyModel):
    """
    Who did which operations step, when (design 3.6): uploading a file,
    refusing one, choosing a CBS business date, starting a reconciliation
    run. Append-only (core.append_only), like CaseEvent and UserActivity.

    `uploaded_file_id` and `run_id` are plain ids, not foreign keys: core is
    below file_processing and reconciliation, and an audit row must outlive
    anything it mentions. `details` holds what the step needs to be read
    later (file name, hash, refusal reasons, detected dates...).
    """

    operation = models.CharField(max_length=30, choices=OperationType.choices)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    username = models.CharField(max_length=150, blank=True)
    payment_module = models.ForeignKey(PaymentModule, on_delete=models.PROTECT, related_name="+")
    business_date = models.DateField(null=True, blank=True)
    uploaded_file_id = models.BigIntegerField(null=True, blank=True)
    run_id = models.BigIntegerField(null=True, blank=True)
    source = models.CharField(max_length=30, blank=True, help_text="api / admin / ...")
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["payment_module", "business_date"], name="oplog_module_date_idx"),
            models.Index(fields=["uploaded_file_id"], name="oplog_file_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} {self.operation} {self.username}"


# ---------------------------------------------------------------------------
# Notification outbox (email-ready alerts; owner-approved plan 2026-10-11)
# ---------------------------------------------------------------------------
class AlertType(models.TextChoices):
    R07X_CASE = "R07X_CASE", "R07x case created (chargeback > 35 days)"
    NEEDS_REVIEW = "NEEDS_REVIEW", "Cases flagged for review after re-evaluation"
    SUBMITTED = "SUBMITTED", "Action submitted for approval"
    BULK_SUBMITTED = "BULK_SUBMITTED", "Actions submitted in bulk"
    REJECTED = "REJECTED", "Proposed action rejected"
    DIGEST_MAKER = "DIGEST_MAKER", "Daily digest (Maker)"
    DIGEST_CHECKER = "DIGEST_CHECKER", "Daily digest (Checker)"
    SETTLEMENT_CASE = "SETTLEMENT_CASE", "Settlement case raised (NTSL difference unexplained)"
    SETTLEMENT_REVIEW = "SETTLEMENT_REVIEW", "Settlement case flagged for review after a re-run"
    TEST = "TEST", "Test email"


class NotificationStatus(models.TextChoices):
    HELD_NO_MAIL_SERVER = "HELD_NO_MAIL_SERVER", "Held: mail not enabled / no mail server"
    QUEUED = "QUEUED", "Queued for sending"
    SENT = "SENT", "Sent"
    FAILED = "FAILED", "Failed (all attempts used)"
    SKIPPED_NO_EMAIL = "SKIPPED_NO_EMAIL", "Skipped: recipient has no email address"
    DROPPED_STALE = "DROPPED_STALE", "Dropped: held too long, not to be sent"


PENDING_NOTIFICATION_STATUSES = (NotificationStatus.HELD_NO_MAIL_SERVER, NotificationStatus.QUEUED)

# Only these may change after a row is written (core.notifications delivery).
NOTIFICATION_DELIVERY_FIELDS = frozenset({"status", "attempts", "last_error", "sent_at", "next_attempt_at"})


class NotificationImmutableError(AppendOnlyError):
    pass


class NotificationQuerySet(models.QuerySet):
    def update(self, **kwargs):
        changed = set(kwargs) - NOTIFICATION_DELIVERY_FIELDS
        if changed:
            raise NotificationImmutableError(f"Notification content is append-only; cannot update {sorted(changed)}")
        return super().update(**kwargs)

    def delete(self):
        raise NotificationImmutableError("Notifications are never deleted")

    def bulk_create(self, objs, *args, **kwargs):
        if kwargs.get("update_conflicts"):
            raise NotificationImmutableError("Notifications are append-only; no upsert")
        return super().bulk_create(objs, *args, **kwargs)


class Notification(models.Model):
    """
    One alert for one recipient (the outbox). Written by
    core.notifications.notify() when the triggering transaction commits;
    mailed later by deliver_pending() (`manage.py send_notifications`) once
    mail is enabled. Content (who, what, about which object) is append-only;
    only the delivery fields (NOTIFICATION_DELIVERY_FIELDS) change. Never
    deleted: an alert that should not go out any more is DROPPED_STALE.
    """

    append_only_error = NotificationImmutableError

    alert_type = models.CharField(max_length=30, choices=AlertType.choices)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        help_text="NULL only for a test email to a typed address",
    )
    recipient_username = models.CharField(max_length=150, blank=True)
    recipient_email = models.CharField(max_length=254, blank=True, help_text="Address when the alert was created")
    subject = models.CharField(max_length=255)
    body = models.TextField()
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    related = GenericForeignKey("content_type", "object_id")
    dedupe_key = models.CharField(
        max_length=100, blank=True, help_text="Set for alerts that must go once per recipient (e.g. one digest a day)"
    )
    link_path = models.CharField(
        max_length=500, blank=True,
        help_text="The SPA route the alert is about, e.g. /upi/cases/123 (Phase 5 Slice 4); blank on older alerts",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    status = models.CharField(max_length=30, choices=NotificationStatus.choices)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True, help_text="Retry not before (backoff)")
    sent_at = models.DateTimeField(null=True, blank=True)

    objects = NotificationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="notification_pending_idx"),
            models.Index(fields=["recipient", "-created_at", "-id"], name="notification_inbox_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["recipient", "dedupe_key"], condition=~Q(dedupe_key=""), name="one_notification_per_dedupe_key"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.alert_type} -> {self.recipient_username or self.recipient_email} [{self.status}]"

    _CONTENT_FIELDS = ("alert_type", "recipient_id", "recipient_username", "recipient_email", "subject", "body",
                       "content_type_id", "object_id", "dedupe_key", "link_path", "created_at")

    def save(self, *args, **kwargs):
        if not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and set(update_fields) - NOTIFICATION_DELIVERY_FIELDS:
                raise NotificationImmutableError("Notification content is append-only")
            old = Notification.objects.filter(pk=self.pk).values(*self._CONTENT_FIELDS).first()
            if old is None or any(old[f] != getattr(self, f) for f in self._CONTENT_FIELDS):
                raise NotificationImmutableError("Notification content is append-only")
        elif self.pk is not None:
            kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise NotificationImmutableError("Notifications are never deleted")


class NotificationRead(models.Model):
    """
    The in-app "read" mark of one alert (design 4.18, Phase 5 Slice 4). Kept
    apart from Notification, whose content is append-only and whose only
    changing fields are the mail-delivery ones. One alert has one recipient,
    so one row per alert is the per-user read status; `user` repeats the
    recipient so "my unread" needs no join through the outbox. Written only
    by api.alerts (mark read / mark all read); never changed afterwards (the
    first read_at stays).
    """

    notification = models.OneToOneField(Notification, on_delete=models.PROTECT, related_name="read_mark")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    read_at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=["user", "notification"], name="notificationread_user_idx")]

    def __str__(self) -> str:
        return f"{self.notification_id} read by {self.user_id} at {self.read_at:%Y-%m-%d %H:%M}"
