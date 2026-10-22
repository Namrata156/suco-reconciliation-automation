"""
/api/v1/notifications/ -- the in-app alerts (Phase 5 Slice 4; design 4.18).
Contract: docs/phase5_slice4_api_contract.txt section A.

The alerts are the core.Notification outbox rows of the logged-in user
(written by core.notifications.notify; mail is held until IT supplies a
server). The outbox stays append-only apart from its delivery fields: the
"read" mark is a separate core.NotificationRead row, one per alert (an alert
has one recipient, so that is the per-user read status).

  GET  /notifications/               my alerts, newest first; ?unread=true|false ?alert_type=csv
  GET  /notifications/unread-count/  for the header bell: one read-only query
  POST /notifications/<id>/read/     mark one read (idempotent)
  POST /notifications/read-all/      mark all read; {"up_to_id": n} limits it

The unread count writes nothing: no session key, no log row. The session is
renewed only as on every request (SESSION_SAVE_EVERY_REQUEST), so the SPA
must poll it only while the user is active (design 7.2).

Links: each alert carries "link", a route of the SPA. New alerts store it
(Notification.link_path, set by exception_management.alerts and
settlement.cases). Alerts written before Slice 4 have Django-admin URLs in
their body; for them the link is worked out from the related case, the alert
type, or the admin URL in the body (old_alert_link), never shown as is.
"""
from __future__ import annotations

import re

from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, F, Max
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response

from api.base import V1APIView
from api.operations import _bad_request
from api.results import _bool_param, _enum_filter
from core.models import AlertType, Notification, NotificationRead, PaymentModule
from exception_management import api as legacy
from exception_management.models import ExceptionCase

LISTED_ALERT_TYPES = [t for t in AlertType.values if t != AlertType.TEST]

_ADMIN_CASE = re.compile(r"/admin/exception_management/exceptioncase/(\d+)/change/")
_ADMIN_QUEUE = re.compile(r"/admin/exception_management/exceptioncase/(\?\S*)?(?:\s|$)")
# Older alerts without a related object, by type: the queue filter they are about.
_QUEUE_BY_TYPE = {
    AlertType.R07X_CASE: "?flag=r07x&status=all",
    AlertType.NEEDS_REVIEW: "?flag=needs_review&status=all",
    AlertType.BULK_SUBMITTED: "?mine=approval",
    AlertType.DIGEST_MAKER: "?mine=assigned",
    AlertType.DIGEST_CHECKER: "?mine=approval",
}


def my_alerts(user):
    return Notification.objects.filter(recipient=user).exclude(alert_type=AlertType.TEST)


def _case_slugs(case_ids) -> dict[int, str]:
    if not case_ids:
        return {}
    return {pk: code.lower() for pk, code in
            ExceptionCase.objects.filter(pk__in=case_ids).values_list("pk", "payment_module__code")}


class _LinkContext:
    """What the links of one page of alerts need, in two queries."""

    def __init__(self, alerts):
        self.case_ct = ContentType.objects.get_for_model(ExceptionCase).pk
        ids = set()
        for n in alerts:
            if n.link_path:
                continue
            if n.content_type_id == self.case_ct and n.object_id:
                ids.add(n.object_id)
            elif (m := _ADMIN_CASE.search(n.body)) and n.alert_type not in (AlertType.R07X_CASE,
                                                                            AlertType.NEEDS_REVIEW):
                ids.add(int(m.group(1)))
        self.slugs = _case_slugs(ids)
        code = PaymentModule.objects.filter(is_active=True).order_by("code").values_list("code", flat=True).first()
        self.default_slug = code.lower() if code else None


