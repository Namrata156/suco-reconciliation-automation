"""
URL configuration for the Enterprise Payment Reconciliation Platform.

  admin/            Django admin (Phase 1-2 inspection screens; Phase 3
                    exception case queue and maker-checker screens)
  api/exceptions/   thin Exception Management API for the Phase 5 front end
                    (session auth; see exception_management.api)
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/exceptions/", include("exception_management.urls")),
]
