"""
Common pieces of /api/v1/ (Phase 5 design 3.1, 3.4, 3.5).

  - SessionAuthentication401: Django session login, CSRF enforced on every
    write by DRF, and "not logged in" answered with 401 (DRF answers 403 for
    a session API by default, so the SPA could not tell "session expired"
    from "not your role").
  - ModuleMixin: resolves <module> (PaymentModule.code, lower case) from the
    URL; unknown or inactive -> 404. Resolved AFTER authentication and the
    role check, so an anonymous caller gets 401 and learns nothing about
    which modules exist.
  - CAPABILITIES: the role matrix of design 3.5 as data, returned by /me so
    the SPA can build its menus. A convenience only: the server checks every
    write in the service functions.
"""
from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework.authentication import SessionAuthentication

from core.models import PaymentModule
from exception_management.api import HasAnyRole
from exception_management.roles import Role, user_roles
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView


class SessionAuthentication401(SessionAuthentication):
    def authenticate_header(self, request):
        # Any value makes DRF answer NotAuthenticated with 401 instead of 403.
        return 'Session realm="api"'


class V1APIView(APIView):
    """Logged in + one of the five roles (read access, design 3.5)."""

    authentication_classes = [SessionAuthentication401]
    permission_classes = [IsAuthenticated, HasAnyRole]


def module_slug(module: PaymentModule) -> str:
    return module.code.lower()


def active_modules():
    return PaymentModule.objects.filter(is_active=True).order_by("code")


class ModuleMixin:
    """For views under /api/v1/<module>/. Sets self.module."""

    module = None

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)  # authentication + permissions first (401 / 403)
        self.module = get_object_or_404(active_modules(), code__iexact=kwargs.get("module", ""))

    def scope(self, qs):
        return qs.filter(payment_module=self.module)


# ---------------------------------------------------------------------------
# Capabilities for the SPA's menus and buttons (design 3.5; P10b: upload, date
# pick and run are Operations User only). Case buttons come per case from
# available_transitions, not from here.
# ---------------------------------------------------------------------------
_ALL = Role.ALL
CAPABILITIES: dict[str, tuple[str, ...]] = {
    "cases.view": _ALL,
    "cases.work": (Role.MAKER,),  # take, release, submit, mark done, remark, bulk take / submit
    "cases.approve": (Role.CHECKER,),  # approve, reject, clear review, bulk approve
    "cases.reassign": (Role.ADMINISTRATOR,),
    "files.view": _ALL,
    "files.upload": (Role.OPERATIONS_USER,),  # upload, CBS date selection, run (Slice 2)
    "results.view": _ALL,
    "settings.view": _ALL,
    "settings.propose_codes": (Role.MAKER,),  # response codes and NTSL mapping (Slice 3)
    "settings.propose_mapping": (Role.MAKER,),
    "settings.propose_tolerance": (Role.ADMINISTRATOR,),
    "settings.approve": (Role.CHECKER,),
    "alerts.view": _ALL,
    "audit.view": (Role.ADMINISTRATOR, Role.AUDITOR),
    "users.admin": (Role.ADMINISTRATOR,),  # link to the Django admin (P3)
}


def capabilities(user) -> dict[str, bool]:
    roles = user_roles(user)
    return {name: bool(roles.intersection(allowed)) for name, allowed in CAPABILITIES.items()}
