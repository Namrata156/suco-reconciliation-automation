"""S1: the seeded mapping covers every description seen on the real files,
unmapped descriptions are reported, and mapping data cannot be edited
outside the workflow."""
from __future__ import annotations

from io import StringIO

from django.core.management import call_command

from core.append_only import AppendOnlyError
from settlement.categories import SECTION_BY_CATEGORY, NtslCategory
from settlement.mapping import Classifier, active_classifier, active_version
from settlement.models import (
    MappingEventType,
    MappingImmutableError,
    MapStatus,
    MatchType,
    NtslCategoryMapEntry,
    NtslCategoryMapVersion,
    NtslMappingEvent,
)
from settlement.seed_data import ntsl_category_map_v1
from settlement.seed_data.ntsl_descriptions_seen import DESCRIPTIONS
from settlement.tests.base import NtslTestBase


class SeedCoverageTests(NtslTestBase):
    def test_every_seen_description_maps_to_its_category_with_exactly_one_entry(self):
        _, classifier = active_classifier(self.module)
        self.assertEqual(len(DESCRIPTIONS), 267)
        wrong = {}
        for description, expected in DESCRIPTIONS.items():
            hits = classifier.matches(description)
            if len(hits) != 1 or hits[0].category != expected:
                wrong[description] = [h.category for h in hits]
        self.assertEqual(wrong, {})

    def test_every_category_is_used_and_has_a_section(self):
        used = {e[2] for e in ntsl_category_map_v1.ENTRIES}
        self.assertEqual(used, set(NtslCategory.values))
        self.assertEqual(set(SECTION_BY_CATEGORY), set(NtslCategory.values))

    def test_txn_type_is_data_on_the_entry(self):
        _, classifier = active_classifier(self.module)
        self.assertEqual(classifier.classify("Remitter QR Code-Default U2 Approved Transaction Amount").txn_type, "U2")
        self.assertEqual(classifier.classify("Beneficiary U3 RB Approved Transaction Amount").category,
                         NtslCategory.TXN_BENEFICIARY_RB)

    def test_unmapped_description_is_none(self):
        _, classifier = active_classifier(self.module)
        self.assertIsNone(classifier.classify("Remitter U9 Approved Something New"))
        self.assertIsNone(classifier.classify("settlement amount"))  # exact match is case-sensitive

    def test_exact_entry_wins_over_regex(self):
        _, classifier = active_classifier(self.module)
        self.assertEqual(classifier.classify("Remitter Intent-SI U2 Approved Fee").category, NtslCategory.MANDATE_FEE)
        self.assertEqual(classifier.classify("Remitter Intent-Default U2 Approved Fee").category, NtslCategory.FEE)

    def test_seed_is_active_v1_and_idempotent(self):
        v = active_version(self.module)
        self.assertEqual((v.version, v.status), (1, MapStatus.ACTIVE))
        self.assertEqual(v.entries.count(), len(ntsl_category_map_v1.ENTRIES))
        self.assertEqual(list(v.events.values_list("event_type", flat=True)), [MappingEventType.SEEDED])
        out = StringIO()
        call_command("seed_ntsl_category_map", stdout=out)
        self.assertIn("nothing seeded", out.getvalue())
        self.assertEqual(NtslCategoryMapVersion.objects.filter(payment_module=self.module).count(), 1)

    def test_classifier_from_plain_dicts(self):
        c = Classifier([{"match_type": MatchType.REGEX, "pattern": r"^A.*$", "category": "FEE", "txn_type": "",
                         "priority": 1}])
        self.assertEqual(c.classify("Abc").category, "FEE")
        self.assertIsNone(c.classify("xAbc"))  # full match


class MappingImmutabilityTests(NtslTestBase):
    def setUp(self):
        super().setUp()
        self.version = active_version(self.module)
        self.entry = self.version.entries.first()

    def test_entry_cannot_be_changed_or_deleted(self):
        self.entry.category = NtslCategory.FEE
        with self.assertRaises(MappingImmutableError):
            self.entry.save()
        with self.assertRaises(MappingImmutableError):
            self.entry.delete()
        with self.assertRaises(MappingImmutableError):
            NtslCategoryMapEntry.objects.filter(pk=self.entry.pk).update(category="FEE")
        with self.assertRaises(MappingImmutableError):
            NtslCategoryMapEntry.objects.filter(pk=self.entry.pk).delete()

    def test_no_entry_can_be_added_to_an_active_version(self):
        with self.assertRaises(MappingImmutableError):
            NtslCategoryMapEntry.objects.create(version=self.version, match_type=MatchType.EXACT, pattern="X",
                                                category=NtslCategory.FEE)
        with self.assertRaises(MappingImmutableError):
            NtslCategoryMapEntry.objects.bulk_create([NtslCategoryMapEntry(
                version=self.version, match_type=MatchType.EXACT, pattern="Y", category=NtslCategory.FEE)])

    def test_version_cannot_be_changed_or_deleted_outside_the_workflow(self):
        self.version.status = MapStatus.RETIRED
        with self.assertRaises(MappingImmutableError):
            self.version.save()
        with self.assertRaises(MappingImmutableError):
            self.version.delete()
        with self.assertRaises(MappingImmutableError):
            NtslCategoryMapVersion.objects.filter(pk=self.version.pk).update(status=MapStatus.RETIRED)
        with self.assertRaises(MappingImmutableError):
            NtslCategoryMapVersion.objects.filter(pk=self.version.pk).delete()

    def test_events_are_append_only(self):
        event = NtslMappingEvent.objects.get(version=self.version)
        self.assertTrue(issubclass(MappingImmutableError, AppendOnlyError))
        with self.assertRaises(MappingImmutableError):
            event.delete()
        with self.assertRaises(MappingImmutableError):
            NtslMappingEvent.objects.filter(pk=event.pk).update(remarks="x")
