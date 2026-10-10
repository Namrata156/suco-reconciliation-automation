"""
The five roles (Phase 3 design section 4) as Django groups. The groups and
their model permissions are created by data migration
exception_management/0002_roles. The workflow service checks group
MEMBERSHIP (a superuser holds every role); the model permissions are what
the Slice 2 admin screens will use.

A user may hold several roles (owner D3), e.g. Maker and Checker.
"""
from __future__ import annotations


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
    if user.is_superuser:
        return set(Role.ALL)
    return set(user.groups.filter(name__in=Role.ALL).values_list("name", flat=True))


def has_role(user, role: str) -> bool:
    return role in user_roles(user)
