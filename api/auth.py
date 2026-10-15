"""
Login, logout, CSRF bootstrap and "me" (Phase 5 design 2.2, 4.1, 4.2).

Login calls Django's own authenticate() / login(), and logout calls
logout(), so core.signals keeps writing core.UserActivity (login, logout,
failed login) exactly as for the admin. CSRF is checked on login and logout
too: DRF checks it only for an already-logged-in session, so these two views
check it themselves (no login CSRF).
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.signals import user_login_failed
from django.middleware.csrf import get_token
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from api.base import SessionAuthentication401, V1APIView, active_modules, capabilities, module_slug
from exception_management.roles import Role, user_roles

# The SPA warns this long before the idle timeout (design 4.2 / 7.2).
SESSION_WARNING_SECONDS = 120


def enforce_csrf(request) -> None:
    """Raises DRF PermissionDenied (403) when the request fails Django's CSRF
    check, as DRF does for a logged-in session."""
    SessionAuthentication401().enforce_csrf(request._request)


def me_payload(user) -> dict:
    roles = user_roles(user)
    return {
        "id": user.pk,
        "username": user.get_username(),
        "full_name": user.get_full_name() or user.get_username(),
        "roles": [r for r in Role.ALL if r in roles],
        "permissions": capabilities(user),
        "modules": [{"code": m.code, "slug": module_slug(m), "name": m.name} for m in active_modules()],
        # Users and roles stay in the Django admin (P3); the SPA links to it.
        "admin_url": "/admin/" if Role.ADMINISTRATOR in roles and user.is_staff else None,
        "session": {
            "idle_timeout_seconds": settings.SESSION_COOKIE_AGE,
            "warning_seconds": SESSION_WARNING_SECONDS,
            "expires_at_browser_close": settings.SESSION_EXPIRE_AT_BROWSER_CLOSE,
        },
    }


class CsrfView(V1APIView):
    """GET: sets the csrftoken cookie (and returns the token) so the SPA can
    send X-CSRFToken on its first write (the login)."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"csrf_token": get_token(request._request)})


class LoginView(V1APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        enforce_csrf(request)
        username = request.data.get("username")
        password = request.data.get("password")
        errors = {}
        if not isinstance(username, str) or not username.strip():
            errors["username"] = ["This field is required."]
        if not isinstance(password, str) or not password:
            errors["password"] = ["This field is required."]
        if errors:
            return Response({"detail": "Invalid input", "errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        # authenticate() sends user_login_failed itself on bad credentials.
        user = authenticate(request._request, username=username.strip(), password=password)
        if user is None:
            return Response({"detail": "Invalid credentials"}, status=status.HTTP_400_BAD_REQUEST)
        if not user_roles(user):
            # Correct password but no role here: no session, logged as a failed login.
            user_login_failed.send(sender=__name__, credentials={"username": user.get_username()},
                                   request=request._request)
            return Response({"detail": "This account has no role in the reconciliation system. "
                                       "Contact the Administrator."}, status=status.HTTP_403_FORBIDDEN)
        login(request._request, user)  # rotates the session key and the CSRF token
        return Response(me_payload(user))


class LogoutView(V1APIView):
    """Always 204. An already-expired session has nothing to log out (and
    nothing is logged)."""

    permission_classes = [AllowAny]

    def post(self, request):
        enforce_csrf(request)
        if request.user.is_authenticated:
            logout(request._request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(V1APIView):
    def get(self, request):
        return Response(me_payload(request.user))
