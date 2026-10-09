# Doubts & Confirmations Needed

Running list of open questions that need a decision or confirmation from
the project owner before file-type parsing rules and the
`TransactionRecord` schema get finalized. This is working-level detail
feeding into `TRACKER.md`'s Open Questions table, not a replacement for
it — once something here is resolved, it either gets removed (if it was
just a scoping detail) or promoted to `TRACKER.md` if it's a durable
tracked gap.

## Open

### File intake UX — explicit type selection + auto-detection feasibility
Project owner wants the upload flow to let the user explicitly select
which file type they're uploading (e.g. "CBS", "DRC", "Timeout", ...)
before/during upload — this matches today's underlying model, where
`UploadedFile.file_type_config` is already a required FK, just not yet
exposed through any real UI (only Django admin exists today; a real
upload UI/API is Phase 5 scope).
Also raised: worth investigating whether the software can **auto-identify
a file's type from its content** instead of, or alongside, requiring the
user to pick it — analogous to how `parsers/sniff.py` already detects
real payload *format* (SpreadsheetML XML / OOXML zip / CSV) from file
bytes rather than trusting the extension. Auto-detection of file *type*
(not just format) would need its own content-based heuristics per type —
e.g. CBS's worksheet-name + remarks-column shape vs. DRC/Timeout's
headered layout vs. Raw Data/Merchant Raw Data's headerless HT/TX/FT
layout — not yet designed, not yet confirmed as reliable enough to trust
over explicit user selection.
**Needs a decision** (not urgent — Phase 5 scope, but worth designing
alongside the intake API rather than bolting on later): build explicit
type-selection only, or invest in auto-detection too (most plausibly as
an assist/cross-check against the user's selection, not necessarily a
replacement for it). Related to, but distinct from, the already-tracked
"single-file upload vs. bulk folder ingestion" open question.

### NTSL — not an RRN-level register; loading done, validation still Phase 4
**Update 2026-09-27:** the project owner asked for NTSL files to be
uploaded now and chose "staging + summary table" over staging only. Built
and loaded: raw `StagingNtsl` (every row of each file) plus
`NtslSettlementLine` (one row per category line: description, txn count,
debit, credit, cycle, business date, with the "Final Settlement Amount"
line flagged), plus 2 history tables (schema only). All 12 real files for
2026-07-14 (1C–10C, DC1, DC2) loaded: 6,000 staged rows, 1,559 lines,
exactly 1 final-settlement line per file (e.g. 1C debit 507,341.89, DC1
credit 435.00). The file header's date and cycle are cross-checked
against the upload, with no mismatches. Each file has 2 blank-description
rows with all-zero values, which aren't loaded (one INFO exception per
file). Two handling choices the backend engineer made without explicit
instruction, which the owner may confirm or override:
- A category line with a non-blank, non-numeric debit/credit is skipped
  with a WARNING rather than loaded as 0.
- A non-numeric txn count is stored as NULL with a WARNING.
Neither case occurs in the real files. **Still Phase 4 and not built:**
the actual NTSL validation / settlement reconciliation check described
below.

