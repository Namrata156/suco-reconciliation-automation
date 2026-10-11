"""
NTSL line categories (Phase 4 design S1, approved 2026-10-11) and where each
category sits in NTSL's own arithmetic (S2 self-check).

Which DESCRIPTION belongs to which category is data (NtslCategoryMapEntry,
versioned, changed only through maker-checker). This module only fixes the
category list and the section each category belongs to; the sections were
worked out from the 36 real cycles of 14 Jul / 25 Sep / 26 Sep 2026:

  DETAIL      every line above "Beneficiary / Remitter Sub Totals".
              Sub Totals debit  = sum of the DETAIL debits,
              Sub Totals credit = sum of the DETAIL credits (per column, not
              netted). Fees, GST, PSP fees, the switching-fee rebate and the
              non-financial mandate fees are all INSIDE Sub Totals.
  SUBTOTAL    "Beneficiary / Remitter Sub Totals".
  SETTLEMENT  "Settlement Amount" = Sub Totals credit - debit, shown on one
              side (debit when SSK pays).
  POST        lines between Settlement Amount and Final (Net Adjusted ...,
              pre-arbitration, penalties, deemed acceptances, NRP fees,
              customer compensation).
  FINAL       "Final Settlement Amount" = Settlement net + POST nets.

Two placements could not be proven because the lines were 0.00 in every
real cycle: SETTLEMENT_CHARGES (assumed DETAIL, it sits above Sub Totals)
and NRP_FEE / NRP_FEE_GST / COMPENSATION (assumed POST, they sit above
Final). If a real file ever disagrees, the self-check fails for that cycle
rather than passing silently.
"""
from __future__ import annotations

from django.db import models


class NtslCategory(models.TextChoices):
    # -- transaction amounts (compared with the system side in Slice 2) -----
    TXN_REMITTER = "TXN_REMITTER", "Remitter transaction amount (SSK pays)"
    TXN_REMITTER_RB = "TXN_REMITTER_RB", "Remitter transaction amount, RB (deemed approved)"
    TXN_BENEFICIARY = "TXN_BENEFICIARY", "Beneficiary transaction amount (SSK receives)"
    TXN_BENEFICIARY_RB = "TXN_BENEFICIARY_RB", "Beneficiary transaction amount, RB (deemed approved)"
    # -- fees and taxes inside Sub Totals --------------------------------------
    FEE = "FEE", "Interchange fee"
    FEE_GST = "FEE_GST", "GST on interchange fee"
    SWITCHING_FEE = "SWITCHING_FEE", "NPCI switching fee"
    SWITCHING_FEE_GST = "SWITCHING_FEE_GST", "GST on NPCI switching fee"
    PSP_FEE = "PSP_FEE", "Payer PSP fee paid"
    PSP_FEE_GST = "PSP_FEE_GST", "GST on payer PSP fee"
    REBATE = "REBATE", "Switching fee rebate"
    REBATE_GST = "REBATE_GST", "GST on switching fee rebate"
    MANDATE_FEE = "MANDATE_FEE", "Non-financial mandate fee"
    MANDATE_FEE_GST = "MANDATE_FEE_GST", "GST on non-financial mandate fee"
    SETTLEMENT_CHARGES = "SETTLEMENT_CHARGES", "Settlement charges"
    # -- NTSL's own totals ------------------------------------------------------
    SUBTOTAL = "SUBTOTAL", "Beneficiary / Remitter Sub Totals"
    SETTLEMENT_AMOUNT = "SETTLEMENT_AMOUNT", "Settlement Amount"
    # -- between Settlement Amount and Final -----------------------------------
    ADJUSTMENT = "ADJUSTMENT", "Net adjusted amount"
    ADJUSTMENT_FEE = "ADJUSTMENT_FEE", "Fee / switching fee on adjustments"
    PRE_ARBITRATION = "PRE_ARBITRATION", "Pre-arbitration deemed acceptance"
    PENALTY = "PENALTY", "Pre-arbitration penalty"
    DEEMED = "DEEMED", "Deemed good-faith / deferred acceptance"
    NRP_FEE = "NRP_FEE", "NRP raise fee"
    NRP_FEE_GST = "NRP_FEE_GST", "GST on NRP raise fee"
    COMPENSATION = "COMPENSATION", "Customer compensation for non-compliance"
    FINAL = "FINAL", "Final Settlement Amount"


class Section:
    DETAIL = "DETAIL"
    SUBTOTAL = "SUBTOTAL"
    SETTLEMENT = "SETTLEMENT"
    POST = "POST"
    FINAL = "FINAL"


C = NtslCategory
SECTION_BY_CATEGORY: dict[str, str] = {
    **{
        c: Section.DETAIL
        for c in (
            C.TXN_REMITTER, C.TXN_REMITTER_RB, C.TXN_BENEFICIARY, C.TXN_BENEFICIARY_RB,
            C.FEE, C.FEE_GST, C.SWITCHING_FEE, C.SWITCHING_FEE_GST, C.PSP_FEE, C.PSP_FEE_GST,
            C.REBATE, C.REBATE_GST, C.MANDATE_FEE, C.MANDATE_FEE_GST, C.SETTLEMENT_CHARGES,
        )
    },
    C.SUBTOTAL: Section.SUBTOTAL,
    C.SETTLEMENT_AMOUNT: Section.SETTLEMENT,
    **{
        c: Section.POST
        for c in (
            C.ADJUSTMENT, C.ADJUSTMENT_FEE, C.PRE_ARBITRATION, C.PENALTY, C.DEEMED,
            C.NRP_FEE, C.NRP_FEE_GST, C.COMPENSATION,
        )
    },
    C.FINAL: Section.FINAL,
}
assert set(SECTION_BY_CATEGORY) == set(NtslCategory.values), "every category needs a section"

TXN_CATEGORIES = (C.TXN_REMITTER, C.TXN_REMITTER_RB, C.TXN_BENEFICIARY, C.TXN_BENEFICIARY_RB)
