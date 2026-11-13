"""
The five roles (Phase 3 design section 4) as Django groups. The groups and
their model permissions are created by data migration
exception_management/0002_roles. The workflow service checks group
MEMBERSHIP (a superuser holds every role); the model permissions are what
the Slice 2 admin screens will use.

A user may hold several roles (owner D3), e.g. Maker and Checker.

Owner decision 2026-11-12 (Plan B item 4): roles can be switched off with
settings.ROLES_ENFORCED (env, default False). Switched off, every active,
signed-in user holds every role (as a superuser always did), so every
role check built on user_roles / has_role opens up together; every step is
still logged by username and self-approval is still recorded. The Django
groups stay in the database: ROLES_ENFORCED=true restores the role limits.
The few places that query groups directly use users_holding_q /
listed_roles below.
"""
from __future__ import annotations

from django.conf import settings
from django.db.models import Q


class Role:
    ADMINISTRATOR = "Administrator"
    OPERATIONS_USER = "Operations User"
    MAKER = "Maker"
    CHECKER = "Checker"
    AUDITOR = "Auditor"

    ALL = (ADMINISTRATOR, OPERATIONS_USER, MAKER, CHECKER, AUDITOR)


def user_roles(user) -> set[str]:
    if user is None or not user.is_authenticated or not user.is_active:
        return set()
    if user.is_superuser or not roles_enforced():
        return set(Role.ALL)
    return set(user.groups.filter(name__in=Role.ALL).values_list("name", flat=True))


def has_role(user, role: str) -> bool:
    return role in user_roles(user)


def roles_enforced() -> bool:
    """settings.ROLES_ENFORCED, read at call time (tests override it)."""
    return bool(getattr(settings, "ROLES_ENFORCED", False))


def users_holding_q(*roles: str) -> Q:
    """Filter for a user queryset: the users holding any of `roles` (all
    roles when none given). Roles switched off: every user (Q())."""
    if not roles_enforced():
        return Q()
    return Q(is_superuser=True) | Q(groups__name__in=roles or Role.ALL)


def listed_roles(user) -> list[str]:
    """The role names to SHOW for a user in a list (users, reports), in
    Role.ALL order; prefetch "groups" on the queryset. Roles switched off:
    [] (the screens hide the role column, me.roles_enforced is false)."""
    if not roles_enforced():
        return []
    names = set(Role.ALL) if user.is_superuser else {g.name for g in user.groups.all()}
    return [r for r in Role.ALL if r in names]