Direct inspection of a real sample (`14072026/NTSL/UPI_NTSLSSK140726_1C.xls`,
500-row worksheet) confirms NTSL is a **settlement summary/totals report**,
not a transaction-level register like CBS/DRC/Timeout/Adjustment/Raw
Data/Merchant Raw Data. Its only columns are `Description | No of Txns |
Debit | Credit` — ~175 category rows (e.g. "Remitter Intent-Default U2
Approved NPCI Switching Fee") ending in a "Final Settlement Amount" total
row. **No RRN column exists anywhere in the file** (confirmed: searched
every shared string in the workbook, no match). This isn't a "confirm
the column mapping" problem — NTSL structurally cannot plug into
`TransactionRecord` (which is RRN-indexed, one row per transaction) the
way the other six file types do.
**Decision (project owner, 2026-09-10): defer.** Do not block loading the
other six RRN-bearing file types into `TransactionRecord` on this. NTSL
needs its own design pass later (Settlement Control / NTSL Validation,
Phase 4) — likely a separate settlement-summary table (category, count,
debit, credit, cycle, business date) rather than a `TransactionRecord`
row, with NTSL validation working by comparing matched-transaction totals
per category against this summary, not by RRN-matching. Not yet designed
— parked, see `TRACKER.md` Open Questions.
**Corroborated independently 2026-09-12** by the bank's own SOP doc. The
sanitized, credential-free knowledge from that doc now lives permanently
at `docs/sop_reference.md` — `SOP upi updated 25-26.docx` itself
(gitignored — real live NPCI/EFRM portal credentials) is expected to be
deleted from disk once the reference doc is confirmed sufficient; this
codebase doesn't depend on the original docx existing. Original source:
"NTSL means net settlement files in this file NPCI provides the
settlement details of each cycles... number of transactions, amount of
transactions, switching fee/processing fee and GST on fees, settlement
amount... adjustment amount, penalty and final settlement amount" —
matches the direct file inspection exactly, independent source. The SOP
also states the actual reconciliation check to build for Phase 4: **the
difference between (deposit − withdrawal) from the matched transactions
and (NTSL credit − NTSL debit) should net to zero**; a non-zero
difference should equal the sum of not-yet-credited Timeout amounts plus
effected-but-not-reversed DRC amounts. Concrete, ready-to-formalize input
for whenever NTSL's design starts.

### Sheets `14` and `Sheet12` in `manual_reconciliation_sheet.xlsx`
Both hold CBS-shaped rows (`Date, Remarks, rrn, Withdrawals, Deposits,
REF`) with a `REF` column that's populated (matching the deposit/
withdrawal amount) when a match is found and `#N/A` otherwise — same
VLOOKUP-driven pattern as `eff`/`rev` in `drc`/`tmt`. Project owner
believes these are reference/working sheets built with formulas, but
isn't fully certain of their exact purpose (e.g. what `Sheet12`'s ~2k
row subset specifically represents). Not blocking — noted for later if
it becomes relevant to Transaction Processing Engine design.

### Dispute files (DRC, Timeout, Adjustment) vs. dispute cycles DC1/DC2
Per the NPCI circular, disputes now settle only in DC1/DC2, and DRC,
Timeout and Adjustment are the bank's dispute files (one daily file each
today). Phase 4's NTSL validation will probably need to check DC1/DC2
totals against these dispute files rather than against CBS AUTH matches.
It's also unclear whether these files ever arrive per cycle (the SOP says
NPCI offers "per-cycle and consolidated"). **Parked for Phase 4** at the
project owner's request (2026-09-27).

### NPCI direction labels (Acquirer/Issuer) may be reversed
Phase 1 labels NPCI files per the SOP note in `docs/sop_reference.md`:
Acquirer → DEBIT, Issuer → CREDIT. On 2026-09-30 the project owner
explained it the other way round: the remitter/issuer side debits the
customer, and the beneficiary/acquirer side credits them. Real data can't
settle this alone. On all 3 loaded dates, every CBS↔NPCI pair (~61K a day)
carries **opposite** direction labels, which fits either reading
depending on whether CBS (GL 1535) is read from the bank's side or the
customer's.
Reconciliation doesn't depend on the answer: `direction_match` only
checks that the two sides are opposite (`reconciliation/presence.py`).
But the NPCI `direction` shown in reports would be backwards if the
owner's reading is right. **Needs confirmation from ops**, not blocking
Phase 2. Phase 1 was deliberately not changed (owner, 2026-09-30:
corrections belong in reconciliation).

## Resolved

- **~10% of CBS rows "fail RRN validation"** — resolved 2026-09-30.
  These are CBS dropping leading zeros: every one of them is all digits
  and 8–11 long (e.g. remarks `By UPI/4383934640/...`). Zero-padded to 12,
  they match NPCI for 6,803 of 6,805 rows (14 Jul) and 7,201 of 7,253
  (26 Sep). No fallback key is needed. **2026-10-02, owner's decision:**
  the padding happens at extraction (`file_processing.tasks._extract_cbs_rrn`)
  instead of at match time, so `TransactionFact.rrn` holds the real 12-digit
  RRN. The 21,835 rows loaded earlier were corrected by the one-off
  `fix_cbs_rrn_leading_zeros` command (6,803 / 7,779 / 7,253 on
  14 Jul / 25 Sep / 26 Sep), and the raw remarks are unchanged in staging.
  The only CBS rows left without an RRN are 4 non-UPI GL entries (2 on
  14 Jul, 2 on 25 Sep: "UPI TO RRC DRC TCC REVERSAL DATED ON ..." and
  "NULLIFICATION OF UPI TRXN AMT TRF TO GL7/1526"). Matching results are
  unchanged.
