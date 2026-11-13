"""Read-only admin views of the generated NPCI files (generation and download
go through the API, which checks the role and writes the audit log). The
file content is not shown here: it holds account numbers."""
from django.contrib import admin

from npci_files.models import CreditVoucher, CreditVoucherApproval, CreditVoucherCredit, NpciFile, NpciFileRow


class _ReadOnly(admin.ModelAdmin):
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(NpciFile)
class NpciFileAdmin(_ReadOnly):
    list_display = ("file_name", "file_date", "sequence", "row_count", "total_amount", "partial",
                    "generated_by_username", "generated_at")
    list_filter = ("payment_module", "partial")
    date_hierarchy = "file_date"
    exclude = ("content",)


@admin.register(NpciFileRow)
class NpciFileRowAdmin(_ReadOnly):
    list_display = ("npci_file", "line", "rrn", "flag", "code", "reply_source", "shtdat", "amount", "account_masked",
                    "text")
    list_filter = ("flag", "code", "reply_source")
    search_fields = ("rrn",)
    raw_id_fields = ("case", "action")


@admin.register(CreditVoucher)
class CreditVoucherAdmin(_ReadOnly):
    """CBS credit files (owner 2026-11-11). Content and rows hold account numbers: not shown."""

    list_display = ("file_name", "voucher_date", "version", "row_count", "total_amount", "rows_to_check",
                    "generated_by_username", "generated_at")
    list_filter = ("payment_module",)
    date_hierarchy = "voucher_date"
    exclude = ("content",)


@admin.register(CreditVoucherApproval)
class CreditVoucherApprovalAdmin(_ReadOnly):
    list_display = ("voucher", "approved_count", "removed_count", "total_amount", "approved_by_username",
                    "approved_at")
    exclude = ("uploaded_content",)


@admin.register(CreditVoucherCredit)
class CreditVoucherCreditAdmin(_ReadOnly):
    list_display = ("approval", "rrn", "amount", "account_changed")
    search_fields = ("rrn",)
    exclude = ("account",)
    raw_id_fields = ("case", "voucher_row")
