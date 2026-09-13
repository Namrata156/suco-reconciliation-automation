# Reconciliation Automation — Tracker

Status values: Not Started / In Progress / Blocked / Done
Team size: 1–2 engineers

## Timeline (planning estimate — fill in dates once Phase 0 starts)

| Phase | Scope | Estimate | Start | Target end | Status |
|---|---|---|---|---|---|
| 0 | Close spec gaps + lock tech stack | 1.5–2 weeks | 2026-08-19 | *overrun — needs re-baseline* | In Progress (tech stack locked; spec-gap closure deliberately deferred) |
| 1 | Database Design + File Processing Framework | 3–4 weeks | 2026-08-28 | 2026-09-18 – 2026-09-25 | In Progress |
| 2 | Transaction Processing Engine + Business Rule Engine | 5–6 weeks | | | Not Started |
| 3 | Exception Management + Maker-Checker Workflow | 3–4 weeks | | | Not Started |
| 4 | Settlement Control (NTSL) + NPCI File Generation | 3 weeks | | | Not Started |
| 5 | API + Front End | 4–5 weeks | | | Not Started |
| 6 | Reporting Module | 2–3 weeks | | | Not Started |
| 7 | UAT, hardening, rollout | 2–3 weeks | | | Not Started |

Total estimate: ~26–30 weeks (6–7 months). Update Status per phase as
work moves (Not Started / In Progress / Blocked / Done) and note actual
dates as they happen — that's what lets later estimates get more
accurate than the initial planning numbers above.

**Phase 0 overrun (flagged 2026-09-10):** estimated 1.5–2 weeks, open
since 2026-08-19 (~3 weeks as of 2026-09-10). The tech stack closed on
time; the five stub spec sections did not, and were deliberately
deferred to unblock Phase 1. Those stubs feed Phases 3–5, so this is a
real schedule risk, not just stale bookkeeping. **Target end left blank
deliberately — re-baselining is a project-owner decision, not a PM one.**

**Phase 1 exit criteria (added 2026-09-10):** Phase 1 closes when
`load_to_unified_table` (Stage 4) populates `TransactionRecord` for every
in-scope file type, and the full pipeline runs end to end against
`sample_decrypted_files/`. Schema existing is not sufficient — the
current stub is why "Database Design: done?" reads as ambiguous.

## Components

