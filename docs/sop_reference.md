# UPI Reconciliation — SOP Reference (Sanitized)

Extracted from the bank's internal SOP (`SOP upi updated 25-26.docx`,
2026-09-12) as an **operational/process reference**, not a requirements
document — `docs/reconciliation_automation.docx` remains the sole source
of truth for requirements per `claude.md`. This file captures how
reconciliation is currently done *manually*, useful as ground truth for
matching logic, exception rules, and cycle timings.

**Deliberately excluded from this file**: portal IP addresses, every
username/password, employee names, personal mobile numbers, and
security-question answers. The original SOP docx contains real, live
credentials and should not be kept once this reference exists.

## What UPI is

UPI (Unified Payments Interface) is a mobile money-transfer system.
Customers transfer funds to a beneficiary using account details, mobile
number, or UPI ID — the bank has no direct interference in the customer's
app-level transaction; funds move and the customer sees success/failure
immediately, but settlement between banks happens via NPCI in cycles.

## Settlement cycles (12 total)

UPI runs 24x7; transactions settle in these cycles:

| Cycle | Timing |
|---|---|
| 1C | 21:00:00 – 00:00:00 |
| 2C | 00:00:00 – 05:00:00 |
| 3C | 05:00:00 – 07:00:00 |
| 4C | 07:00:00 – 09:00:00 |
| 5C | 09:00:00 – 11:00:00 |
| 6C | 11:00:00 – 13:00:00 |
| 7C | 13:00:00 – 15:00:00 |
| 8C | 15:00:00 – 17:00:00 |
| 9C | 17:00:00 – 19:00:00 |
| 10C | 19:00:00 – 21:00:00 |
| 11C | 00:00:00 – 16:00:00 |
| 12th (unnamed in SOP) | 16:00:00 – 00:00:00 |

Note: the last two rows' timings match this codebase's `DC1` (00:00–16:00)
and `DC2` (16:00–00:00) exactly — same cycles, different names. The
original spec's "8 cycles" appears to be a stale number; 12 is the real
count, and `core.SettlementCycle`'s existing model (10 numbered + DC1 +
DC2) already reflects this — no schema change needed. See `tracker.md`.

After each cycle completes, NPCI makes files available; the bank
downloads and reconciles them, then updates dispute files back to NPCI.

## Transaction flow

Customer initiates in the UPI app → debited from the SUCO account →
credited to the bank's GL → moves to the remitter bank's switch → a
success/failure response returns to the remitter. The transaction then
reaches NPCI, which bridges the two banks → NPCI's beneficiary-side flow
debits the beneficiary bank's GL and credits the final beneficiary
account, with its own response message.

## Files obtained per cycle (from NPCI)

- Adjustment Report
- Time Out Report
- DRC (Debit Reversal Confirmation) Report
- Raw Data (both Issuer and Acquirer)
- Merchant Raw Data (both Issuer and Acquirer)
- NTSL Files

Raw Data and Merchant Raw Data are downloaded per-cycle; NTSL and the
others are available both per-cycle and as a consolidated report. All
files except NTSL arrive PGP-encrypted from NPCI and require decryption
before use (this system deliberately never performs that decryption
itself — see `tracker.md` Tech Stack; the bank hands over pre-decrypted
files).

Separately, a CBS GL Extract ("Statement of Office Account") is pulled
from the bank's own core banking system for GL code `1535` ("UPI Sponsor
Bank A/C"), covering a 3-day window (day before/of/after the target
business date) — this matches `FileTypeConfig.DateValidationMode.
RANGE_CONTAINS` already built for CBS.

## Working-sheet reconciliation logic (manual process today)

This is the real logic behind `manual_reconciliation_sheet.xlsx` and the
Transaction Presence Matrix concept in `tracker.md`:

1. **RRN extraction**: RRN (the 12-digit unique UPI transaction
   identifier) is extracted from the CBS remarks/narration text starting
   at the 8th character, 12 digits long (`=MID(remarks, 8, 12)` in the
   manual sheet) — matches this codebase's `_CBS_REMARKS_RE` /
   `_extract_cbs_rrn` implementation.
2. **DRC matching**: DRC records (filtered to the bank's own records,
   remitter = SSK) are checked against CBS by RRN via VLOOKUP
   ("effected" check), and separately against a duplicate-RRN sheet built
   from CBS to detect reversals ("reversed" check). Classification:
   - Effected **and** reversed → transaction is **online reversed**.
   - Effected, **not** reversed → **needs manual reversal to the
     customer**.
   - Neither effected nor reversed → **online response failed**.
   These map to DRC reason codes 102 (online reverse), 103 (manually
   crtd), 104 (not effected to CBS) — already corroborated independently
   via `SSKUPI14072026.csv`, see `doubts.md`.
3. **Timeout matching**: Timeout records (beneficiary = SSK) are checked
   the same way (effected / reversed via VLOOKUP against CBS and the
   reversal sheet):
   - Effected, not reversed → **online credited**.
   - Not effected, not reversed → credit manually, or return the
     transaction to the remitter.
4. **Adjustment records**: only `RET` type and `credit adjustment`,
   remitter = SSK, are actioned — credited to the customer manually,
   reason code 501 ("manually credited to customer post reconciliation").
   Chargebacks (if any) are separately accepted or declined.

