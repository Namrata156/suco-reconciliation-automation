from django.apps import AppConfig


class NpciFilesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "npci_files"
    verbose_name = "NPCI File Generation"

    def ready(self):
        from core.append_only import guard_deletes
        from npci_files.models import (
            CreditVoucher,
            CreditVoucherApproval,
            CreditVoucherCredit,
            CreditVoucherRow,
            NpciFile,
            NpciFileRow,
        )

        for model in (NpciFile, NpciFileRow, CreditVoucher, CreditVoucherRow, CreditVoucherApproval,
                      CreditVoucherCredit):
            guard_deletes(model)
