"""
URL configuration for the Enterprise Payment Reconciliation Platform.

Phase 1 scope only -- just the Django admin (useful for inspecting
FileTypeConfig / UploadedFile / FileValidationException rows during
development). No API app exists yet (API is a Phase 5 stub).
"""
from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