## Raw Data / Merchant Raw Data structure

Each covers the whole day's debit and credit transactions across all
cycles, in two variants (Raw Data, Merchant Raw Data), each split into
Issuer and Acquirer files:

- **Issuer transactions are credit to the bank's customer** (P2P and
  P2M).
- **Acquirer transactions are debit to the bank's customer** (P2P and
  P2M).

Only date, RRN, amount, and a transaction code are used from each row.
**Amounts include decimals encoded as whole numbers — divide to get the
real rupee value** (e.g. a raw value of 4000 is actually ₹40) — this
codebase's confirmed paise scaling (`amount_scale_factor = 0.01`) is
consistent with this. After extraction, all four variants (Issuer,
Acquirer, Merchant Issuer, Merchant Acquirer) get checked against CBS by
RRN via VLOOKUP, and only the "effected" (matched) rows carry forward to
the NTSL comparison below.

## NTSL (Net Settlement) structure and reconciliation

NTSL provides, per cycle: number of transactions, transaction amounts,
switching/processing fees, GST on fees, settlement amount, adjustment
amount, penalty, and a final settlement amount. It is a **settlement
summary**, not transaction-level detail — confirmed both by direct file
inspection and this SOP independently (see `doubts.md`).

**The actual reconciliation check**: take all cycles' NTSL figures in one
format, separating debit amount, credit amount, adjustment amount,
switching fee, and GST. The bank's own CBS-effected amount should match
NTSL's final settlement amount. Then:

> **(matched-transaction deposit − withdrawal) − (NTSL credit − debit)
> should net to zero.** A non-zero difference should equal the sum of
> not-yet-credited Timeout amounts plus effected-but-not-reversed DRC
> amounts.

This is the concrete formula to build into Phase 4's NTSL validation once
that component's design starts.

## Dispute update actions (in the NPCI portal, maker → checker)

- **DRC update**: search by date + RRN, open "Debit Reversal Confirmation
  (DRC)", enter RRN, select reason code — 102 (customer account reversed
  online), 103 (customer account reversed manually post-reconciliation),
  or 104 (online decline response failed) — submit, then approve in the
  checker role.
- **Adjustment update**: search by date + RRN, open "Refund Reversal
  Confirmation", enter the bank adjustment reference, reason code 501,
  remarks, submit, then approve.
- **Timeout update**: search by date + RRN; if effected, "TCC —
  Transaction Credit Confirmation" with reason code 102 (already credited
  online) or 103 (credited manually post-reconciliation); if not
  effected, either credit manually or return via "RET — Return" with a
  reason code (account closed, NRI account, party instructions, account
  doesn't exist, invalid beneficiary details, credit freezed, beneficiary
  bank unable to credit, or "any other reason").

## Chargebacks

- **Raising a chargeback (outward)**: search the transaction, choose
  "Chargeback" (non-fraud) or "Fraud Chargeback Rise" (fraud, with
  evidence upload), select a reason code and full/partial amount as
  applicable, submit, then approve.
- **Responding to an inward chargeback**: must respond within TAT or it's
  deemed accepted. Choose "Accept Chargeback" or "Representment
  Chargeback" (denying, with evidence), reason code, remarks, submit,
  then approve.

## Generic Good Faith Adjustments

- **BGGC** (Beneficiary Generic Good Faith Credit): beneficiary bank
  recovers wrongly-credited/fraud funds from the remitting bank after the
  35-day TAT for chargebacks has passed. Funds settle immediately on
  raise; the receiving bank must accept or represent within 3 calendar
  days or it's deemed accepted.
- **RGGC** (Remitter Generic Good Faith Credit): remitting bank sends
  funds to a beneficiary bank that missed representing a chargeback in
  time. Same 3-day accept/represent window.
- **BGGD** (Beneficiary Generic Good Faith Debit): beneficiary/sponsor
  bank raises a debit adjustment on the remitter/issuing bank for IPO
  executions only, within 15 days of the mandate creation date; the
  issuing bank has 3 days to accept/reject or it's deemed accepted on day
  4. Only valid for U2/IPO transactions with initiation modes 11 or 13
  and purpose code 01; declined if the original transaction already
  carries response codes 00, RB, or DD.

## Fraud monitoring (EFRM) — process overview

NPCI's Enterprise Fraud Risk Management (EFRM) platform offers member
banks real-time and near-real-time fraud monitoring using ML/AI model
scores, across two portals (UPI, and a separate one for IMPS/ATM/ECOM/
POS). Banks investigate flagged transactions (contacting the customer)
and classify each as confirmed genuine, undetermined, confirmed fraud, or
assumed genuine. A monthly "nil reporting" is mandatory if no fraud is
found in a given month.

Current fraud-trend guidance from NPCI: **investment scams** are a
growing UPI fraud pattern. Recommended bank-side controls: review
transactions with multiple incoming credits, conduct due diligence
against account holders' profiles vs. their transaction patterns, act on
suspicious accounts with law enforcement, apply extra scrutiny when
creating VPAs/UPI IDs with similar name series or shared
device/mobile/IP patterns, and review NPCI's monthly alerts on domestic
UPI IDs appearing on international websites.
