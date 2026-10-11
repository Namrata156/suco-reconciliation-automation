"""
Seeds UPI NTSL category mapping v1 as the ACTIVE version (see
settlement/seeding.py). Idempotent, create-only. An existing DB that already
has the UPI PaymentModule got v1 from data migration settlement/0002.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from settlement.seeding import seed_ntsl_category_map


class Command(BaseCommand):
    help = "Seeds UPI NTSL category mapping v1 (active). Idempotent."

    def handle(self, *args, **options):
        created, message = seed_ntsl_category_map()
        self.stdout.write(self.style.SUCCESS(message) if created else message)
