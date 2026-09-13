from celery import chain
from django.contrib import admin, messages
from django.template.response import TemplateResponse

from file_processing.models import (
    FileTypeConfig,
    FileValidationException,
    ProcessingStatus,
    UploadedFile,
)
from file_processing.tasks import load_to_unified_table, normalize_rows


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
        Django-admin stand-in for the interactive business-date picker
        needed by RANGE_CONTAINS file types (e.g. CBS GL Extract): there is
        no upload API/frontend yet (API + Front End is a Phase 5 stub), so
        this is a custom admin action with an intermediate page rather than
        a real product-facing UI. A real UI/API "pick from detected dates"
        flow belongs in Phase 5 -- see tracker.md.
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
            chosen_date = request.POST.get("business_date")
            if chosen_date not in (uploaded_file.detected_dates or []):
                self.message_user(
                    request,
                    f"'{chosen_date}' is not one of the dates detected in this file "
                    f"({uploaded_file.detected_dates}).",
                    level=messages.ERROR,
                )
                return None

            pending_context = uploaded_file.pending_pipeline_context
            uploaded_file.business_date = chosen_date
            uploaded_file.processing_status = ProcessingStatus.VALIDATED
            uploaded_file.save(update_fields=["business_date", "processing_status"])

            chain(normalize_rows.s(pending_context), load_to_unified_table.s()).apply_async()

            self.message_user(
                request,
                f"Business date {chosen_date} applied to '{uploaded_file.filename}'; "
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