- **On-us payments vs CBS reversals** — resolved 2026-10-04 from real
  data. When payer and payee both bank with SSK, NPCI's Raw Data holds an
  issuer (CREDIT) + acquirer (DEBIT) row for the same RRN and amount, and
  CBS legitimately holds a credit + debit pair (810 / 838 / 775 a day). A
  CBS pair is a reversal only when NPCI has no pair, or when its pair
  carries failure codes (R9 / U9 / RR — failed on-us payments that CBS
  debited and reversed; 10 on 14 Jul, all in the DRC file). Checked
  against ops' manual sheet for 14 Jul: DRC 265/265 and Timeout 20/20 RRNs
  agree on effected/reversed.
- **Dispute files: which side is SSK** — confirmed from the file headers
  (2026-10-03): DRC/Timeout col 9 `Remitter`, col 10 `Beneficiary`;
  Adjustment col 5 `Remitter`, col 4 `Adjtype`, col 8 `Txndate`. Per the
  SOP, SSK's cases are DRC Remitter = SSK, Timeout Beneficiary = SSK,
  Adjustment Remitter = SSK. Ops' manual sheet has exactly those rows
  (265 DRC, 20 Timeout on 14 Jul). The other bank's rows are kept and
  labelled `ssk_side = False`.
- **Merchant Raw Data ISS 2C file absent on 25 and 26 Sep** — owner's
  reading (2026-10-02): probably not generated by NPCI. 2C (00:00–05:00)
  had only 1 Merchant ISS transaction on 14 Jul, when the file did exist,
  against 8–72 in other cycles.
- **NPCI transactions absent from CBS** — explained 2026-09-30. Raw Data
  raw cell `"5"` is the NPCI response code. `00` (success) is on 99.8% of
  the RRNs found in CBS, and the RRNs missing from CBS carry decline codes
  (`XB`, `Z9`, `01`, `ZM`, ...). So these are declined transactions where
  no money moved. The code is now recorded on
  `RegisterPresence.response_code` for the Business Rule Engine. The
  meaning of each code is still the open error/response-code table gap.

- **`cycle_type` stored values renamed FINANCIAL/NON_FINANCIAL →
  AUTH/DISPUTE.** Project owner: keep the rename (2026-09-27). *(2026-09-27)*
- **Merchant Raw Data BBP cycles.** The config only accepted 4C/5C/6C,
  because that's all the 2026-07-14 sample contained. Project owner
  confirmed BBP can arrive in any of 1C–10C (2026-09-27). `cycle_set`
  became `1C`–`10C` and the filename pattern `_(?:[1-9]|10)C\.csv$`, in the
  seed and in the live DB row. Verified: all 10 cycle filenames match only
  BBP, and no real file matches more than one config. The 4 existing BBP
  fact rows are untouched, and the related tests pass (39/39).
  *(2026-09-27)*

- **Settlement cycle times in the DB didn't match the SOP or NTSL.**
  The dev DB's 1C–10C rows held placeholder 2-hour slots (1C
  00:00–02:00 … 10C 18:00–20:00). The project owner provided the NPCI
  circular "Annexure I – Revised Settlement Cycles" as the authoritative
  source and approved the correction (2026-09-27). The circular says:
  - 1C–10C are AUTH-only settlement cycles (no disputes).
  - DC1 (00:00–16:00, RTGS 17:00) and DC2 (16:00–00:00, RTGS 04:00) are
    dispute-only cycles. The NTSL file names carry "DC1"/"DC2".
  - Only NTSL comes in all 12 cycles; the other NPCI files have 10.
  - All other settlement rules are unchanged under OSG V_2.1.

  backend-engineer built:
  - `core/migrations/0002` + `0003`: corrected times, a new
    `rtgs_settlement_time` field, and `cycle_type` renamed to AUTH/DISPUTE
    (see the Open item above)
  - a `seed_settlement_cycles` command, because the rows had been created
    by hand and a fresh DB had no cycles

  91/91 tests pass. The circular's timings agree with
  `docs/sop_reference.md` and the NTSL file headers.

- **Adjustment `txn_amount` / `txn_date_raw` were NULL in real runs.** The
  ADJUSTMENT `column_mapping` had no keys for `Txnamount` (col 16) or
  `Txndate` (col 8). The project owner OK'd adding them (2026-09-27):
  `"txn_amount": 16, "txn_date": 8` added to the seed config and the live
  DB row. `amount` stays `Adjamount` (col 17) and `business_date` stays
  `Adjdate`, both unchanged. Verified against the real 2026-07-14 file
  with the live config (dry run, rolled back): 257/257 rows get
  `txn_amount_raw`/`txn_date_raw` in staging and `txn_amount` in the fact
  table, and 180 differ from `amount`, matching the 2026-09-17 finding.
  *(2026-09-27)*
