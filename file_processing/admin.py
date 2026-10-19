from django.contrib import admin, messages
from django.template.response import TemplateResponse

from core.operations import OperationError
from file_processing.intake import select_business_date as select_business_date_service
from file_processing.models import (
    FileTypeConfig,
    FileValidationException,
    ProcessingStatus,
    UploadedFile,
)


@admin.register(FileTypeConfig)
class FileTypeConfigAdmin(admin.ModelAdmin):
    list_display = ("payment_module", "file_type_code", "version", "is_active", "payload_format")
    list_filter = ("payment_module", "is_active", "payload_format", "container_format")
    search_fields = ("file_type_code",)


@admin.register(UploadedFile)
class UploadedFileAdmin(admin.ModelAdmin):
    list_display = (
        "filename",
        "payment_module",
        "file_type_config",
        "business_date",
        "settlement_cycle",
        "processing_status",
        "is_current",
        "uploaded_at",
    )
    list_filter = ("payment_module", "processing_status", "is_current")
    search_fields = ("filename", "sha256_hash")
    # Visibility for debugging RANGE_CONTAINS files' detected dates -- not
    # manual editing; business_date is only ever set via the
    # "Select business date" admin action (or typed in at creation for
    # EXACT_MATCH file types, unaffected by this).
    readonly_fields = ("detected_dates",)
    actions = ["select_business_date"]

    @admin.action(description="Select business date for detected date range")
    def select_business_date(self, request, queryset):
        """
        Django-admin fallback for the business-date picker needed by
        RANGE_CONTAINS file types (e.g. CBS GL Extract). Since Phase 5
        Slice 2 the logic lives in file_processing.intake.
        select_business_date, shared with the API/SPA screen (design 4.10).
        """
        if queryset.count() != 1:
            self.message_user(
                request,
                "Select exactly one file to choose its business date.",
                level=messages.ERROR,
            )
            return None

        uploaded_file = queryset.get()
        if uploaded_file.processing_status != ProcessingStatus.AWAITING_DATE_SELECTION:
            self.message_user(
                request,
                f"'{uploaded_file.filename}' is not awaiting date selection "
                f"(current status: {uploaded_file.processing_status}).",
                level=messages.ERROR,
            )
            return None

        if request.POST.get("apply"):
            # Same service as the API (POST /api/v1/<module>/files/<id>/select-date/):
            # Operations User only (P10b), date must be one of detected_dates,
            # audited in core.OperationLog, processing resumed after commit.
            try:
                select_business_date_service(request.user, uploaded_file, request.POST.get("business_date"), source="admin")
            except OperationError as exc:
                self.message_user(request, exc.detail, level=messages.ERROR)
                return None
            self.message_user(
                request,
                f"Business date {request.POST.get('business_date')} applied to '{uploaded_file.filename}'; "
                "normalization and load re-queued.",
                level=messages.SUCCESS,
            )
            return None

        context = {
            **self.admin_site.each_context(request),
            "uploaded_file": uploaded_file,
            "detected_dates": uploaded_file.detected_dates or [],
            "opts": self.model._meta,
            "title": "Select business date",
        }
        return TemplateResponse(
            request,
            "admin/file_processing/uploadedfile/select_business_date.html",
            context,
        )


@admin.register(FileValidationException)
class FileValidationExceptionAdmin(admin.ModelAdmin):
    list_display = ("uploaded_file", "rule_code", "severity", "resolved", "detected_at")
    list_filter = ("severity", "resolved")
