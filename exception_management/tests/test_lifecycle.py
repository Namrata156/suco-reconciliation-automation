"""Case lifecycle (exception_management.services): every transition,
allowed and forbidden, per role; mandatory inputs; self-approval; defaults."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from exception_management import services
from exception_management.actions import ACTION_SPECS, DEFAULT_ACTION_BY_RULE, ActionType
from exception_management.models import CaseEvent, CaseStatus, Decision, EventType, ProposedAction
from exception_management.roles import Role, has_role, user_roles
from exception_management.services import InvalidTransition, MissingInput, RoleRequired
from exception_management.tests.base import D, CaseTestBase
from rule_engine.models import CodeFlag, ResponseCode


class RoleTests(CaseTestBase):
    def test_groups_exist_with_permissions(self):
        from django.contrib.auth.models import Group

        groups = {g.name: g for g in Group.objects.filter(name__in=Role.ALL)}
        self.assertEqual(set(groups), set(Role.ALL))
        perms = {name: set(g.permissions.values_list("codename", flat=True)) for name, g in groups.items()}
        self.assertIn("work_case", perms[Role.MAKER])
        self.assertIn("approve_case", perms[Role.CHECKER])
        self.assertIn("reassign_case", perms[Role.ADMINISTRATOR])
        self.assertNotIn("approve_case", perms[Role.MAKER])
        self.assertNotIn("work_case", perms[Role.CHECKER])
        self.assertTrue(all(p.startswith("view_") for p in perms[Role.AUDITOR]))
        self.assertIn("view_caseevent", perms[Role.AUDITOR])
        for name, codes in perms.items():  # nobody may edit the audit trail
            self.assertFalse({"change_caseevent", "delete_caseevent", "delete_exceptioncase"} & codes, name)

    def test_membership(self):
        both = self.user(Role.MAKER, Role.CHECKER)
        self.assertEqual(user_roles(both), {Role.MAKER, Role.CHECKER})
        self.assertEqual(user_roles(self.user(superuser=True)), set(Role.ALL))
        self.assertFalse(has_role(self.user(Role.MAKER, active=False), Role.MAKER))
        self.assertFalse(has_role(None, Role.MAKER))


class TakeReleaseReassignTests(CaseTestBase):
    def setUp(self):
        super().setUp()
        self.c = self.make_case()

    def test_take(self):
        m = self.maker()
        services.take(self.c, m)
        self.c.refresh_from_db()
        self.assertEqual((self.c.status, self.c.assigned_to), (CaseStatus.IN_PROGRESS, m))
        e = self.c.events.get(event_type=EventType.TAKEN)
        self.assertEqual((e.actor, e.actor_username, e.old_status, e.new_status), (m, m.username, "OPEN", "IN_PROGRESS"))

    def test_take_forbidden_roles(self):
        for roles in ((Role.CHECKER,), (Role.AUDITOR,), (Role.OPERATIONS_USER,), (Role.ADMINISTRATOR,), ()):
            with self.subTest(roles=roles), self.assertRaises(RoleRequired):
                services.take(self.c, self.user(*roles))
        with self.assertRaises(RoleRequired):
            services.take(self.c, self.user(Role.MAKER, active=False))
        services.take(self.c, self.user(superuser=True))  # superuser holds every role

    def test_take_only_open(self):
        services.take(self.c, self.maker())
        with self.assertRaises(InvalidTransition):
            services.take(self.c, self.maker())

    def test_release(self):
        m = self.maker()
        services.take(self.c, m)
        with self.assertRaises(InvalidTransition):  # another maker
            services.release(self.c, self.maker())
        with self.assertRaises(RoleRequired):
            services.release(self.c, self.checker())
        services.release(self.c, m)
        self.c.refresh_from_db()
        self.assertEqual((self.c.status, self.c.assigned_to), (CaseStatus.OPEN, None))
        with self.assertRaises(InvalidTransition):  # not IN_PROGRESS any more
            services.release(self.c, m)

    def test_admin_can_release_someone_elses_case(self):
        services.take(self.c, self.maker())
        services.release(self.c, self.user(Role.ADMINISTRATOR))
        self.assertEqual(self.case(self.c.rrn).status, CaseStatus.OPEN)

    def test_reassign(self):
        admin, m1, m2 = self.user(Role.ADMINISTRATOR), self.maker(), self.maker()
        with self.assertRaises(RoleRequired):
            services.reassign(self.c, m1, m2)  # maker is not admin
        with self.assertRaises(RoleRequired):
            services.reassign(self.c, admin, self.checker())  # target must be a maker
        services.reassign(self.c, admin, m1)  # OPEN -> IN_PROGRESS with m1
        self.c.refresh_from_db()
        self.assertEqual((self.c.status, self.c.assigned_to), (CaseStatus.IN_PROGRESS, m1))
        with self.assertRaises(InvalidTransition):
            services.reassign(self.c, admin, m1)  # already theirs
        services.reassign(self.c, admin, m2)
        e = self.c.events.filter(event_type=EventType.REASSIGNED).last()
        self.assertEqual(e.changes, {"assigned_to": [m1.username, m2.username]})
        services.submit(self.c, m2)
        with self.assertRaises(InvalidTransition):  # with the checker
            services.reassign(self.c, admin, m1)


class SubmitTests(CaseTestBase):
    def taken(self, rule_code="R01", rrn="100000000001"):
        c = self.make_case(rrn, rule_code)
        m = self.maker()
        services.take(c, m)
        return c, m

    def test_submit_default_action(self):
        c, m = self.taken("R01")
        a = services.submit(c, m, remarks="done in portal")
        self.assertEqual((a.action_type, a.response_code.flag, a.response_code.code), (ActionType.DRC_102, "DRC", "102"))
        self.assertEqual((a.rule_code, a.rule_result_id, a.decision), ("R01", c.rule_result_id, Decision.PENDING))
        c.refresh_from_db()
        self.assertEqual(c.status, CaseStatus.PENDING_APPROVAL)
        e = c.events.get(event_type=EventType.SUBMITTED)
        self.assertEqual(e.action_snapshot["action_type"], "DRC_102")
        self.assertEqual(e.action_snapshot["response_code"]["code"], "102")

    def test_submit_forbidden(self):
        c, m = self.taken()
        with self.assertRaises(InvalidTransition):  # not the assignee
            services.submit(c, self.maker())
        with self.assertRaises(RoleRequired):
            services.submit(c, self.checker())
        services.release(c, m)
        with self.assertRaises(InvalidTransition):  # OPEN, not taken
            services.submit(c, m)

    def test_rule_without_default_needs_a_choice(self):
        c, m = self.taken("R13")
        self.assertEqual(c.default_action_type, "")
        with self.assertRaises(MissingInput):
            services.submit(c, m)
        with self.assertRaises(MissingInput):  # close without action needs the finding
            services.submit(c, m, action_type=ActionType.CLOSE_NO_ACTION, remarks="  ")
        a = services.submit(c, m, action_type=ActionType.CLOSE_NO_ACTION, remarks="Found in 27 Sep NPCI file")
        self.assertIsNone(a.response_code)

    def test_ret_default_115_and_other_reason(self):
        c, m = self.taken("R05")
        self.assertEqual((c.default_action_type, c.default_response_code.code), (ActionType.RET, "115"))
        closed = ResponseCode.objects.get(payment_module=self.module, flag=CodeFlag.RET, description="Account closed")
        a = services.submit(c, m, action_type=ActionType.RET, response_code=closed)
        self.assertEqual(a.response_code, closed)

    def test_ret_default_used_when_no_code_given(self):
        c, m = self.taken("R05")
        self.assertEqual(services.submit(c, m).response_code.code, "115")

    def test_wrong_code_for_action(self):
        c, m = self.taken("R05")
        ret = c.default_response_code
        with self.assertRaises(MissingInput):
            services.submit(c, m, action_type=ActionType.DRC_102, response_code=ret)
        with self.assertRaises(MissingInput):
            services.submit(c, m, action_type=ActionType.CBS_ADJUSTMENT_ENTRY, response_code=ret)
        with self.assertRaises(MissingInput):
            services.submit(c, m, action_type="NOT_AN_ACTION")
        self.assertEqual(self.case(c.rrn).status, CaseStatus.IN_PROGRESS)  # nothing half-written
        self.assertFalse(ProposedAction.objects.exists())

    def test_fixed_code_filled_in_for_a_changed_action(self):
        c, m = self.taken("R01")
        a = services.submit(c, m, action_type=ActionType.DRC_103)
        self.assertEqual((a.response_code.flag, a.response_code.code), ("DRC", "103"))

    def test_rrc_501_without_credit_requires_cbs_reversal_evidence(self):
        c, m = self.taken("R06b")
        self.assertEqual(c.default_action_type, ActionType.RRC_501_NO_CREDIT)
        full = dict(evidence_cbs_reversal_date=date(2026, 9, 26), evidence_cbs_reversal_amount="200.00",
                    evidence_cbs_reversal_rrn="264950354228")
        for missing in full:
            with self.subTest(missing=missing), self.assertRaises(MissingInput):
                services.submit(c, m, **{k: v for k, v in full.items() if k != missing})
        with self.assertRaises(MissingInput):
            services.submit(c, m, **{**full, "evidence_cbs_reversal_amount": "0"})
        with self.assertRaises(MissingInput):
            services.submit(c, m, **{**full, "evidence_cbs_reversal_rrn": "   "})
        a = services.submit(c, m, **full)
        self.assertEqual((a.action_type, a.response_code.flag, a.response_code.code), ("RRC_501_NO_CREDIT", "RRC", "501"))
        self.assertEqual(a.evidence_cbs_reversal_amount, Decimal("200.00"))
        snap = c.events.get(event_type=EventType.SUBMITTED).action_snapshot
        self.assertEqual((snap["evidence_cbs_reversal_date"], snap["evidence_cbs_reversal_amount"],
                          snap["evidence_cbs_reversal_rrn"]), ("2026-09-26", "200.00", "264950354228"))

    def test_r06b_maker_may_choose_manual_credit_instead(self):
        c, m = self.taken("R06b")
        a = services.submit(c, m, action_type=ActionType.MANUAL_CREDIT_RRC_501)
        self.assertEqual(a.response_code.code, "501")


class DecisionTests(CaseTestBase):
    def submitted(self, maker=None):
        c = self.make_case()
        m = maker or self.maker()
        services.take(c, m)
        services.submit(c, m)
        return c, m

    def test_approve(self):
        c, m = self.submitted()
        chk = self.checker()
        with self.assertRaises(RoleRequired):
            services.approve(c, m)  # maker only
        a = services.approve(c, chk, remarks="ok")
        self.assertEqual((a.decision, a.decided_by, a.self_approved, a.decision_remarks), (Decision.APPROVED, chk, False, "ok"))
        c.refresh_from_db()
        self.assertEqual(c.status, CaseStatus.APPROVED)
        with self.assertRaises(InvalidTransition):
            services.approve(c, chk)  # not pending any more

    def test_cannot_approve_before_submission(self):
        c = self.make_case()
        with self.assertRaises(InvalidTransition):
            services.approve(c, self.checker())
        with self.assertRaises(InvalidTransition):
            services.reject(c, self.checker(), reason="x")

    def test_self_approval_is_allowed_and_flagged(self):
        both = self.user(Role.MAKER, Role.CHECKER)
        c, _ = self.submitted(maker=both)
        a = services.approve(c, both)
        self.assertTrue(a.self_approved)
        self.assertTrue(c.events.get(event_type=EventType.APPROVED).action_snapshot["self_approved"])

    def test_reject_requires_reason_and_returns_to_maker(self):
        c, m = self.submitted()
        chk = self.checker()
        for reason in ("", "   ", None):
            with self.subTest(reason=reason), self.assertRaises(MissingInput):
                services.reject(c, chk, reason=reason)
        with self.assertRaises(RoleRequired):
            services.reject(c, m, reason="no")
        a = services.reject(c, chk, reason="Wrong code: use 103")
        self.assertEqual((a.decision, a.decision_remarks), (Decision.REJECTED, "Wrong code: use 103"))
        c.refresh_from_db()
        self.assertEqual((c.status, c.assigned_to), (CaseStatus.IN_PROGRESS, m))
        self.assertEqual(c.events.get(event_type=EventType.REJECTED).remarks, "Wrong code: use 103")
        a2 = services.submit(c, m, action_type=ActionType.DRC_103)  # resubmission: a second action row
        self.assertEqual(c.actions.count(), 2)
        self.assertEqual(c.pending_action, a2)

    def test_mark_completed_requires_reference(self):
        c, m = self.submitted()
        with self.assertRaises(InvalidTransition):  # not approved yet
            services.mark_completed(c, m, external_reference="NPCI-1")
        services.approve(c, self.checker())
        for ref in ("", "  "):
            with self.subTest(ref=ref), self.assertRaises(MissingInput):
                services.mark_completed(c, m, external_reference=ref)
        with self.assertRaises(InvalidTransition):  # only the maker holding it
            services.mark_completed(c, self.maker(), external_reference="NPCI-1")
        with self.assertRaises(RoleRequired):
            services.mark_completed(c, self.checker(), external_reference="NPCI-1")
        a = services.mark_completed(c, m, external_reference=" NPCI-1 ")
        self.assertEqual((a.external_reference, a.completed_by), ("NPCI-1", m))
        c.refresh_from_db()
        self.assertEqual(c.status, CaseStatus.COMPLETED)
        self.assertEqual(c.approved_action, a)
        with self.assertRaises(InvalidTransition):
            services.mark_completed(c, m, external_reference="again")

    def test_full_event_trail(self):
        c, m = self.submitted()
        services.add_remark(c, m, "called the customer")
        services.approve(c, self.checker())
        services.mark_completed(c, m, external_reference="REF")
        self.assertEqual(
            list(c.events.order_by("id").values_list("event_type", "old_status", "new_status")),
            [("CREATED", "", "OPEN"), ("TAKEN", "OPEN", "IN_PROGRESS"), ("SUBMITTED", "IN_PROGRESS", "PENDING_APPROVAL"),
             ("REMARK", "PENDING_APPROVAL", "PENDING_APPROVAL"), ("APPROVED", "PENDING_APPROVAL", "APPROVED"),
             ("COMPLETED", "APPROVED", "COMPLETED")],
        )


class RemarkTests(CaseTestBase):
    def test_add_remark(self):
        c = self.make_case()
        m = self.maker()
        e = services.add_remark(c, m, " checked CBS ")
        self.assertEqual((e.event_type, e.remarks, e.actor), (EventType.REMARK, "checked CBS", m))
        with self.assertRaises(MissingInput):
            services.add_remark(c, m, "  ")
        with self.assertRaises(RoleRequired):
            services.add_remark(c, self.user(Role.AUDITOR), "x")
        self.assertEqual(CaseEvent.objects.filter(event_type=EventType.REMARK).count(), 1)


class DefaultActionTests(CaseTestBase):
    EXPECTED = {
        "R01": ("DRC_102", "DRC", "102"),
        "R02": ("DRC_103", "DRC", "103"),
        "R03": ("DRC_104", "DRC", "104"),
        "R04a": ("TCC_102", "TCC", "102"),
        "R04b": ("TCC_102", "TCC", "102"),
        "R05": ("RET", "RET", "115"),
        "R06": ("MANUAL_CREDIT_RRC_501", "RRC", "501"),
        "R06b": ("RRC_501_NO_CREDIT", "RRC", "501"),
        "R12": ("CBS_ADJUSTMENT_ENTRY", None, None),
        "R07b": ("", None, None),
        "R07x": ("", None, None),
        "R07c": ("", None, None),
        "R13": ("", None, None),
        "R99": ("", None, None),
    }

    def test_default_action_per_rule(self):
        for i, code in enumerate(self.EXPECTED):
            self.result(f"3000000000{i:02d}", code)
        self.sync()
        for i, (code, (action, flag, num)) in enumerate(self.EXPECTED.items()):
            c = self.case(f"3000000000{i:02d}")
            rc = c.default_response_code
            with self.subTest(rule=code):
                self.assertEqual((c.default_action_type, rc and rc.flag, rc and rc.code), (action, flag, num))

    def test_table_agrees_with_the_active_rule_codes(self):
        """A pre-filled action with a fixed code must be the rule's own code."""
        for rule_code, action in DEFAULT_ACTION_BY_RULE.items():
            spec = ACTION_SPECS[action]
            rule = self.rules[rule_code]
            with self.subTest(rule=rule_code):
                if spec.code_flag is None:
                    self.assertIsNone(rule.response_code)
                else:
                    self.assertEqual(rule.response_code.flag, spec.code_flag)
                    if spec.fixed_code:
                        self.assertEqual(rule.response_code.code, spec.fixed_code)

    def test_every_action_type_has_a_spec(self):
        self.assertEqual(set(ACTION_SPECS), set(ActionType.values))