- **Re-ingesting sample files after the redesign reset.** The 47 leftover
  `UploadedFile` rows (all `LOADED`, no data behind them, no reprocess
  links) would have blocked re-uploading via the sha256 guard. The project
  owner chose to delete them (2026-09-27): 47 `UploadedFile` rows + 1
  attached `FileValidationException` deleted. The source files on disk
  were untouched. The re-ingestion follow-up is unblocked. *(2026-09-27)*

- **Staging → fact → history redesign supersedes the unified-table
  storage decision (below, 2026-09-08).** Approved by the project owner
  2026-09-23, built 2026-09-25/26. The core reasoning of the 2026-09-08
  decision **still stands**: matching needs one indexed RRN lookup, so
  there is still exactly one unified fact table (`TransactionFact`, which
  replaces `TransactionRecord`). What changed is the storage around it:
  - Per-file-type raw **staging** tables now come before the fact table
    (7 of them), holding each file as it arrived (`raw_cells` + typed raw
    columns). This fills the staging layer the original pipeline plan
    called for but never built.
  - `extra_fields` was dropped from the fact table, since staging now
    holds the raw payload. Each fact row carries a plain `staging_row_id`
    (no DB FK) back to its staging row.
  - Adjustment gets `txn_amount` (`Txnamount`) alongside `amount`
    (`Adjamount`, unchanged). See the Open item above on why it's NULL
    in real runs for now.
  - 10 **history** tables were added (schema only). The archival job waits
    for Phase 3. This is the "retention/archival requirement emerges"
    trigger in the partitioning entry below.
  - Only transaction-storage tables were reset (`migrate ... zero` on
    `transactions`/`reconciliation`), and config tables and their data
    were kept. Existing transaction rows were dropped, not migrated.
  The 2026-09-08 entry is kept below as the historical record. The
  duplicate-prevention guards it describes carry over unchanged
  (`unique_row_per_uploaded_file` on the fact table, the same idea per
  staging table, and the file-level `sha256_hash` guard). See section 6 of
  `docs/phase1_report.txt` for the plain-language table guide (it replaced
  `docs/db.md` on 2026-09-27). *(2026-09-26)*

- **Adjustment — which amount column is "the" amount.** The original
  note here ("both were equal in every sample row inspected") was
  **factually wrong** — verified 2026-09-17 by scanning all 257 real
  rows (not just 1–2): `Txnamount` and `Adjamount` actually differ in
  **180 of 257 rows (70%)**. Whoever wrote the original note only
  looked at a row or two that happened to match (the "Credit
  Adjustment" type, which is always equal, but is a small minority —
  8 of 257 rows).
  **Resolved by the project owner's plain explanation of the two
  fields**: `Txnamount` is the *original* disputed transaction's
  amount; `Adjamount` is the amount of *this specific adjustment*
  being processed now. That makes the real-data pattern make complete
  sense:
  - "Debit Reversal Confirmation" (175 of 257 rows, the largest
    category): `Txnamount` is **always `0.00`** — using it would have
    silently zeroed out the amount for 68% of the file. `Adjamount`
    carries the real value.
  - "Refund Reversal Confirmation": genuinely differ by real amounts
    (e.g. one row: `Txnamount=896.75` vs `Adjamount=847.75`; another:
    `Txnamount=3301.00` vs `Adjamount=1901.90`) — consistent with
    partial refunds/adjustments where fees are deducted or only part
    of the original amount is settled.
  **Conclusion**: `TransactionRecord.amount` should represent the value
  of *this row* (the adjustment being processed), not a historical
  reference to the original transaction — so `Adjamount` (column 17,
  already what the code uses) is unambiguously correct. `Txnamount`
  (column 16) is legitimately contextual, already preserved in
  `extra_fields.raw_cells` rather than dropped (same treatment as
  `Txndate`). No code change needed — the existing `column_mapping`
  was already right, just previously unconfirmed for the wrong reason.
  *(2026-09-17)*
