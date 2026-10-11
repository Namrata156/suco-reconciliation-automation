"""
Append-only tables (audit logs): rows may be inserted, never changed or
deleted through the ORM. Used by exception_management.CaseEvent, the
response-code change log and core.UserActivity.

Guarded paths: Model.save() of an existing row (and of a new instance given
an existing pk -- INSERT is forced), Model.delete(), QuerySet.update() /
delete() (so also bulk_update, related-manager update/delete),
bulk_create(update_conflicts=True) and pre-set pks, and cascades (a
pre_delete receiver, see guard_deletes). Raw SQL bypasses all of this, as
for the rule-set immutability guards.
"""
from __future__ import annotations

from django.db import models
from django.db.models.signals import pre_delete


class AppendOnlyError(Exception):
    """An append-only row was about to be changed or deleted."""


def _error(model):
    return getattr(model, "append_only_error", AppendOnlyError)


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise _error(self.model)(f"{self.model.__name__} rows are append-only; they cannot be updated")

    def delete(self):
        raise _error(self.model)(f"{self.model.__name__} rows are append-only; they cannot be deleted")

    def bulk_create(self, objs, *args, **kwargs):
        objs = list(objs)
        if kwargs.get("update_conflicts"):
            raise _error(self.model)(f"{self.model.__name__} rows are append-only; no upsert")
        if any(o.pk is not None for o in objs):
            raise _error(self.model)(f"{self.model.__name__} rows are append-only; no pre-set pk")
        return super().bulk_create(objs, *args, **kwargs)

    # bulk_update() goes through update() and is blocked there.


class AppendOnlyModel(models.Model):
    append_only_error = AppendOnlyError

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding or kwargs.get("force_update") or kwargs.get("update_fields"):
            raise self.append_only_error(f"{type(self).__name__} rows are append-only; they cannot be updated")
        # INSERT only: a new instance given an existing pk must not turn into an UPDATE.
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise self.append_only_error(f"{type(self).__name__} rows are append-only; they cannot be deleted")


def guard_deletes(model) -> None:
    """Blocks deletes that never call delete() (cascades); also stops Django
    "fast deleting" the model's rows without signals. Call from AppConfig.ready()."""

    def _block(sender, instance, **kwargs):
        raise _error(sender)(f"{sender.__name__} rows are append-only; they cannot be deleted")

    pre_delete.connect(_block, sender=model, weak=False, dispatch_uid=f"append_only_guard:{model._meta.label}")
