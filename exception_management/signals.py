"""Delete guards for this app's append-only tables (core.append_only):
cascades never call Model.delete() / QuerySet.delete(). CaseEvent.case is
PROTECT, so a cascade cannot reach events today; this keeps it that way if an
FK is ever changed. Imported from ExceptionManagementConfig.ready()."""
from __future__ import annotations

from core.append_only import guard_deletes
from exception_management.models import CaseEvent, ResponseCodeChangeEvent

guard_deletes(CaseEvent)
guard_deletes(ResponseCodeChangeEvent)
