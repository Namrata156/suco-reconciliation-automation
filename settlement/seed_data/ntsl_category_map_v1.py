"""
NTSL category mapping, version 1 -- plain data (no imports from live
models), read by both the seed command and data migration
settlement/0002. Built 2026-10-11 from the 267 distinct descriptions on the
36 loaded NTSL files (14 Jul, 25 Sep, 26 Sep 2026; 1C-10C, DC1, DC2).
The full list, with the category each one must get, is
seed_data/ntsl_descriptions_seen.py (used by the coverage test).

Matching: an EXACT entry wins; otherwise the REGEX entries are tried in
`priority` order (full match). The v1 regexes are written so that no seen
description matches more than one entry. A description that matches
nothing is UNMAPPED: the self-check fails for that cycle and lists it.

Transaction-type families (U2 / U3) are separate entries so the type is data
too. On the real files U2 lines are the Merchant Raw Data transactions and U3
the Raw Data ones (checked 2026-10-11, all 3 dates).

Each entry: (match_type, pattern, category, txn_type, priority, notes)
"""
from __future__ import annotations

PAYMENT_MODULE_CODE = "UPI"
VERSION = 1
REASON = (
    "Initial mapping, Phase 4 Slice 1 (design approved by the project owner "
    "2026-10-11). Seeded from the 267 descriptions on the 36 NTSL files loaded "
    "for 14 Jul, 25 Sep and 26 Sep 2026."
)

EXACT = "EXACT"
REGEX = "REGEX"

_PARTY = r"(?:Remitter|Beneficiary) (?:.+ )?"  # "Remitter U3 ..." and "Remitter QR Code-Default U3 ..."


