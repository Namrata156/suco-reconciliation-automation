"""Writes core.UserActivity from Django's auth signals (login, logout,
failed login). Registered in CoreConfig.ready()."""
from __future__ import annotations

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from core.models import UserActivity, UserActivityType


def _request_info(request) -> dict:
    if request is None:
        return {}
    meta = getattr(request, "META", {}) or {}
    return dict(
        ip_address=meta.get("REMOTE_ADDR") or None,
        user_agent=(meta.get("HTTP_USER_AGENT") or "")[:255],
        path=(getattr(request, "path", "") or "")[:255],
    )


@receiver(user_logged_in, dispatch_uid="core.user_activity.login")
def _logged_in(sender, request, user, **kwargs):
    UserActivity.objects.create(
        activity_type=UserActivityType.LOGIN, user=user, username=user.get_username(), **_request_info(request)
    )


@receiver(user_logged_out, dispatch_uid="core.user_activity.logout")
def _logged_out(sender, request, user, **kwargs):
    UserActivity.objects.create(
        activity_type=UserActivityType.LOGOUT,
        user=user if user is not None and user.pk else None,
        username=user.get_username() if user is not None else "",
        **_request_info(request),
    )


@receiver(user_login_failed, dispatch_uid="core.user_activity.login_failed")
def _login_failed(sender, credentials, request=None, **kwargs):
    from django.contrib.auth import get_user_model

    model = get_user_model()
    username = str((credentials or {}).get(model.USERNAME_FIELD, "") or (credentials or {}).get("username", ""))[:150]
    user = model._default_manager.filter(**{model.USERNAME_FIELD: username}).first() if username else None
    UserActivity.objects.create(
        activity_type=UserActivityType.LOGIN_FAILED, user=user, username=username, **_request_info(request)
    )
