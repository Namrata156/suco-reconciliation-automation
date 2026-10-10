"""CaseEvent is append-only: every ORM path that would change or remove an
event is blocked; cases with events cannot be deleted (PROTECT)."""
from __future__ import annotations

from django.db import transaction
from django.db.models import ProtectedError

from exception_management import services
from exception_management.models import CaseEvent, CaseImmutableError, EventType
from exception_management.tests.base import CaseTestBase


class CaseEventImmutabilityTests(CaseTestBase):
    def setUp(self):
        super().setUp()
        self.c = self.make_case()
        services.add_remark(self.c, self.maker(), "original")
        self.e = CaseEvent.objects.get(event_type=EventType.REMARK)

    def assertBlocked(self, fn):
        with self.assertRaises(CaseImmutableError), transaction.atomic():
            fn()
        self.e.refresh_from_db()
        self.assertEqual(self.e.remarks, "original")
        self.assertEqual(CaseEvent.objects.count(), 2)  # CREATED + REMARK

    def test_instance_save(self):
        self.e.remarks = "edited"
        self.assertBlocked(self.e.save)

    def test_instance_save_update_fields(self):
        self.e.remarks = "edited"
        self.assertBlocked(lambda: self.e.save(update_fields=["remarks"]))

    def test_new_instance_with_existing_pk_does_not_overwrite(self):
        clone = CaseEvent(pk=self.e.pk, case=self.c, payment_module_code="UPI", business_date=self.c.business_date,
                          rrn=self.c.rrn, event_type=EventType.REMARK, remarks="edited")
        with self.assertRaises(Exception), transaction.atomic():  # IntegrityError: INSERT only
            clone.save()
        self.e.refresh_from_db()
        self.assertEqual(self.e.remarks, "original")

    def test_instance_delete(self):
        self.assertBlocked(self.e.delete)

    def test_queryset_update(self):
        self.assertBlocked(lambda: CaseEvent.objects.filter(pk=self.e.pk).update(remarks="edited"))

    def test_related_manager_update_and_delete(self):
        self.assertBlocked(lambda: self.c.events.update(remarks="edited"))
        self.assertBlocked(lambda: self.c.events.all().delete())

    def test_queryset_delete(self):
        self.assertBlocked(lambda: CaseEvent.objects.all().delete())

    def test_bulk_update(self):
        self.e.remarks = "edited"
        self.assertBlocked(lambda: CaseEvent.objects.bulk_update([self.e], ["remarks"]))

    def test_bulk_create_upsert(self):
        self.e.remarks = "edited"
        self.assertBlocked(lambda: CaseEvent.objects.bulk_create(
            [self.e], update_conflicts=True, unique_fields=["id"], update_fields=["remarks"]
        ))

    def test_update_or_create(self):
        self.assertBlocked(lambda: CaseEvent.objects.update_or_create(pk=self.e.pk, defaults={"remarks": "edited"}))

    def test_cascade_signal_guard(self):
        from django.db.models.signals import pre_delete

        with self.assertRaises(CaseImmutableError):
            pre_delete.send(sender=CaseEvent, instance=self.e, using="default", origin=self.e)

    def test_case_with_events_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.c.delete()

    def test_rule_result_and_match_group_protected_by_case(self):
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.c.rule_result.delete()
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.c.match_group.delete()

    def test_appending_still_works(self):
        services.add_remark(self.c, self.maker(), "second")
        self.assertEqual(CaseEvent.objects.count(), 3)
