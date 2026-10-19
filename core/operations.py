"""
Shared pieces of the operations services (Phase 5 Slice 2): the
operations log writer and the error types the upload / date-selection /
run services raise. The API maps the errors to status codes (403 / 409 /
400), the Django admin to a message.
"""
from __future__ import annotations

from datetime import date

from core.models import OperationLog, PaymentModule


class OperationError(Exception):
    """Base: refused, nothing changed. `errors` = {field: [messages]}."""

    def __init__(self, detail: str, errors: dict | None = None):
        super().__init__(detail)
        self.detail = detail
        self.errors = errors or {}


class RoleRequired(OperationError):
    pass


class WrongState(OperationError):
    """The item is in the wrong status for this step (409)."""


class InvalidInput(OperationError):
    pass


def log_operation(operation: str, *, user, payment_module: PaymentModule, business_date: date | None = None,
                  uploaded_file_id: int | None = None, run_id: int | None = None, source: str = "",
                  details: dict | None = None) -> OperationLog:
    return OperationLog.objects.create(
        operation=operation,
        user=user if user is not None and user.is_authenticated else None,
        username=user.get_username() if user is not None and user.is_authenticated else "",
        payment_module=payment_module,
        business_date=business_date,
        uploaded_file_id=uploaded_file_id,
        run_id=run_id,
        source=source,
        details=details or {},
    )
