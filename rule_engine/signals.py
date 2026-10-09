"""Guards Rule.alternative_codes (m2m) changes on ACTIVE/RETIRED rule sets --
m2m add/remove/clear never call Rule.save(), so they need their own check."""
from __future__ import annotations

from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from rule_engine.models import Rule, ResponseCode, _assert_rule_sets_draft


@receiver(m2m_changed, sender=Rule.alternative_codes.through)
def _guard_alternative_codes(sender, instance, action, reverse, model, pk_set, **kwargs):
    if action not in ("pre_add", "pre_remove", "pre_clear"):
        return
    if not reverse:  # rule.alternative_codes.add(...)
        _assert_rule_sets_draft([instance.rule_set_id])
        return
    # code.rules_as_alternative.add(rule, ...)
    assert isinstance(instance, ResponseCode)
    rules = Rule.objects.filter(pk__in=pk_set) if pk_set else instance.rules_as_alternative.all()
    _assert_rule_sets_draft(rules.values_list("rule_set_id", flat=True))
