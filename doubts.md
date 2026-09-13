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

### Direction (`DEBIT`/`CREDIT`) not derived for DRC, Timeout, Adjustment
Only CBS has a confirmed direction-derivation rule (`remarks_prefix`,
already implemented). Raw Data and Merchant Raw Data (+ BBP) are now
resolved too — see Resolved section below, via the bank's SOP doc. DRC,
Timeout, and Adjustment still load into `TransactionRecord` with
`direction=UNKNOWN` — no rule for deriving debit/credit is documented
anywhere in this file, `TRACKER.md`, or the SOP. **Needs confirmation**
of the actual rule for these three (e.g. a column that states it
directly, a sign convention, a transaction-type code) before this can be
trusted for anything direction-sensitive (net settlement calculations,
some reconciliation checks). Note: the SOP does describe DRC as
inherently debit-side disputes and Timeout as inherently credit-side
disputes (by the nature of what each report covers), which suggests a
possible **constant** direction per register type rather than a per-row
derivation — worth confirming with the project owner rather than
assuming, since it's a characterization of the report's purpose, not a
stated column rule.

### Adjustment — which amount column is "the" amount?
Backend-engineer mapped Adjustment's loaded `amount` to `Adjamount`
(column 17), not `Txnamount` (column 16) — the report has both, and in
every sample row inspected they were equal, so the choice couldn't be
confirmed from data alone. **Needs confirmation** of which is the
canonical value for `TransactionRecord.amount` (the two may diverge on
adjustment cases where the disputed amount and the adjustment amount
differ).

### NTSL — not an RRN-level register; deferred
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

## Resolved

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
  Not yet implemented — `FileTypeConfig`/Stage 4 still need a rule keyed
  on the ACQ/ISS filename distinction (currently collapsed since both
  variants share one classification and one `column_mapping`). DRC,
  Timeout, and Adjustment direction remain open — see the Open section
  above. *(2026-09-12)*
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
