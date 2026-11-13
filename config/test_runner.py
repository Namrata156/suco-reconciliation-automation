"""
The test runner: runs every test with settings.ROLES_ENFORCED = True, so the
existing role tests (written when roles always applied) keep testing the
role limits. Tests of the switched-off case (owner 2026-11-12) use
@override_settings(ROLES_ENFORCED=False).
"""
from django.test.runner import DiscoverRunner
from django.test.utils import override_settings


class RolesEnforcedTestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._roles_enforced = override_settings(ROLES_ENFORCED=True)
        self._roles_enforced.enable()

    def teardown_test_environment(self, **kwargs):
        self._roles_enforced.disable()
        super().teardown_test_environment(**kwargs)