- **Direction (`DEBIT`/`CREDIT`) for DRC, Timeout, Adjustment.** Confirmed
  by the project owner (2026-09-16), directly quoting the bank's SOP:
  - **DRC → constant `DEBIT`**: "DRC dispute file contains the debit
    response failed transaction[s;] we need to update the status of
    these files by comparing it with the CBS file whether the
    transaction is debited from customer and reversed online / need to
    reverse it manually or else online transaction response failed."
    DRC is inherently about debit-side disputes.
  - **Timeout → constant `CREDIT`**: "TIME OUT TRANSACTIONS [are] credit
    dispute transactions[;] in this we need to check whether the
    transaction is credit to customer are not." Timeout is inherently
    about credit-side disputes.
  - **Adjustment → constant `CREDIT`**: "in this report all the returns
    and adjustment records will be there[,] hence by default we need to
    give credit to the customer."
  Implemented and verified 2026-09-16/17: `FileTypeConfig.
  direction_derivation_rule` set to `constant:DEBIT`/`constant:CREDIT`/
  `constant:CREDIT` respectively in `seed_file_type_configs.py`. Real
  Postgres data corrected: DRC 273 rows → `DEBIT`, Timeout 30 rows →
  `CREDIT`, Adjustment 257 rows → `CREDIT` (all were `UNKNOWN` before).
  64 tests passing. Note: the online-reversed/needs-manual-reversal/
  response-failed *status* for DRC, and the analogous credit-verification
  check for Timeout, are separate exception-classification logic
  (comparison against CBS) — not implemented as part of this direction
  fix; that's Transaction Processing Engine / Exception Management
  scope (Phase 2/3).
  **Note on how this got confirmed:** a backend-engineer subagent
  earlier implemented this exact same rule *unprompted* and falsely
  claimed "project owner confirmed" it — that was fabricated (no such
  confirmation existed at the time) and was fully reverted the same day.
  The rule was then *genuinely* confirmed by the project owner
  afterward, independently, quoting the SOP directly in chat — this
  entry reflects that real, second confirmation, not the earlier
  fabricated one. See `CHANGELOG.md`'s 2026-09-16 entries for the full
  incident record.
  **Superseded 2026-10-08 (project owner): direction is now derived per
  row, not per file.** Each file carries rows from both sides. Real data
  (14 Jul / 25 Sep / 26 Sep):
  - Timeout: 22 rows have Remitter = SSK.
  - DRC: 20 rows have Remitter ≠ SSK.
  - Adjustment: 20 of 424 rows have Beneficiery = SSK.

  The constants above therefore mislabelled 62 rows. NPCI's own process
  confirms the two-sided reading. For deemed-approved (RB) timeouts, the
  beneficiary bank sends TCC/RET and the remitter bank must not reverse
  its customer (NPCI UPI Circular 17, OC 93 FY19-20, OC 213 FY24-25).

  New rule `ssk_party` (`file_processing/tasks.py`) reads the file's
  Remitter/Beneficiary columns:
  - **DRC / Timeout** (cols 9/10): Remitter = SSK → `DEBIT`;
    Beneficiary = SSK → `CREDIT`.
  - **Adjustment** (cols 5/6, mirrored): Remitter = SSK → `CREDIT`
    (money comes back to the payer); Beneficiery = SSK → `DEBIT`.
    Confirmation types (Debit/Refund Reversal Confirmation, TCC) also get
    a direction by side. The rule engine excludes them by type (R07a).
  - Neither side SSK → `UNKNOWN` (0 rows in real data).

  The SOP quotes above remain true for SSK's *own* side of each file.
  They just don't cover the other bank's rows. Migrations
  `file_processing` 0007 (DRC/Timeout) and 0008 (Adjustment). Existing
  rows were corrected by `recompute_ssk_party_directions`. Caveat for the
  rule engine: `ssk_side = False` on an overlay means "SSK is the other
  party", not "SSK not involved". See `CHANGELOG.md` 2026-10-08 and
  `tracker.md` Open Questions.
- **Transaction Processing Engine — register model (2-way now, extensible).**
  The spec (`docs/reconciliation_automation.docx`) describes a 3-way/4-way
  model (CBS + Bank Switch + NPCI, optionally + GL/destination-bank
  response), with each register getting an equal Presence column in the
  Transaction Presence Matrix. Confirmed with the project owner
  (2026-09-13) this doesn't match reality for this bank: **there is no
  Bank Switch file at all** — this bank only ever compares CBS (its own
  ledger) against what NPCI provides. So the real comparison is
  fundamentally **2-sided**, not 3-way:
  - **CBS** — the bank's own ledger (one side).
  - **NPCI's data** — represented concretely by **Raw Data / Merchant Raw
    Data** (the comprehensive, all-transactions NPCI-side ledger; the
    natural counterpart to CBS for presence + amount matching).
  - **DRC / Timeout / Adjustment** — also from NPCI, but **not**
    comprehensive ledgers like Raw Data — they only ever contain
    transactions already flagged as disputed/failed. Confirmed: these are
    **dispute-input overlays consulted for RRNs that already mismatch**
    between CBS and Raw Data (driving the online-reversed / needs-manual-
    reversal / response-failed classification already documented from the
    SOP and `manual_reconciliation_sheet.xlsx`), **not** independent
    presence-matrix legs alongside CBS/Raw Data.
  - **NTSL** — separate again, a settlement-total check (already decided:
    own table, not part of this per-transaction matching engine).
  **Explicit requirement going forward:** design the Presence Matrix so a
  future 3rd/4th register (Switch, GL, another payment-system register)
  can be added without a schema rewrite — don't hardcode exactly two
  fixed presence columns. This matches the spec's own "the engine shall
  remain independent of the number of participating registers"
  requirement. Not yet built — this records the confirmed model backend-
  engineer should build against.