def old_alert_link(n: Notification, ctx: _LinkContext) -> str | None:
    """The SPA route of an alert written before link_path existed."""
    if n.content_type_id == ctx.case_ct and n.object_id:
        slug = ctx.slugs.get(n.object_id)
        return f"/{slug}/cases/{n.object_id}" if slug else None
    if ctx.default_slug is None:
        return None
    if n.alert_type in (AlertType.R07X_CASE, AlertType.NEEDS_REVIEW):  # several cases: the filtered queue
        return f"/{ctx.default_slug}/cases{_QUEUE_BY_TYPE[n.alert_type]}"
    m = _ADMIN_QUEUE.search(n.body)
    if m:
        return f"/{ctx.default_slug}/cases{m.group(1) or ''}"
    m = _ADMIN_CASE.search(n.body)
    if m:
        slug = ctx.slugs.get(int(m.group(1)))
        return f"/{slug}/cases/{m.group(1)}" if slug else None
    query = _QUEUE_BY_TYPE.get(n.alert_type)
    return f"/{ctx.default_slug}/cases{query}" if query is not None else None


def alert_row(n: Notification, ctx: _LinkContext) -> dict:
    link = n.link_path or old_alert_link(n, ctx)
    module = link.split("/")[1] if link and link.count("/") >= 2 else None
    related = None
    if n.content_type_id == ctx.case_ct and n.object_id:
        related = {"type": "case", "id": n.object_id}
    read_at = getattr(n, "read_at", None)
    return {
        "id": n.pk, "alert_type": n.alert_type, "alert_type_label": n.get_alert_type_display(),
        "subject": n.subject, "body": n.body, "created_at": n.created_at,
        "read": read_at is not None, "read_at": read_at,
        "module": module, "link": link, "related": related,
    }


def _with_read(qs):
    return qs.annotate(read_at=F("read_mark__read_at"))


def _unread(qs):
    return qs.filter(read_mark__isnull=True)


class NotificationListView(V1APIView):
    def get(self, request):
        p, errors = request.query_params, {}
        qs = my_alerts(request.user)
        unread = _bool_param(p, "unread", errors)
        types = _enum_filter(p, "alert_type", LISTED_ALERT_TYPES, errors)
        if errors:
            return _bad_request("Invalid input", errors)
        unread_count = _unread(qs).count()
        if unread is True:
            qs = _unread(qs)
        elif unread is False:
            qs = qs.filter(read_mark__isnull=False)
        if types:
            qs = qs.filter(alert_type__in=types)
        paginator = legacy.QueuePagination()
        page = paginator.paginate_queryset(_with_read(qs).order_by("-created_at", "-id"), request, view=self)
        ctx = _LinkContext(page)
        response = paginator.get_paginated_response([alert_row(n, ctx) for n in page])
        response.data["unread"] = unread_count
        return response


class UnreadCountView(V1APIView):
    """The header bell. One SELECT; nothing is written."""

    def get(self, request):
        agg = _unread(my_alerts(request.user)).aggregate(unread=Count("id"), latest_id=Max("id"))
        return Response({"unread": agg["unread"], "latest_id": agg["latest_id"]})


def _mark(notification_ids, user) -> int:
    now = timezone.now()
    rows = [NotificationRead(notification_id=pk, user=user, read_at=now) for pk in notification_ids]
    NotificationRead.objects.bulk_create(rows, ignore_conflicts=True)  # already read: keeps the first read_at
    return len(rows)


class NotificationReadView(V1APIView):
    def post(self, request, pk):
        n = get_object_or_404(my_alerts(request.user), pk=pk)
        _mark([n.pk], request.user)
        n = _with_read(my_alerts(request.user)).get(pk=n.pk)
        return Response(alert_row(n, _LinkContext([n])))


class NotificationReadAllView(V1APIView):
    def post(self, request):
        up_to = request.data.get("up_to_id") if hasattr(request.data, "get") else None
        if up_to is not None and (not isinstance(up_to, int) or isinstance(up_to, bool)):
            return _bad_request("Invalid input", {"up_to_id": ["An alert id (whole number)."]})
        qs = _unread(my_alerts(request.user))
        if up_to is not None:
            qs = qs.filter(pk__lte=up_to)
        marked = _mark(list(qs.values_list("pk", flat=True)), request.user)
        return Response({"marked": marked, "unread": _unread(my_alerts(request.user)).count()})