| Component | Phase | Status | Owner | Notes |
|---|---|---|---|---|
| Database Design | 1 | In Progress | backend-engineer | `core`/`file_processing` Django models built (PaymentModule, SettlementCycle, FileTypeConfig, UploadedFile, FileValidationException) and migrated. **2026-09-08: `transactions.TransactionRecord` built** — one unified table across all file types (rrn/business_date/amount/direction/source_register_type as indexed columns, JSONB `extra_fields` for source-specific data), plus row-level (`unique_row_per_uploaded_file`) and file-level (`sha256_hash` uniqueness for non-reprocess uploads) duplicate-prevention constraints. Partitioning deliberately deferred — see Open Questions. **2026-09-10: population logic built** — `core.RegisterType.MERCHANT_RAW_DATA_BBP` added; a partial composite `(rrn, business_date)` index added supporting the RRN+date matching key direction. 48 tests passing (up from 31) |
| File Processing Framework | 1 | In Progress | backend-engineer | Design complete; code scaffolded: format sniffing, CBS/NTSL/CSV parsers, pagination stripping, Celery pipeline stages 1-3 (parse/validate/normalize). NTSL excluded from Stage 4 (deferred — settlement summary, no RRN, see Open Questions). Merchant Raw Data BBP variant resolved 2026-09-10 — own distinct classification, separate from standard ACQ. **2026-09-10: Stage 4 (`load_to_unified_table`) implemented and verified end-to-end against real sample data** — RAW_DATA 1,810 rows loaded, MERCHANT_RAW_DATA 4,123, MERCHANT_RAW_DATA_BBP 1, DRC 273, TIMEOUT 30, ADJUSTMENT 257 (incl. a row whose `Adjdate` genuinely differs from the file's nominal date, correctly reflected). `_row_date_iso` extended for DRC/Timeout `DD/MM/YYYY` and Adjustment `DD-MM-YYYY`. **CBS is the exception: reaches `LOADED` status but loads 0 rows** — a newly-discovered per-worksheet column-mapping mismatch means the config that matches every existing CBS test doesn't match the worksheets ("Report1"/"Report2") holding all 69,239 real rows; see `doubts.md` and the new Open Questions row below. **2026-09-10: CBS 0-rows bug fixed** — confirmed via a full scan of the real file that each of CBS's 3 worksheets (one continuous report, split by Excel's row limit) has its own stable column layout; `column_mapping` now resolves per-worksheet. 69,239 rows now load for 2026-07-14 (was 0). All 6 in-scope RRN-bearing file types are now genuinely loading real data. Still flagged, unresolved: no direction (`DEBIT`/`CREDIT`) derivation rule confirmed for the 5 non-CBS types (all load as `UNKNOWN`), and Adjustment's amount column (`Adjamount` vs `Txnamount`) is unconfirmed — both in `doubts.md`. Adjustment's business-date field resolved 2026-09-09 — `Adjdate` is business_date, `Txndate` kept in `extra_fields` (see `doubts.md`). `SSKUPI14072026.csv` resolved 2026-09-09 — it's an NPCI-bound output file, out of this component's scope (see `doubts.md` and NPCI File Generation row below). IMPS cycle-wise handling still open. CBS business-date selection (3-day range files like GL Extract) now interactive: pipeline pauses at AWAITING_DATE_SELECTION with detected dates surfaced, user picks one via a Django-admin action + intermediate page — this is a Phase 1 stand-in only; a real UI/API "pick from detected dates" flow belongs in Phase 5 (API + Front End). **2026-09-07: PGP decryption removed from scope entirely** — bank/NPCI private key is confidential and will never be shared with this system; the pipeline now assumes every input file arrives already decrypted (`decryption.py`/`secrets_backend.py` deleted, former Stage 1 `decrypt_file` removed, pipeline starts at format sniffing) |
| Transaction Processing Engine | 2 | In Progress | backend-engineer | Presence matrix, matching keys, 3-way/4-way recon defined |
| Business Rule Engine | 2 | In Progress | backend-engineer | Rule evaluation & repository defined; error/response code table missing |
| Exception Management | 3 | Not Started | backend-engineer (logic) / frontend-engineer (UI) | Header only, no content |
| Maker-Checker Workflow | 3 | Not Started | backend-engineer (logic) / frontend-engineer (UI) | Header only, no content |
| Settlement Control / NTSL Validation | 4 | In Progress | backend-engineer | Validation and exceptions defined |
| NPCI File Generation | 4 | Not Started | backend-engineer | Header only, no content (functional requirement exists, design doesn't). 2026-09-09: a real sample of an NPCI-bound output file now exists (`SSKUPI14072026.csv` — cross-references DRC RRNs with reason codes 102/103/104), useful reference once this component's design starts; see `doubts.md` |
| API | 5 | Not Started | backend-engineer | Header only; framework now locked (Django/DRF), no endpoints designed yet |
| Front End Design | 5 | Not Started | frontend-engineer | Header only, no content; framework choice (React vs. Django templates+HTMX) still open |
| Reporting Module | 6 | Not Started | backend-engineer (data/queries) / frontend-engineer (UI) | Not yet started |

## Open Questions

| Item | Location in spec | Owner | Due |
|---|---|---|---|
| IMPS cycle-wise file handling | File Processing Framework > Cycle-Wise File Processing | | |
| Transaction register flowchart | Transaction Processing Engine > Transaction Registers | | |
| Presence-matrix example | Transaction Processing Engine > Transaction Presence Verification | | Partially addressed — `manual_reconciliation_sheet.xlsx` (project owner's real manual working sheet) contains a worked VLOOKUP-based match example: `drc`/`tmt` sheets' `eff`/`rev` columns check each RRN against the master CBS ledger (`1535`) and a separate reversal ledger (`rev`) respectively, `#N/A` = exception. Still needs formal design as an actual Presence Matrix for Phase 2, but real ground truth now exists — see `doubts.md` |
| Error/response code repository table | Business Rule Engine > Error Code and Response Code Management | | |
| ~~Encrypted file handling approach~~ | Tools under consideration | | Resolved 2026-08-31 (library choice), then superseded 2026-09-07 — decryption removed from scope entirely, not a library choice; see Tech Stack table |
| ~~UPI cycle count contradicts sample data (spec says 8, real files show 10 numbered cycles + DC1/DC2)~~ | File Processing Framework > Cycle-Wise File Processing | | Resolved 2026-09-12 — the bank's SOP doc (`SOP upi updated 25-26.docx`) itself lists 12 cycles (10 numbered + "11C"/"12th," timed identically to the sample data's `DC1`/`DC2`, just named differently). Spec's "8" looks stale, not a real data contradiction; existing `core.SettlementCycle` model (10 + DC1 + DC2) already matches reality. See `doubts.md` |
| ~~Unidentified file `SSKUPI1472026.pgp` in sample data — matches no Source File Table entry~~ | File Processing Framework > Source File Details | | Resolved 2026-09-09 — it's the reconciliation output file uploaded to NPCI (not an inbound file), kept as a reference sample for Phase 4 (NPCI File Generation) rather than modeled in this component; see `doubts.md` |
| ~~No "Bank Switch" source file exists in spec or sample data, yet 3-way/4-way recon assumes a Switch register~~ | Transaction Processing Engine > Transaction Registers | | Resolved 2026-09-13 — project owner confirmed: this bank only ever compares CBS (its own ledger) against the files NPCI provides; there is no Switch-side file and never has been. Real comparison is 2-sided (CBS vs. NPCI-sourced data), not 3-way. Decision: build the 2-way comparison now, but design the Presence Matrix to stay extensible so Switch/GL/a 3rd or 4th register can be added later without a schema rewrite — per the spec's own "independent of the number of participating registers" requirement. See `doubts.md` |
| ~10% of CBS rows fail RRN validation — Transaction Processing Engine needs a fallback matching strategy for these | Transaction Processing Engine > Transaction Identification | | |
| Reprocessing semantics when a corrected file's row set genuinely differs (not just a hash change) | File Processing Framework > Reprocessing | | |
| Real-world intake: single-file upload (as specified) vs. bulk folder ingestion (as sample data is organized) | File Processing Framework > File Intake | | |
| Upload flow: let the user explicitly select a file type (CBS/DRC/Timeout/etc.) then upload the matching file — plus explore whether the system can auto-identify a file's type from content (extending `sniff.py`'s existing format-detection approach to type-detection). Related to, but distinct from, the single-file-vs-bulk-folder row above | File Processing Framework > File Intake; API / Front End (Phase 5) | project owner | Not urgent (Phase 5 scope) — noted early so it's designed alongside the intake API; see `doubts.md` |
| CBS business-date selection currently uses a Django-admin stand-in (custom action + intermediate page), not a real API/UI — needs a proper "pick from detected dates" flow once Phase 5 starts | File Processing Framework > Date Validation Mode; API (Phase 5 Front End) | | |
| ~~No PGP private key available to decrypt real sample data for Raw Data, Merchant Raw Data, DRC, Timeout, and Adjustment reports~~ | File Processing Framework > Source File Details / Database Design > TransactionRecord schema | project owner | Superseded 2026-09-07 — moot: decryption removed from this system's scope entirely (see Tech Stack table), project owner supplies pre-decrypted files directly (`sample_decrypted_files/`) as the standing process, not a one-off unblock |
| ~~`_row_date_iso` helper only parses ISO-datetime and bare `YYYY-MM-DD`~~ | File Processing Framework > Normalization | | Resolved 2026-09-10 — extended for `DD/MM/YYYY` (DRC/Timeout) and `DD-MM-YYYY` (Adjustment) as part of Stage 4 build |
| ~~CBS loads 0 rows despite reaching `LOADED` status~~ | File Processing Framework > Normalization; Database Design > TransactionRecord | | Resolved 2026-09-10 — root cause confirmed via a full (not sampled) scan of the real file: CBS's one continuous report splits across 3 worksheets by Excel's row limit, each with a genuinely different but internally-stable column layout. Fixed with a per-worksheet `column_mapping` (keyed by worksheet name, `_default` fallback); other 6 file types unaffected. Verified: 69,239 rows loaded for 2026-07-14 (was 0), 74,144 for 2026-07-13. 52 tests passing (up from 48). See `CHANGELOG.md` and `doubts.md` |
| Direction (`DEBIT`/`CREDIT`) not derived for Raw Data, Merchant Raw Data (+ BBP), DRC, Timeout, Adjustment — all load as `UNKNOWN`; only CBS has a confirmed rule | Transaction Processing Engine > Transaction Identification; File Processing Framework > Normalization | project owner | See `doubts.md` |
| Adjustment amount column unconfirmed: currently mapped to `Adjamount`, not `Txnamount` — the two were equal in every sample row inspected, so undistinguishable from data alone | File Processing Framework > Normalization | project owner | See `doubts.md` |
| ~~Merchant Raw Data — BBP variant: does it need its own classification, or fold into standard ACQ processing?~~ | File Processing Framework > Source File Details | | Resolved 2026-09-10 — project owner decided BBP gets its own distinct classification (separate from standard Merchant Raw Data ACQ), so BBP volume stays identifiable for audit/reporting rather than merging into standard-ACQ figures. Row shape/parsing logic unchanged, only classification differs. Not yet implemented — backend-engineer still needs to add the `RegisterType`/`FileTypeConfig` entries before Stage 4 can include it; see `doubts.md`. One of three Stage-4 blockers resolved, two remain (scope/rollout order, NTSL column mapping) |
| ~~File-type scope / rollout order: do all six file types go into `TransactionRecord` population now, or start narrower?~~ | File Processing Framework > Normalization; Database Design > TransactionRecord | | Resolved 2026-09-10 — project owner decided all six (CBS, Raw Data, Merchant Raw Data, DRC, Timeout, Adjustment) go in now, full scope in one pass. NTSL excluded from this decision (tracked separately, see NTSL row below). Not yet implemented — backend-engineer still needs per-file-type extraction logic for all six in Stage 4; see `doubts.md`. Two of three Stage-4 blockers now resolved, one remains (NTSL column mapping) |
| NTSL is a settlement **summary/totals** report (Description/No of Txns/Debit/Credit, ~175 category rows + a final total), confirmed via real sample — **no RRN column exists at all**. Doesn't fit `TransactionRecord`'s RRN-indexed per-transaction design; needs its own settlement-summary table, not a column-mapping fix. **Deferred by project owner 2026-09-10** — does not block loading the other six file types | File Processing Framework > Source File Details; Settlement Control > NTSL Validation | project owner / backend-engineer | Deferred — parked for Phase 4 design; see `doubts.md` |
| ~~Adjustment report — which date is "the" business date (`Adjdate` vs `Txndate`)?~~ | File Processing Framework > Normalization | | Resolved 2026-09-09 — `Adjdate` is the canonical business date → `TransactionRecord.business_date`; `Txndate` retained in `extra_fields` (not dropped). No schema change needed; see `doubts.md` |
| `TransactionRecord` table partitioning deferred at launch (Django has no native Postgres partitioning support; retrofitting onto a populated table means a live rebuild) — revisit once row count crosses ~20-30M, query/vacuum performance degrades, or a data retention/archival requirement emerges, whichever comes first. At current pace (CBS alone: 69,239 rows for one business day) expect the tens-of-millions range within ~1-2 years — check actual row count periodically rather than waiting for a fixed date. See `doubts.md` | Database Design > TransactionRecord schema | project owner / backend-engineer | |

## Tech Stack (approved 2026-08-28)

| Layer | Choice | Notes |
|---|---|---|
| Backend / API | Django + Django REST Framework | Fits rule-engine and maker-checker patterns well |
| Database | PostgreSQL | Stronger transactional integrity for settlement/recon; JSONB for rule definitions; better reporting support |
| File parsing | Python (pandas / openpyxl) | UPI file intake, normalization, RRN extraction, cycle-wise handling |
| Task/queue | Celery + Redis | Async file processing, scheduled cycle-wise ingestion, NPCI file generation jobs |
| Encrypted-file handling | N/A — out of scope | 2026-09-07: bank/NPCI private key is confidential and will never be shared with this system; the pipeline receives pre-decrypted files only (see `sample_decrypted_files/`). Supersedes the earlier `python-gnupg`/`PGPy` choice (corrected 2026-08-31) — this isn't a library swap, decryption isn't performed by this system at all |
| Frontend | React (or Django templates + HTMX as lighter alternative) | Depends on richness needed for Exception Management / Maker-Checker UI |
| Auth | Django built-in auth + role-based permissions | Administrator / Maker / Checker / Auditor roles |