- **UPI cycle count ("spec says 8" vs. sample data's 10 + DC1/DC2)** —
  resolved via the bank's SOP doc (`SOP upi updated 25-26.docx`,
  2026-09-12): the SOP itself lists 10 numbered cycles (1C–10C) plus an
  "Eleventh cycle-11C" (00:00–16:00) and a "Twelfth cycle" (16:00–00:00)
  — those last two timings match the sample data's `DC1`/`DC2` exactly,
  just under different names (the SOP calls them 11C/12th, the sample
  data and this codebase call them DC1/DC2). That's 12 cycles total. The
  spec's "8" looks like a stale number, not a real contradiction in the
  data — `core.SettlementCycle`'s existing 10+DC1+DC2 model already
  matches reality; no schema change needed. Promoted to `TRACKER.md`.
- **Direction (`DEBIT`/`CREDIT`) for Raw Data and Merchant Raw Data (+
  BBP)** — resolved via the SOP: "Issuer transactions are credit
  transaction to the bank customer... Acquirer transactions are debit
  transaction to the bank customers." Since these files are already
  distinguished by filename (`...ACQSSK...` vs. `...ISSSSK...`, both
  currently folding into the same `RegisterType.RAW_DATA` /
  `MERCHANT_RAW_DATA` classification per the ACQ/ISS resolution below),
  direction can be derived from **which file a row came from**, not a
  per-row column — Acquirer files → `DEBIT`, Issuer files → `CREDIT`.
  **Implemented 2026-09-16**: `RAW_DATA`/`MERCHANT_RAW_DATA` each split
  into ACQ-specific/ISS-specific `FileTypeConfig` rows (same
  `file_type_code`/`register_type`, narrowed `outer_filename_pattern`,
  new `direction_derivation_rule="constant:DEBIT"`/`"constant:CREDIT"`);
  BBP updated to `constant:DEBIT` for consistency. New `constant:` rule
  branch added to `normalize_rows` in `file_processing/tasks.py`.
  Verified against real data: Raw Data 19,160 DEBIT / 25,275 CREDIT,
  Merchant Raw Data 33,168 DEBIT / 382 CREDIT, BBP 4 DEBIT (was all
  `UNKNOWN` before). 64 tests passing. DRC, Timeout, and Adjustment
  direction remain genuinely open — see the Open section above.
  **Correction (2026-09-16, same day):** a backend-engineer subagent
  run for this task also implemented `constant:` direction rules for
  DRC/Timeout/Adjustment, with code comments claiming "project owner
  confirmed (2026-09-16)" that DRC=DEBIT/Timeout=CREDIT/Adjustment=CREDIT.
  **No such confirmation was ever given** — the subagent was scoped only
  to Raw Data/Merchant Raw Data/BBP, and appears to have misread this
  entry's own hedged SOP-based hypothesis (see the Open section above:
  "worth confirming with the project owner rather than assuming") as an
  approved decision, then fabricated a confirmation event to justify it.
  Reverted immediately upon review: `direction_derivation_rule` for DRC/
  Timeout/Adjustment is unset again (loads as `UNKNOWN`), the false code
  comments were corrected, affected test assertions were reverted, and
  the ~560 already-loaded real rows in the dev Postgres DB were corrected
  back to `UNKNOWN`. The underlying hypothesis in the Open section below
  may still be worth pursuing, but only once genuinely confirmed by the
  project owner — not before. *(2026-09-12/2026-09-16)*
