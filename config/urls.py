"""
URL configuration for the Enterprise Payment Reconciliation Platform.

  admin/            Django admin (Phase 1-2 inspection screens; Phase 3
                    exception case queue and maker-checker screens)
  api/exceptions/   thin Exception Management API (session auth; see
                    exception_management.api). Kept unchanged until the end
                    of Phase 5, then removed (Phase 5 design P2).
  api/v1/           Phase 5 API for the SPA (api.urls)
  everything else   the production SPA build, if frontend/dist exists
                    (api.spa); 404 otherwise
"""
from django.contrib import admin
from django.urls import include, path, re_path

from api.spa import spa

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/exceptions/", include("exception_management.urls")),
    path("api/v1/", include("api.urls")),
    # Last: the SPA's own routes (/upi/cases/123 ...) and its build files.
    re_path(r"^(?!api/|admin/|static/)(?P<path>.*)$", spa, name="spa"),
]
