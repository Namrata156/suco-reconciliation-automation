"""Rule repository admin is read-only (rule edits need maker-checker, Phase 3)."""
from __future__ import annotations

from io import StringIO

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory, TestCase

from rule_engine.models import AgeingBucket, ResponseCode, Rule, RuleSet, RuleTat


class ReadOnlyAdminTests(TestCase):
    def setUp(self):
        call_command("seed_rule_sets", stdout=StringIO())
        self.user = get_user_model().objects.create_superuser("admin", "a@example.invalid", "x")
        self.client.force_login(self.user)

    def test_no_add_change_delete_even_for_superuser(self):
        request = RequestFactory().get("/")
        request.user = self.user
        for model in (RuleSet, Rule, ResponseCode, AgeingBucket):
            model_admin = site._registry[model]
            with self.subTest(model=model.__name__):
                self.assertFalse(model_admin.has_add_permission(request))
                self.assertFalse(model_admin.has_change_permission(request))
                self.assertFalse(model_admin.has_delete_permission(request))
        self.assertNotIn(RuleTat, site._registry)  # shown inline under its rule only

    def test_changelists_and_detail_pages_render(self):
        rule_set = RuleSet.objects.get()
        rule = Rule.objects.get(code="R07b")
        for url in (
            "/admin/rule_engine/ruleset/",
            f"/admin/rule_engine/ruleset/{rule_set.pk}/change/",
            "/admin/rule_engine/rule/",
            f"/admin/rule_engine/rule/{rule.pk}/change/",
            "/admin/rule_engine/responsecode/",
            "/admin/rule_engine/ageingbucket/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get("/admin/rule_engine/rule/add/").status_code, 403)