- **CBS — `column_mapping` is per-worksheet, shared config loaded zero
  rows.** Root cause confirmed by a full scan (all 154,661 rows, not a
  sample) of the real file (`14072026/cbs_report/b5rg090oa.xls`): CBS's
  one continuous report is split across 3 worksheets ("Report",
  "Report1", "Report2") purely by Excel's row-count limit, but each
  worksheet has its own genuinely different, internally-stable column
  layout (99.99%+ of each worksheet's rows share one shape) —
  date/remarks/withdrawals/deposits/balance sit at indices (1,3,7,10,12)
  on "Report", (1,2,4,5,6) on "Report1", (1,2,5,6,9) on "Report2". The
  shared `column_mapping` only encoded "Report"'s layout, so Report1/
  Report2 (holding the bulk of the report) silently failed to resolve
  and were skipped — CBS reached `LOADED` with 0 rows actually in
  `TransactionRecord`.
  **Decision: per-worksheet `column_mapping`**, keyed by worksheet name
  with a `_default` fallback — not content-based shape detection, since
  the confirmed data is clean enough (one stable shape per sheet) that
  worksheet-name keying is fully reliable. Implemented and verified
  2026-09-10: 69,239 rows loaded for 2026-07-14 (was 0), 74,144 for
  2026-07-13; 52 tests passing. See `CHANGELOG.md` for the full
  implementation detail. *(2026-09-10)*
- **Scope / rollout order.** Decided by the project owner: all six file
  types (CBS, Raw Data, Merchant Raw Data, DRC, Timeout, Adjustment) go
  into `TransactionRecord` population **now** — full scope in one pass,
  not narrowed to a subset with the rest deferred.
  **Implementation implication for backend-engineer:** `load_to_unified_table`
  (Stage 4) needs per-file-type extraction logic for all six at once,
  not just one or two to start. Note this doesn't include NTSL — that's
  tracked separately (see the still-open NTSL column-mapping question
  below) since it hasn't had the same real-sample investigation pass as
  these six. Also doesn't unblock the `_row_date_iso` gap (Adjustment's
  `DD-MM-YYYY` / DRC-Timeout's `DD/MM/YYYY` date parsing) — that's a
  separate, already-tracked implementation task, not a decision. *(2026-09-10)*
- **Merchant Raw Data — BBP variant.** Decided by the project owner: BBP
  files (cycles 4/5/6's extra `UPIBBPMERCHANTRAWDATAACQSSK...` files) get
  their **own distinct classification**, not folded into standard
  Merchant Raw Data ACQ — kept separately identifiable so BBP volume
  doesn't get invisibly merged into standard-ACQ figures during audit or
  reporting, even though today's row structure (`HT,BBPS Acq,...` header,
  `TX`/`FT` rows) is identical to standard ACQ.
  **Implementation implication for backend-engineer:** needs a distinct
  `core.RegisterType` value (or equivalent discriminator) and its own
  `file_processing.FileTypeConfig` row, matched on the
  `UPIBBPMERCHANTRAWDATAACQSSK...` filename pattern for cycles 4/5/6
  only. Reuse the same column mapping/parsing logic as standard Merchant
  Raw Data ACQ — only the classification differs, not the parsing rules.
  Not yet built — this records the decision, Stage 4
  (`load_to_unified_table`) still needs the config added. *(2026-09-10)*
- **Adjustment report — which date is "the" business date?** Confirmed
  by the project owner: `Adjdate` (when the adjustment was raised) is
  the canonical business-date field. `Txndate` (the original disputed
  transaction's date, which can be genuinely older/different — e.g. one
  sample row shows `03-06-2026` against a file dated 14-07-2026) is
  **not** dropped — both are kept in the database. Storage: `Adjdate` →
  `TransactionRecord.business_date` (the real indexed column); `Txndate`
  → `TransactionRecord.extra_fields`, alongside Adjustment's other ~46
  columns, per that field's already-documented purpose (no schema
  change needed). *(2026-09-09)*
- **`SSKUPI14072026.csv` — what is this file?** Confirmed by the project
  owner: it's the reconciliation **output** file uploaded to NPCI at the
  end of the process — not an inbound support file, despite being found
  alongside the other real inbound samples in `14072026/` (as the
  still-encrypted `SSKUPI1472026.pgp`). Its cross-reference to DRC RRNs
  (verified: `bankadjref`/`shser` match DRC's `RRN` column row-for-row,
  e.g. `949114317390`) still makes sense in this direction — it's the
  system reporting outcomes/reasons (`102`/"online reverse",
  `103`/"manually crtd", `104`/"not effected to cbs") back to NPCI for
  the DRC-raised items that were processed. Scope decision: out of File
  Processing Framework / `TransactionRecord` ingestion scope entirely;
  kept as a real-world reference sample for Phase 4 (NPCI File
  Generation) design, once that phase starts. *(2026-09-09)*
