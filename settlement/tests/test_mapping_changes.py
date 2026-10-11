"""S1 maker-checker: changing the active NTSL mapping."""
from __future__ import annotations

from exception_management.roles import Role
from exception_management.services import InvalidTransition, MissingInput, RoleRequired
from settlement.categories import NtslCategory
from settlement.mapping import active_classifier, active_version
from settlement.mapping_changes import approve_mapping_change, propose_mapping_change, reject_mapping_change
from settlement.models import CheckStatus, MappingEventType, MapStatus, MatchType, NtslCategoryMapVersion
from settlement.selfcheck import check_ntsl_date
from settlement.tests.base import AUTH_LINES, D, NtslTestBase

NEW_LINE = "Net Chargeback Penalty Including GST"
ADD = [{"match_type": MatchType.EXACT, "pattern": NEW_LINE, "category": NtslCategory.PENALTY,
        "notes": "first seen on a test file"}]


class MappingChangeTests(NtslTestBase):
    def setUp(self):
        super().setUp()
        self.v1 = active_version(self.module)

    def propose(self, user=None, **kw):
        kw.setdefault("add", ADD)
        kw.setdefault("reason", "NPCI added a penalty line (circular X)")
        return propose_mapping_change(self.module, user or self.maker(), **kw)

    def test_propose_then_approve_activates_new_version(self):
        self.load_cycle("1C", AUTH_LINES + [(NEW_LINE, None, "0", "0")])
        self.assertEqual(check_ntsl_date(self.module, D).checks[0].status, CheckStatus.FAIL)  # unmapped

        maker, checker = self.maker(), self.checker()
        v2 = self.propose(maker)
        self.assertEqual((v2.version, v2.status, v2.based_on_id), (2, MapStatus.PENDING, self.v1.pk))
        self.assertEqual(active_version(self.module), self.v1)  # nothing changes until approval
        self.assertEqual(len(v2.change_summary["added"]), 1)
        self.assertEqual(v2.change_summary["removed"], [])
        self.assertEqual(v2.change_summary["recategorised_loaded_descriptions"],
                         [{"description": NEW_LINE, "old": None, "new": "PENALTY"}])
        self.assertEqual(v2.entries.count(), self.v1.entries.count() + 1)

        approve_mapping_change(v2, checker, remarks="ok")
        v2.refresh_from_db()
        self.v1.refresh_from_db()
        self.assertEqual((v2.status, self.v1.status), (MapStatus.ACTIVE, MapStatus.RETIRED))
        self.assertFalse(v2.self_approved)
        self.assertEqual(v2.decided_by, checker)
        self.assertEqual(list(v2.events.values_list("event_type", flat=True)),
                         [MappingEventType.PROPOSED, MappingEventType.APPROVED])
        self.assertEqual(list(self.v1.events.values_list("event_type", flat=True)),
                         [MappingEventType.SEEDED, MappingEventType.RETIRED])
        _, classifier = active_classifier(self.module)
        self.assertEqual(classifier.classify(NEW_LINE).category, NtslCategory.PENALTY)
        check = check_ntsl_date(self.module, D).checks[0]
        self.assertEqual((check.status, check.map_version_id), (CheckStatus.PASS, v2.pk))

    def test_same_person_may_approve_but_it_is_flagged(self):
        both = self.user(Role.MAKER, Role.CHECKER)
        v2 = approve_mapping_change(self.propose(both), both)
        self.assertTrue(v2.self_approved)
        self.assertTrue(v2.events.get(event_type=MappingEventType.APPROVED).self_approved)

    def test_roles_are_enforced(self):
        with self.assertRaises(RoleRequired):
            self.propose(self.checker())
        v2 = self.propose()
        with self.assertRaises(RoleRequired):
            approve_mapping_change(v2, self.maker())
        with self.assertRaises(RoleRequired):
            reject_mapping_change(v2, self.maker(), reason="no")

    def test_reject_needs_a_reason_and_frees_the_slot(self):
        v2 = self.propose()
        with self.assertRaises(InvalidTransition):
            self.propose()  # one pending change at a time
        with self.assertRaises(MissingInput):
            reject_mapping_change(v2, self.checker(), reason=" ")
        reject_mapping_change(v2, self.checker(), reason="wrong category")
        v2.refresh_from_db()
        self.assertEqual(v2.status, MapStatus.REJECTED)
        self.assertEqual(active_version(self.module), self.v1)
        with self.assertRaises(InvalidTransition):
            approve_mapping_change(v2, self.checker())
        self.assertEqual(self.propose().version, 3)

    def test_invalid_proposals_are_refused(self):
        bad = [
            {"add": [], "remove_entry_ids": []},
            {"add": [{**ADD[0], "category": "NOT_A_CATEGORY"}]},
            {"add": [{"match_type": MatchType.REGEX, "pattern": "([unclosed", "category": "FEE"}]},
            {"add": [{**ADD[0], "txn_type": "U9"}]},
            {"add": ADD + ADD},
            {"reason": ""},
        ]
        for kw in bad:
            with self.subTest(kw=kw), self.assertRaises(MissingInput):
                self.propose(**kw)
        self.assertFalse(NtslCategoryMapVersion.objects.filter(status=MapStatus.PENDING).exists())

    def test_proposal_that_unmaps_a_loaded_description_is_refused(self):
        self.load_cycle("1C", AUTH_LINES)
        settle = self.v1.entries.get(pattern="Settlement Amount")
        with self.assertRaisesMessage(MissingInput, "would be unmapped"):
            self.propose(add=[], remove_entry_ids=[settle.pk])

    def test_proposal_that_makes_a_loaded_description_ambiguous_is_refused(self):
        self.load_cycle("1C", AUTH_LINES)
        greedy = {"match_type": MatchType.REGEX, "pattern": r"^Remitter .*Transaction Amount$",
                  "category": NtslCategory.TXN_REMITTER, "priority": 5}
        with self.assertRaisesMessage(MissingInput, "more than one regex entry"):
            self.propose(add=[greedy])

    def test_approval_refused_when_active_version_changed_meanwhile(self):
        v2 = self.propose()
        self.v1.status = MapStatus.RETIRED
        self.v1._workflow_change = True
        self.v1.save()
        with self.assertRaises(InvalidTransition):
            approve_mapping_change(v2, self.checker())