ENTRIES: list[tuple] = [
    # -- NTSL's own totals and fixed lines -------------------------------------
    (EXACT, "Beneficiary / Remitter Sub Totals", "SUBTOTAL", "", 0, ""),
    (EXACT, "Settlement Amount", "SETTLEMENT_AMOUNT", "", 0, ""),
    (EXACT, "Final Settlement Amount", "FINAL", "", 0, "The loader also flags this line (is_final_settlement)"),
    (EXACT, "Settlement Charges", "SETTLEMENT_CHARGES", "", 0, "0.00 on every loaded cycle"),
    (EXACT, "Switching Fee Rebate for U2 transactions", "REBATE", "U2", 0, "Credit to SSK"),
    (EXACT, "GST on Switching Fee Rebate for U2 transactions", "REBATE_GST", "U2", 0, "Credit to SSK"),
    # -- between Settlement Amount and Final -----------------------------------
    (EXACT, "Net Adjusted Amount", "ADJUSTMENT", "", 0,
     "Non-zero only in DC1/DC2 on the loaded dates; ties to the Adjustment file (Q6 finding)"),
    (EXACT, "Net Adjusted Fee with Tax", "ADJUSTMENT_FEE", "", 0, "0.00 on every loaded cycle"),
    (EXACT, "Net Debit Adjustment Switching Fee with Tax", "ADJUSTMENT_FEE", "", 0, "0.00 on every loaded cycle"),
    (EXACT, "Pre-Arbitration Deemed Acceptance", "PRE_ARBITRATION", "", 0, "0.00 on every loaded cycle"),
    (EXACT, "Pre-Arbitration Deemed Acceptance Penalty", "PENALTY", "", 0, "0.00 on every loaded cycle"),
    (EXACT, "Net Penalty debited on Pre-Arbitration Acceptance Including GST", "PENALTY", "", 0,
     "0.00 on every loaded cycle"),
    (EXACT, "Net Penalty credited on Pre-Arbitration Acceptance Including GST", "PENALTY", "", 0,
     "0.00 on every loaded cycle"),
    (EXACT, "Net Deemed Good Faith Debit Adjustment Acceptance", "DEEMED", "", 0, "0.00 on every loaded cycle"),
    (EXACT, "Net Deemed Good Faith Chargeback Acceptance", "DEEMED", "", 0, "0.00 on every loaded cycle"),
    (EXACT, "Net Deemed Good Faith Representment Acceptance", "DEEMED", "", 0, "0.00 on every loaded cycle"),
    (EXACT, "Net Deemed Good Faith Debit Chargeback Acceptance", "DEEMED", "", 0, "0.00 on every loaded cycle"),
    (EXACT, "Net Deemed Deferred Chargeback Acceptance", "DEEMED", "", 0, "0.00 on every loaded cycle"),
    (EXACT, "NRP Raise Fees", "NRP_FEE", "", 0, "0.00 on every loaded cycle"),
    (EXACT, "NRP Raise Fees GST", "NRP_FEE_GST", "", 0, "0.00 on every loaded cycle"),
    (EXACT, "Customer Compensation For Non Compliance Debit", "COMPENSATION", "", 0, "0.00 on every loaded cycle"),
    (EXACT, "Customer Compensation For Non Compliance Credit", "COMPENSATION", "", 0, "0.00 on every loaded cycle"),
    # -- "FOR NON - FINANCIAL MANDATE/OD TXNs" section (credit to SSK). These
    #    descriptions occur only in that section on all 36 files; the regex
    #    FEE family below would otherwise catch them.
    (EXACT, "Remitter Default-SI U2 Approved Fee", "MANDATE_FEE", "U2", 0, "Non-financial mandate section"),
    (EXACT, "Remitter Default-SI U2 Approved Fee Gst", "MANDATE_FEE_GST", "U2", 0, "Non-financial mandate section"),
    (EXACT, "Remitter Intent-SI U2 Approved Fee", "MANDATE_FEE", "U2", 0, "Non-financial mandate section"),
    (EXACT, "Remitter Intent-SI U2 Approved Fee Gst", "MANDATE_FEE_GST", "U2", 0, "Non-financial mandate section"),
    (EXACT, "Remitter QR Man-SI U2 Approved Fee", "MANDATE_FEE", "U2", 0, "Non-financial mandate section"),
    (EXACT, "Remitter QR Man-SI U2 Approved Fee Gst", "MANDATE_FEE_GST", "U2", 0, "Non-financial mandate section"),
    (EXACT, "Remitter SDK-SI U2 Approved Fee", "MANDATE_FEE", "U2", 0, "Non-financial mandate section"),
    (EXACT, "Remitter SDK-SI U2 Approved Fee Gst", "MANDATE_FEE_GST", "U2", 0, "Non-financial mandate section"),
    # -- transaction amounts ---------------------------------------------------
    (REGEX, r"^Remitter (?:.+ )?U2 Approved Transaction Amount$", "TXN_REMITTER", "U2", 10, ""),
    (REGEX, r"^Remitter (?:.+ )?U3 Approved Transaction Amount$", "TXN_REMITTER", "U3", 10, ""),
    (REGEX, r"^Remitter (?:.+ )?U2 RB Approved Transaction Amount$", "TXN_REMITTER_RB", "U2", 10,
     "RB = deemed approved (Timeout file)"),
    (REGEX, r"^Remitter (?:.+ )?U3 RB Approved Transaction Amount$", "TXN_REMITTER_RB", "U3", 10,
     "RB = deemed approved (Timeout file)"),
    (REGEX, r"^Beneficiary (?:.+ )?U2 Approved Transaction Amount$", "TXN_BENEFICIARY", "U2", 10, ""),
    (REGEX, r"^Beneficiary (?:.+ )?U3 Approved Transaction Amount$", "TXN_BENEFICIARY", "U3", 10, ""),
    (REGEX, r"^Beneficiary (?:.+ )?U2 RB Approved Transaction Amount$", "TXN_BENEFICIARY_RB", "U2", 10,
     "RB = deemed approved (Timeout file)"),
    (REGEX, r"^Beneficiary (?:.+ )?U3 RB Approved Transaction Amount$", "TXN_BENEFICIARY_RB", "U3", 10,
     "RB = deemed approved (Timeout file)"),
    # -- fees (RB variants included: fees are only checked inside NTSL) -------
    (REGEX, rf"^{_PARTY}U2(?: RB)? Approved Fee$", "FEE", "U2", 20, ""),
    (REGEX, rf"^{_PARTY}U3(?: RB)? Approved Fee$", "FEE", "U3", 20, ""),
    (REGEX, rf"^{_PARTY}U2(?: RB)? Approved Fee Gst$", "FEE_GST", "U2", 20, ""),
    (REGEX, rf"^{_PARTY}U3(?: RB)? Approved Fee Gst$", "FEE_GST", "U3", 20, ""),
    (REGEX, rf"^{_PARTY}U2(?: RB)? Approved NPCI Switching Fee$", "SWITCHING_FEE", "U2", 20, ""),
    (REGEX, rf"^{_PARTY}U3(?: RB)? Approved NPCI Switching Fee$", "SWITCHING_FEE", "U3", 20, ""),
    (REGEX, rf"^{_PARTY}U2(?: RB)? Approved NPCI Switching Fee Gst$", "SWITCHING_FEE_GST", "U2", 20, ""),
    (REGEX, rf"^{_PARTY}U3(?: RB)? Approved NPCI Switching Fee Gst$", "SWITCHING_FEE_GST", "U3", 20, ""),
    (REGEX, r"^(?:.+ )?U2(?: RB)? Approved Payer PSP Fee -Paid$", "PSP_FEE", "U2", 20, "No party prefix"),
    (REGEX, r"^(?:.+ )?U3(?: RB)? Approved Payer PSP Fee -Paid$", "PSP_FEE", "U3", 20, "No party prefix"),
    (REGEX, r"^(?:.+ )?U2(?: RB)? Approved Payer PSP Fee Gst -Paid$", "PSP_FEE_GST", "U2", 20, "No party prefix"),
    (REGEX, r"^(?:.+ )?U3(?: RB)? Approved Payer PSP Fee Gst -Paid$", "PSP_FEE_GST", "U3", 20, "No party prefix"),
]