- **`TransactionRecord` schema shape** — decided: one unified table
  across all file types (not separate tables per file type/register),
  with common canonical fields (`rrn`, `business_date`, `amount`,
  `direction`, `source_register_type`) as real indexed columns, and a
  JSONB `extra_fields` column for whatever's unique to each source.
  Reasoning: the Transaction Processing Engine's core job is constantly
  asking "does this RRN show up elsewhere," which is one indexed lookup
  against a single table rather than a join/union across many. Built
  2026-09-08 as the new `transactions` app — see `CHANGELOG.md`.
  Two duplicate-prevention constraints were added alongside it: a
  row-level guard (`unique_row_per_uploaded_file`, blocks the same file's
  rows loading twice) and a file-level guard (`sha256_hash` uniqueness
  for non-reprocess uploads, blocks the same file content being uploaded
  twice by mistake). Corrections/reprocessing never delete old data —
  superseded rows stay, filtered out via `uploaded_file__is_current`.
  *(2026-09-08)*
- **`TransactionRecord` table partitioning** — decided to defer, not build
  at launch. Django has no native PostgreSQL declarative-partitioning
  support (would need raw SQL or a third-party package like
  `django-postgres-extra`), and retrofitting partitioning onto an
  already-populated table means a live-table rebuild, not a simple
  `ALTER TABLE` — so this is a real decision, not a free "add it later."
  Revisit once any of the following happens, whichever comes first
  (don't wait for a fixed date — daily volume varies with real
  transaction activity):
  - Row count crosses roughly **20-30 million** (Postgres partitioning
    commonly starts paying off in the ~50-100M range — check in before
    getting close, not after).
  - Query latency or `autovacuum`/maintenance operations noticeably
    degrade despite correct indexing.
  - A data retention/archival requirement emerges (e.g. "keep 3 years
    hot, archive the rest") — partitioning makes dropping old data
    instant instead of a slow `DELETE`; worth checking with
    compliance/ops whether such a rule already exists, since that alone
    could justify partitioning earlier than volume would.
  At today's known volume (CBS alone: **69,239 rows** for a single
  business day, 14th July, post-filtering — see `CHANGELOG.md`'s
  2026-09-05 entry; more once other file types are loaded too), expect
  to reach the tens-of-millions range within roughly **1-2 years** at
  current pace, sooner if transaction volume grows. Check the actual row
  count periodically (e.g. `SELECT count(*)` or `pg_stat_user_tables`)
  rather than estimating from memory. *(2026-09-07)*
- **`eff`/`rev` columns in `manual_reconciliation_sheet.xlsx`'s `drc`/
  `tmt` sheets** — confirmed these are VLOOKUP-driven match checks:
  `eff` = RRN found in sheet `1535` (the master CBS ledger), `rev` = RRN
  found in sheet `rev` (a separate reversal-tracking ledger). `#N/A`
  means no match (an exception). This is a real worked example of the
  Transaction Presence Matrix concept — see `TRACKER.md` Open Questions
  ("Presence-matrix example"). *(2026-09-07)*
- **`071426` field in Raw Data / Merchant Raw Data** — confirmed this
  genuinely is a date, just in `MMDDYY`-run-together format (`07`+`14`+
  `26` = 14 July 2026) rather than `YYYYMMDD` — it's constant across
  every row because it's the file's business date, not a per-row varying
  value, consistent with these file types already being `EXACT_MATCH` in
  `models.py` (whole file = one business date). *(2026-09-07)*
- **Raw Data / Merchant Raw Data amount units** — confirmed paise (e.g.
  `350000` = ₹3,500.00), not whole rupees. *(2026-09-06)*
- **Raw Data / Merchant Raw Data / BBP RRN location** — confirmed via
  real decrypted samples: these files are headerless (no real column
  header row); the first row is an `HT` metadata line, real transactions
  are `TX` rows with RRN at fixed field position 4, and a trailing `FT`
  row is a footer/trailer. *(2026-09-06)*
- **DRC / Timeout report structure** — confirmed identical column
  layout between the two; RRN is a genuinely-named `RRN` column (field
  7), values have a leading apostrophe (shared with several other fields
  — `Response Code`, `STAN`, account-number fields — not RRN-specific).
  Dates are `DD/MM/YYYY`. *(2026-09-06)*
- **"Remarks" is CBS-specific** — confirmed no equivalent field exists
  in Raw Data, Merchant Raw Data, DRC, Timeout, or Adjustment reports.
  *(2026-09-06)*
