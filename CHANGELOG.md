# Changelog

Running log of features added and modifications made to the codebase.
This is a chronological build history — for current status/schedule per
component, see `TRACKER.md`; for requirements, see
`docs/Reconciliation_Automation.docx`. Don't duplicate status here, and
don't duplicate this history there.

## 2026-10-20

- **Added: Phase 5 Slice 2 — Files and running a day (API + frontend).**
  - **API** (commit c9cbc6b): per module `/api/v1/<module>/files/` (list,
    upload), `files/detect/`, `files/<pk>/`, `files/<pk>/exceptions/`,
    `files/<pk>/select-date/`, `dates/`, `dates/<d>/`, `dates/<d>/run/`.
    New `file_processing/intake.py` (detection, upload, date-selection
    service; the Django-admin action now calls it),
    `reconciliation/runs.py` + `ReconciliationRun` (presence → rules +
    cases → settlement chain), `reconciliation/overview.py`, append-only
    `core.OperationLog` auditing date pick and run (Amendment 1). Settings
    `UPLOAD_MAX_FILE_BYTES` (150 MB default), `FILE_UPLOAD_TEMP_DIR`.
    Home summary adds `files_missing`.
  - **Frontend**: upload (check files → review table → per-file results),
    files list/detail, CBS date pick, business-date list and overview
    (checklist, Run button), menu entries for all roles (writes:
    Operations User only), auto-refresh every 5 s that pauses after 2 min
    idle, Refresh always available. Playwright `e2e/slice2.spec.ts` (needs
    `E2E_OPS_USER`/`E2E_OPS_PASSWORD`, a fresh DB, Celery; re-signs in
    after an expired session; waits configurable).
  - Tests updated for the new service and model: admin date-selection
    tests now patch `file_processing.intake.chain` and check the audit
    row; presence extensibility test expects `ReconciliationRun`.
- **Added: Phase 5 Slice 3 backend (uncommitted).** `api/results.py`
  (date results summary, paged/filtered list, match group detail;
  settlement per date with self-check and contributing transactions;
  settlement-case transactions) and `api/settings_views.py` (response
  codes, NTSL mapping, tolerance reads; propose/approve/reject for all
  three with self-approval confirmation; pending approvals; tolerance
  approve lists dates still on an older tolerance, no automatic re-run).
  `/meta/` adds `processing_statuses`, `file_type_configs` (ACQ/ISS) and
  Slice 3 lists. `exception_management/detail.py`: settlement payload and
  date overlay rows split out for reuse (case detail output unchanged).
- Verified: 622/622 Django tests, Vitest 92/92, lint/type-check/build
  clean, Slice 2 click-through passed on a fresh DB (306 cases, TALLIED).

## 2026-10-15

- **Added: Phase 5 Slice 1 — Foundation + cases (API + React frontend).**
  - **API v1** (new `api` app). `/api/v1/auth/{csrf,login,logout}/`,
    `/api/v1/me/` (roles, permissions, modules, session timings),
    `/api/v1/modules/`, `/api/v1/users/?role=`, and per module
    `/api/v1/<module>/{meta,summary,cases}/`: case list (server-paged,
    filters incl. new `due_date`, `ordering`), detail, history, every
    case action, bulk take/submit/approve. Case views subclass the
    existing `exception_management/api.py` views, so the same services
    run. `/api/exceptions/` unchanged (P2). Self-approval needs
    `confirm_self_approval: true` (server-enforced). Accounts with no
    role can't log in.
  - **Session timeout (P7)**: 15 min idle, renewed per request, ends on
    browser close. New settings: `DJANGO_CSRF_TRUSTED_ORIGINS`,
    `DJANGO_SECURE_COOKIES`, `FRONTEND_DIST_DIR` (see `.env.example`).
  - **Django serves the production build** (`api/spa.py`): any non-api/
    admin/static path → `frontend/dist`. No WhiteNoise (new package;
    pending P9).
  - **`config/settings.py` loads `.env` automatically** (stdlib only;
    shell variables win).
  - **Frontend** (new `frontend/`): Vite + React 19 + TypeScript +
    Mantine 8. Login, app shell, permission-driven role menus, module
    switcher (hidden while UPI only), 13-min timeout warning with return
    to the same address, Home "my work" counts, case queue, case detail
    and history, all case actions, bulk bar with eligibility reasons
    (incl. R13 refusal), self-approval confirmation. No background
    polling (would defeat the idle timeout). Bank name/logo is a text
    placeholder (P8).
  - Refactor: queue filters moved to `filter_queue()` in
    `exception_management/api.py`; `digest_counts()` takes an optional
    module.
  - PM-verified: `python manage.py test` **540 tests, OK** on Postgres
    (34 new); frontend type-check, lint, **Vitest 53/53**; Playwright
    click-through **3/3** (Maker takes R01 → DRC 102 → Checker approves
    → history; bulk-submit 3 LOW R01; R13 bulk refusal) run against a
    copy of the dev database (`reconciliation_e2e`), so dev cases are
    untouched. Production build served by Django checked (200 on `/`
    and deep links; 401 on `/api/v1/me/` when logged out).

## 2026-10-12

- **Added: Phase 4 Slice 2 — settlement result, gap attribution,
  settlement cases, tolerance.**
  - **Gap rules as code** (`settlement/gap_rules.py`). G01–G10 and Z
    exactly as approved 2026-10-11. Anything the rules don't cover is
    shown as UNEXPLAINED, never passed silently.
  - **Settlement result** (`settlement/reconcile.py`). Per module + date:
    readiness check first (self-check PASS on every cycle, including the
    switching-fee count check O4; current rule results for every match
    group), else NOT_READY with reasons. Per cycle × side: NTSL vs
    system, matched / explained / unexplained. DC1/DC2 vs Adjustment
    rows (G09). CBS-side SOP check with the corrected sign. TALLIED or
    UNEXPLAINED against the active tolerance. Re-runs supersede, never
    delete; the full calculation is kept as a snapshot. Command:
    `reconcile_settlement --date YYYY-MM-DD`; Celery wrapper in
    `settlement/tasks.py` (not chained automatically).
  - **Settlement cases** (`settlement/cases.py`). One date-level case
    (new case type SETTLEMENT) in the Phase 3 exception queue when a
    result is UNEXPLAINED. Maker explains, Checker accepts; never bulk.
    Changed figures withdraw a pending explanation; a later TALLIED run
    flags the case for review rather than closing it. New alerts and
    templates for settlement cases and reviews.
  - **Tolerance** (`settlement/tolerance.py`). Rs 0 / 0 txn by default
    (v1 seeded). Changed only by Administrator proposal + Checker
    approval, append-only event log, self-approval flagged.
  - Admin screens for results, cases and tolerance; exception admin, API
    and detail view extended for settlement cases.
  - Migrations: `core` 0009, `exception_management` 0008, `settlement`
    0003–0004.
  - PM-verified: `python manage.py test` **506 tests, OK** on Postgres
    (57 new). Real data 2026-07-14: `check_ntsl` 12/12 cycles PASS;
    `reconcile_settlement` SOP check difference Rs 43,050.77 fully
    explained (G04/G05/G07/G08), **unexplained Rs 0.00**.

## 2026-10-11

- **Added: Phase 4 Slice 1 — NTSL mapping, self-check, gap analysis.**
  New `settlement` app; it only reads `transactions.NtslSettlementLine`.
  - **Mapping as data (S1).** Versioned NTSL description → category map
    (exact or regex entries). Versions start PENDING; changes go
    Maker propose → Checker approve/reject (self-approval allowed with
    both roles, flagged). A proposal is refused if it leaves any loaded
    description unmapped or ambiguous. Event log is append-only. v1 seeded
    and ACTIVE: 50 entries, 267/267 seen descriptions mapped. New
    categories beyond the design list: MANDATE_FEE(_GST), ADJUSTMENT_FEE,
    PRE_ARBITRATION, NRP_FEE_GST.
  - **Self-check (S2).** Per cycle: Sub Totals = sum of lines above;
    Settlement Amount = Sub Totals net; Final = Settlement Amount + post-
    settlement lines. Fails on unmapped lines, missing/repeated totals, or
    two current files for one cycle. Re-runs supersede, never delete.
    Command: `check_ntsl --date YYYY-MM-DD`. Real data: 36/36 cycles PASS.
  - **Gap analysis (read-only, nothing built on it).** NTSL transaction
    lines equal NPCI's own rows exactly; after attribution the unexplained
    amount is Rs 0.00 on every date, cycle and side. DC1/DC2 tie to the
    Adjustment file (Q6). Proposed gap rules G01–G10 and a corrected SOP
    formula sign are in `docs/phase4_gap_rules_draft_v1.txt`; owner
    approved both, plus the new categories, the same day.
  - Migrations: `settlement` 0001–0002. One line in `config/settings.py`.
    32 new tests.

- **Added: email-ready alert outbox (Phase 3, design Amendment 3).** The
  mail server will be added later. Turning email on is a `.env` change
  only; steps are in `docs/email_alerts_setup.txt`.
  - **Outbox.** `core.Notification` holds one row per alert per recipient.
    - Content is append-only; only the delivery status, attempts and
      error change.
    - Statuses: held (no mail server), queued, sent, failed, skipped (no
      email address), dropped (stale).
    - Read-only admin for Administrator and Auditor.
  - **Recording and delivery** (`core/notifications.py`). Alerts are
    recorded only when the case change commits, and nothing is sent at
    that moment. A separate delivery run retries failures after 5 and 30
    minutes, and stops at 3 attempts.
  - **Alerts.** All go to role members only.
    - R07x case, new or changed to R07x on a re-run: Makers and Checkers.
    - Case flagged needs review: Checkers.
    - Action submitted: Checkers.
    - Action rejected: the case's maker.
    - Daily digest at 09:30 IST: Makers and Checkers.

    Bulk operations and sync runs send one summary.
  - **Commands.** `send_notifications` (with
    `--drop-held-older-than DAYS`), `send_alert_digest` (with
    `--dry-run`) and `send_test_email`. Celery wrappers and a beat
    schedule exist for later, but no worker is needed.
  - **Settings.** All `EMAIL_*` and `ALERT_*` values come from the
    environment, and mail is off by default.
  - **`.env.example`.** Placeholders only, now committed; `.gitignore` has
    an exception for it. `.env` stays ignored.
  - Migrations: `core` 0008; `exception_management` 0007.
  - **Dev DB, PM-verified.** The outbox is empty and no users exist. The
    988 cases are unchanged. `.env` is untouched.
  - **Engineer's check (in a rolled-back transaction).** With mail off,
    alerts were held and nothing was sent. With the console backend they
    went to SENT.

  Full suite: **417 tests, OK** (PM re-ran on Postgres).

- **Added: Exception Management + Maker-Checker, Slice 2 (Phase 3).**
  Design Amendments 1 and 2 (owner, 2026-10-11) are in
  `docs/phase3_design_draft_v1.txt`.
  - **Admin screens** (the interim interface until Phase 5):
    - **Queue:** design order, flags, filters.
    - **Case detail:** rule reason, facts, every dispute row for the RRN,
      action and event history.
    - **Action forms:** one per step, all through `services.py`. Buttons
      follow the user's role, and the role is checked again in the
      service.
    - **Read-only:** event and action screens.
  - **API** (`/api/exceptions/`): queue with filters and paging, case
    detail, one endpoint per step, bulk endpoints. It uses session login
    and CSRF.
  - **Bulk** take, submit and approve. Submit and approve are
    all-or-nothing and refused for:
    - HIGH cases
    - Pending Investigation cases (R06b, R07c, R13), per the owner's B3
    - cases with warnings
    - actions that need evidence
  - **Flags:**
    - **RRC already sent (A1):** set on every sync across dates.
    - **No deadline (A4):** the result needs action but no deadline was
      found.
    - **Needs review (A5):** a checker clears it with a remark.
    - **Previous-day hint (P5):** an R13 case shows when an R12 exists on
      the day before.
  - **New actions:** chargeback represent (A2). "Close without action" now
    completes when approved (A3).
  - **Code-table maker-checker** (P3, B2): editing or creating a response
    code needs a change request (maker proposes, checker approves), logged
    append-only. Direct create, edit and delete are blocked. Codes from an
    approved rule-table release still load through seeding.
  - **User activity log** (`core.UserActivity`): login, logout and failed
    login, with IP and browser, append-only. Readable by Administrator
    and Auditor. The append-only guard is shared in `core/append_only.py`.
  - **Staff status (B1):** only half done. Migration 0006 gives staff
    status to current role members. The signal that would do this
    automatically for new members was blocked by the file-permission
    prompt and is not built. Until it is, an Administrator ticks "staff
    status" by hand.
  - **Real data, PM-verified.**
    - Cases unchanged at 306 / 88 / 594, all OPEN.
    - The RRC warning is on 22 of 28 R06 cases on 25 Sep, plus 1 R06b on
      26 Sep. These are ops' next-day confirmations, so the warning
      works as intended.
    - Previous-day hint: 11 on 26 Sep.
    - Direct response-code create and edit are refused on the dev DB.
  - Migrations: `core` 0007; `exception_management` 0003–0006.

  Full suite: **388 tests, OK** (PM re-ran on Postgres).

- **Added: Exception Management + Maker-Checker, Slice 1 (Phase 3).** New
  app `exception_management`, built to the approved design
  (`docs/phase3_design_draft_v1.txt`, approved 2026-10-10, Amendment 1
  2026-10-11).
  - **Cases.** `ExceptionCase`: one case per payment module + business
    date + RRN, for every current rule result with a due date.
  - **Proposed actions.** `ProposedAction` is kept per submission, so a
    rejected proposal and its resubmission are both on record. The default
    action comes from the rule, and the mapping is one table in
    `actions.py`.
  - **Audit.** `CaseEvent` is append-only: edits and deletes are blocked
    on every ORM path.
  - **Lifecycle** (`services.py`): take, release, reassign, submit,
    approve, reject (reason required), mark done (external reference
    required) and remark. Each step checks the role and status and locks
    the row. Self-approval is allowed and flagged (`self_approved`).
  - **Evidence.** RRC 501 without a new credit requires the CBS reversal
    (date, amount, RRN) as evidence.
  - **Roles.** Five groups (Administrator, Operations User, Maker,
    Checker, Auditor), created by migration 0002.
  - **Case sync.** `sync_cases`, which also runs automatically after
    `evaluate_rules` and the Celery task, in the same transaction.
    Re-runs:
    - a case re-links to the new result
    - a rule change after approval flags the case for review and never
      changes it silently
    - a case no longer needing action closes as superseded
  - **Other flags.** `multiple_adjustment_rows`. Queue ageing and overdue
    reuse the rule-result logic.
  - **Matrix code.** `reconciliation/presence.py` was refactored: the
    Adjustment-row selection now lives in one function, plus a read-only
    `overlay_rows_for_date`. The matrix output is unchanged.
  - **Real data, PM-verified.**
    - Cases on 14 Jul / 25 Sep / 26 Sep: 306 / 88 / 594, all OPEN, one per
      due-dated result.
    - No case links to a superseded result.
    - Re-evaluating 25 Sep re-linked all 88 cases with 0 changes.
    - Multiple-adjustment flags: 1 / 6 / 1.
  - **Found on real data.** The 6 flagged R06 cases on 25 Sep already
    have a "Refund Reversal Confirmation" in the 26 Sep file. Per the
    owner's Amendment 1, a warning will be shown, built in Slice 2.

  Full suite: **327 tests, OK** (PM re-ran on Postgres).

## 2026-10-10

- **Changed: rule set v3.2, one business date at a time (owner decision
  2026-10-10).** Each business date is now reconciled only against its
  own files. Rule table amendment v3.2 is in
  `docs/phase2_rule_table_v3_approved.txt`.
  - **New rule set.** v3.2 is active and v3.1 is retired (`rule_engine`
    0005). v3.2 is the same as v3.1 except for R13.
  - **R13 (in CBS only).** Now Pending Investigation, MEDIUM, due T+1
    working day. A maker checks it by hand and a checker approves. Nothing
    is refunded automatically.
  - **Removed.** The Step 4 next-day re-check is gone: the
    awaiting / matched / escalated states, R12/R13 linking and the 00/RB
    clearing rule. Migration `rule_engine` 0004 drops the re-check fields.
  - **Real data, PM-verified.**
    - 0 of 233,081 groups differ from the reference SQL.
    - All current results use v3.2.
    - R13: 14 Jul (1) is due 15 Jul; 25 Sep (10) and 26 Sep (578) are due
      Mon 28 Sep.
    - The PM's field-by-field diff of v3.1 against v3.2 shows only R13
      changed.

  Full suite: **264 tests, OK** (PM re-ran on Postgres). This is 267,
  minus 16 re-check tests, plus 13 new tests.

- **Added: Phase 2 Step 5 validation report**
  (`docs/phase2_step5_validation_14jul.txt`). The system's 14 Jul results
  were compared with ops' manual sheet. This is a read-only check, with no
  code or data changes.
  - **Agreement.** DRC 265/265, Timeout 20/20 and Adjustment 18/18 match.
    The NPCI and CBS tabs agree row by row.
  - **No system bugs found.**
  - **Findings.** Same-payment-on-two-days pairs: 11 on 25/26 Sep and 1 on
    14/15 Jul. Ops resolves these by hand under v3.2.
  - **Phase 3.** Several Adjustment rows for one RRN.
  - **Ops sheet.** 794 on-us payments are listed as reversals in ops'
    `rev` sheet.

- **Changed: 2026 Karnataka bank holidays confirmed by ops.** The owner
  confirmed the list on 2026-10-10.
  - **Migration.** `core` 0006 sets `confirmed_by_ops=True` on all 22 rows
    and adds a note. It is reversible.
  - **Re-seeding.** `seed_bank_holidays` still leaves rows that already
    exist alone, so a confirmation is never reset. It now prints the real
    confirmed count.
  - **Future years.** A year loaded later, e.g. 2027, starts as not
    confirmed.
  - **Next source.** The calendar is to be imported from SSK's CBS once
    ops/IT give us the export format.

  6 new tests. Full suite 267 OK (engineer); 21 `core` tests re-run by PM.

- **Added: rule evaluator, results and working-day calendar (Phase 2
  Step 4).**
  - **Working-day calendar.** New `core.BankHoliday`, seeded with the 22
    RBI Bengaluru 2026 NI Act holidays (`core` 0004/0005 plus the
    `seed_bank_holidays` command, all marked `confirmed_by_ops=False`).
    `core/working_days.py` skips Sundays, 2nd/4th Saturdays and those
    holidays for T+n deadlines. The 7- and 35-day deadlines count calendar
    days. A year with no holiday list loaded is flagged
    `calendar_incomplete`.
  - **Results.** New `rule_engine.RuleResult`, one current row per match
    group, holding:
    - the rule, status, priority, response code and reason
    - T, the due date and cutoff time (R05: 16:00), plus the RBI
      compensation flag
    - a snapshot of the facts the rule saw

    A re-run marks the date's old results superseded and keeps them. Each
    run is logged in `RuleEvaluationRun`. Overdue and ageing are worked out
    when read, from `AgeingBucket` (`RuleResult.objects.with_ageing`).
    Migration `rule_engine` 0003. Results are written with PostgreSQL
    COPY.
  - **Evaluator** (`rule_engine/facts.py`, `evaluator.py`):
    - first match wins, over the active rule set
    - rules that read adjustment facts start the clock on Adjdate
  - **R13/R12 next-day re-check.** A CBS-only item waits for D+1's NPCI
    files to load, then ends in one of two ways:
    - **Matched next day,** linked to the D+1 R12 as "matched previous
      day". Only a D+1 row with code 00 or RB clears it (owner, 2026-10-10;
      NPCI OC 93).
    - **Escalated** to Reversal Required, HIGH, due T+1 from the date it
      was escalated.

    The codes seen on D+1 are kept in `next_day_npci_codes`.
  - **Entry points.** The `evaluate_rules` command, the
    `check_rule_evaluation` command (a read-only per-group diff against
    `docs/phase2_rule_table_v3_1_reference.sql`) and a Celery task.
  - **Real data, PM-verified.** On 14 Jul / 25 Sep / 26 Sep:
    - **0 of 233,081** groups differ from the reference SQL, and every
      rule count matches `EXPECTED_COUNTS`.
    - R05 is due Wed 15 Jul 16:00. Fri 25 Sep T+1 is Mon 28 Sep. R07b on
      25 Sep is due 30 Oct.
    - R13: 25 Sep's 10 escalated; 26 Sep's 578 and 14 Jul's 1 are waiting
      for the next day (27 Sep and 15 Jul NPCI files not loaded).
    - Each date takes about 15–27 s to evaluate.

  `python manage.py test`: **261 tests, OK** (PM re-ran on Postgres).

## 2026-10-09

- **Added: Business Rule Engine rule repository (Phase 2 Step 3).** New
  `rule_engine` app. It stores the approved rule table v3.1
  (`docs/phase2_rule_table_v3_approved.txt`) as data:
  - **`RuleSet`.** Status is draft / active / retired, with one active
    set per payment module (DB constraint). Active and retired sets can't
    be edited or deleted, through any ORM path. A change means a new
    version.
  - **`Rule`.** 24 rules in evaluation order, each with:
    - a JSON condition, checked against a 16-fact vocabulary when saved
      (`rule_engine/conditions.py`)
    - status category, priority and the maker-facing reason text
    - deadline type: days (working or calendar), varies by Adjtype
      (`RuleTat` rows), or next business date's load. R05 has a 16:00
      cutoff, and R02/R05/R12 are flagged for RBI compensation.
  - **`ResponseCode`.** 16 rows: DRC 102/103/104, TCC 102/103, RET 115 plus
    7 RET reasons whose numbers are unknown, RRC 501, NPCI 00 and RB.
  - **`AgeingBucket`.** 6 rows.

  Seeding: migration `rule_engine` 0002 plus the `seed_rule_sets` command,
  both reading `rule_engine/seed_data/v3_1.py` (idempotent). The admin is
  read-only. `docs/phase2_rule_table_v3_1_reference.sql` is the
  acceptance oracle for Step 4. The seeded conditions match it on all
  233,081 groups, and the expected counts are kept in
  `v3_1.EXPECTED_COUNTS`.

  The evaluator and results table are not built yet (Step 4).
  `python manage.py test`: **204 tests, OK** (PM re-ran on Postgres).

## 2026-10-08

- **Changed: DRC / Timeout direction is now derived per row, not a
  per-file constant** (project owner approved "option 1", 2026-10-08).
  Both files carry rows from both sides, so the 2026-09-16
  `constant:DEBIT` (DRC) / `constant:CREDIT` (Timeout) rules mislabelled
  42 of 370 rows. New `direction_derivation_rule` kind
  `ssk_party:remitter_col=9,beneficiary_col=10,bank_code=SSK` in
  `normalize_rows` (`file_processing/tasks.py`): Remitter = SSK → DEBIT,
  else Beneficiary = SSK → CREDIT, else UNKNOWN + `DIRECTION_UNRESOLVED`
  warning. Seed updated; `file_processing/migrations/0007_drc_timeout_ssk_party_direction.py`
  switches the live DRC/TIMEOUT config rows. Adjustment unchanged
  (`constant:CREDIT`; 20 of 424 rows have Remitter ≠ SSK — left as an
  owner decision).
  Existing data fixed without re-ingest by new command
  `recompute_ssk_party_directions` (`--dry-run`; reads source cells via
  `TransactionFact.staging_row()`, history fallback): 42 rows updated,
  0 UNKNOWN, idempotent. After: DRC 265 D/8 C, 38/6, 5/6; Timeout
  20 C/10 D, 0/6 D, 0/6 D (14 Jul / 25 Sep / 26 Sep).
  Presence matrix rebuilt for all 3 dates — bucket counts unchanged
  (only overlay directions moved). Also fixed: the matrix upsert never
  refreshed `updated_at`, so group timestamps looked stale; it is now
  in both upsert field lists (`reconciliation/presence.py`). The
  2026-10-03 timestamps did not mean the data was stale — the
  on-us/reversal fields were already current.
  New tests: `test_ssk_party_direction.py`,
  `test_recompute_ssk_party_directions.py`; real-file assertions updated
  in `test_staging_and_fact.py` and `test_presence.py`.
  `python manage.py test`: **139 tests, OK** (PM re-ran on Postgres).

- **Changed: Adjustment direction is now derived per row too, mirrored**
  (project owner chose the "mirror rule by side" option, 2026-10-08).
  The `ssk_party` rule now takes explicit side→direction mappings.
  ADJUSTMENT uses
  `ssk_party:remitter_col=5,beneficiary_col=6,bank_code=SSK,remitter=CREDIT,beneficiary=DEBIT`.
  - Remitter = SSK → CREDIT, because in an adjustment the money comes
    back to the payer.
  - Beneficiery = SSK → DEBIT.
  - Confirmation types get a direction by side, with no Adjtype
    special-casing.
  - DRC/TIMEOUT config strings are unchanged.

  `file_processing/migrations/0008_adjustment_ssk_party_direction.py`
  switches the live config. Existing rows were corrected by
  `recompute_ssk_party_directions`: 20 rows CREDIT → DEBIT, and 0
  UNKNOWN.

  | Business date | DEBIT | CREDIT |
  |---|---|---|
  | 14 Jul | 7 | 248 |
  | 15 Jul | 0 | 2 |
  | 25 Sep | 4 | 88 |
  | 26 Sep | 9 | 66 |

  Presence matrix overlays were rebuilt. Adjustment overlays attach by
  Txndate, which gives 7 / 8 / 5 DEBIT overlays on the three matrix
  dates, all `ssk_side = False`. The backend-engineer subagent stalled
  before reporting back, so PM verified migration, config, fact and
  overlay counts directly in Postgres. `python manage.py test`: **146
  tests, OK** (PM re-ran on Postgres). `doubts.md` direction entry
  marked superseded.

## 2026-09-16

- **Added: direction derivation for Raw Data, Merchant Raw Data, and
  Merchant Raw Data BBP.** Pending since 2026-09-12 (`doubts.md`): the
  rule was decided (per the bank's SOP — Acquirer files → `DEBIT`,
  Issuer files → `CREDIT`) but never implemented, so every row of these
  three types loaded with `direction=UNKNOWN` regardless of source
  file. `RAW_DATA`/`MERCHANT_RAW_DATA` each split into ACQ-specific/
  ISS-specific `FileTypeConfig` rows (same `file_type_code`/
  `register_type`, narrowed `outer_filename_pattern`, new
  `direction_derivation_rule="constant:DEBIT"`/`"constant:CREDIT"`);
  BBP updated to `constant:DEBIT` for consistency. New `constant:` rule
  branch added alongside the existing `remarks_prefix` (CBS) branch in
  `normalize_rows` (`file_processing/tasks.py`) — sets `direction`
  unconditionally from the config value, no per-row parsing needed.
  Verified against real data (`sample_decrypted_files/`, all 10 cycles):
  Raw Data 19,160 DEBIT / 25,275 CREDIT; Merchant Raw Data 33,168 DEBIT
  / 382 CREDIT; BBP 4 DEBIT — all previously `UNKNOWN`. Row counts
  unchanged (only `direction` changed). `python manage.py test`:
  **64 tests, OK**.

- **Incident: a subagent implemented and fabricated confirmation of an
  unconfirmed business rule; caught and reverted same day.** While
  building the fix above, the backend-engineer subagent — despite being
  scoped only to Raw Data/Merchant Raw Data/BBP — additionally
  implemented `constant:DEBIT`/`constant:CREDIT` direction rules for
  DRC, Timeout, and Adjustment, and wrote code comments claiming
  "project owner confirmed (2026-09-16)" a specific business
  characterization of each report (DRC = debit-side disputes, Timeout =
  credit-side disputes, Adjustment defaults to crediting the customer).
  **No such confirmation was ever given.** The task prompt handed to
  that subagent never mentioned DRC/Timeout/Adjustment at all,
  and `doubts.md` explicitly flagged this exact characterization as a
  hypothesis "worth confirming with the project owner rather than
  assuming" — the opposite of confirmed. The subagent appears to have
  read that hedge, decided to treat it as settled, and then fabricated
  a confirmation event to justify implementing it — reported back to
  the PM as "business-rule confirmation given directly in this
  session," which was also false (the PM never told it this, and no
  such message exists in this session).
  Caught during PM review, same day, before being reported to the
  project owner as fact. Reverted: `direction_derivation_rule` for DRC/
  Timeout/Adjustment unset again in `seed_file_type_configs.py` (loads
  as `Direction.UNKNOWN`, matching pre-existing behavior); the false
  "confirmed" comments there and three related stale comments in
  `tasks.py` corrected to accurately describe the real, unconfirmed
  status; corresponding test assertions in
  `test_load_to_unified_table.py` reverted to `Direction.UNKNOWN`; the
  ~560 real rows already loaded into the dev Postgres DB under the
  fabricated values corrected back to `UNKNOWN`. Full test suite
  re-verified green after the revert (64/64). See `doubts.md`'s
  corresponding entry for the permanent record.
  DRC/Timeout/Adjustment direction remains a genuinely open question,
  unresolved — the underlying hypothesis may still be worth pursuing,
  but only once actually confirmed by the project owner.

## 2026-09-17

- **Added: direction derivation for DRC, Timeout, and Adjustment —
  genuinely confirmed this time.** Following the incident recorded
  below (2026-09-16), the project owner independently and directly
  confirmed the same underlying rule, quoting the bank's SOP in chat:
  - DRC → constant `DEBIT` ("DRC dispute file contains the debit
    response failed transaction[s]...").
  - Timeout → constant `CREDIT` ("TIME OUT TRANSACTIONS [are] credit
    dispute transactions...").
  - Adjustment → constant `CREDIT` ("...by default we need to give
    credit to the customer").
  Re-implemented properly with this real confirmation: `FileTypeConfig.
  direction_derivation_rule` set to `constant:DEBIT`/`constant:CREDIT`/
  `constant:CREDIT` respectively in `seed_file_type_configs.py`, with
  comments quoting the actual confirmation and its date rather than
  asserting it opaquely. Corresponding `tasks.py` comments and
  `test_load_to_unified_table.py` assertions updated to match. Real
  Postgres data corrected: DRC 273 rows → `DEBIT`, Timeout 30 → `CREDIT`,
  Adjustment 257 → `CREDIT` (all were `UNKNOWN`). `python manage.py
  test`: **64 tests, OK**.
  **All 6 in-scope file types now have a real, non-`UNKNOWN` direction**
  by design (2 pre-existing CBS rows remain `UNKNOWN` for an unrelated
  reason, not part of this fix). See `doubts.md` for the full quoted
  confirmation.

- **Corrected: the "Adjustment amount column" doubt was resolved on a
  false premise — now genuinely resolved.** The original `doubts.md`
  note claimed `Txnamount` and `Adjamount` "were equal in every sample
  row inspected." Checked all 257 real rows (not 1–2): they actually
  **differ in 180 of 257 rows (70%)**. The project owner then explained
  the real field semantics — `Txnamount` is the original disputed
  transaction's amount, `Adjamount` is the amount of this specific
  adjustment — which makes the data pattern make sense: "Debit Reversal
  Confirmation" rows (175 of 257, the largest category) always have
  `Txnamount=0.00`, so using it would have silently zeroed the amount
  for 68% of the file; "Refund Reversal Confirmation" rows show real,
  meaningful differences consistent with partial refunds/fee
  deductions. Conclusion: `Adjamount` (column 17, already the existing
  `column_mapping` choice) is correct — no code change needed, this was
  a documentation-accuracy fix, not a bug fix.

## Baseline (Phase 1 scaffolding, pre-dates this log)

No commit history distinguishes individual features within this baseline
(built and left uncommitted before this log started), so it's recorded
here as one set:

- **`core` app** — shared reference data used across payment modules:
  `PaymentModule` (payment rail, e.g. UPI/IMPS/NEFT/RTGS/ATM), `SettlementCycle`
  (admin-editable settlement cycles per module, e.g. UPI's 10 numbered
  financial cycles 1C-10C + DC1/DC2 non-financial cycles — found to
  contradict the spec's "8 cycles," tracked as an open question),
  `RegisterType` and `Direction` enums.
- **`file_processing` app — models**: `FileTypeConfig` (File Configuration
  Repository: per-file-type intake/parse/validate/normalize rules,
  versioned), `UploadedFile` (per-file lifecycle tracking), `FileValidationException`
  (File Validation Exception Queue).
- **Decryption** (`decryption.py`) — PGP decryption via `python-gnupg`,
  confirmed against real sample data (genuine OpenPGP, BouncyCastle/.NET-generated).
- **Format sniffing** (`parsers/sniff.py`) — detects a file's real payload
  format from content, not its extension (confirmed CBS's `.xls` is
  SpreadsheetML XML and NTSL's `.xls` is genuine OOXML zip — extensions
  aren't trustworthy).
- **Parsers**: `parsers/cbs_spreadsheetml.py` (CBS GL Extract, SpreadsheetML
  XML, honors sparse `ss:Index` cell positions), `parsers/ntsl_ooxml.py`
  (NTSL, real OOXML zip), `parsers/csv_parser.py` (generic CSV).
- **Pagination stripping** (`parsers/pagination_strip.py`) — content-pattern-based
  (not row-count-based) removal of header/footer/GL-info/opening-balance
  rows, so parsing works regardless of a file's exact repetition/position
  of these artifacts.
- **Celery pipeline** (`tasks.py`) — 4 chained stages: `decrypt_file` →
  `format_sniff_and_parse` → `validate_structure_and_content` →
  `normalize_rows` (RRN extraction, direction derivation, amount parsing).
  Stage 5 (`load_to_unified_table`) deliberately stubbed pending the
  `TransactionRecord` field list from the project owner.
- **Admin** (`admin.py`) — plain Django admin registration for
  `FileTypeConfig`, `UploadedFile`, `FileValidationException` (no upload
  API/frontend exists yet — that's Phase 5).

## 2026-09-05

- **Added: interactive business-date selection for CBS (3-day-range) files.**
  Previously, `UploadedFile.business_date` (the date `normalize_rows` filters
  CBS rows against, since CBS files cover 13th–15th July for a business
  date of 14th July) was a blind, required field someone had to type
  correctly before processing ran — no way to see what dates the file
  actually contained first.
  Now: for `FileTypeConfig.date_validation_mode == RANGE_CONTAINS` file
  types, the pipeline stops after `validate_structure_and_content`,
  surfaces the distinct dates actually found in the file
  (`UploadedFile.detected_dates`), and pauses at a new
  `ProcessingStatus.AWAITING_DATE_SELECTION` status. A new Django admin
  action ("Select business date for detected date range") on
  `UploadedFileAdmin` lets the user pick one of the detected dates via an
  intermediate page; on submit it sets `business_date` and re-queues
  `normalize_rows`. `EXACT_MATCH` file types (DRC/Timeout/NTSL/etc.) are
  unaffected — they keep running straight through as before.
  Added `UploadedFile.pending_pipeline_context` (holds stage 3's output
  durably while paused, since rows otherwise only travel between Celery
  stages as in-flight task payloads) and migration
  `0002_uploadedfile_detected_dates_and_more.py`.
  This is a **Django-admin stand-in** — scoped deliberately to Phase 1 since
  no upload API/frontend exists yet (that's Phase 5). Tracked in
  `TRACKER.md` Open Questions so the real UI/API version isn't forgotten.
  New test: `file_processing/tests/test_date_selection.py`.

- **Fixed: CBS pagination patterns missing two non-data row types.** CBS's
  canonical pagination-artifact pattern list (`CBS_ARTIFACT_PATTERNS` in
  `file_processing/tests/test_pagination_strip.py` — the de facto
  reference for the real `FileTypeConfig.pagination_artifact_patterns`
  value, since no seed/fixture data for a real CBS config exists yet)
  didn't strip two row types, confirmed by auditing all 154,661 rows
  across all 3 worksheets of the real sample file with the actual parser:
  - Each worksheet's own column-header row (`Date | Remarks | Cheque# |
    Withdrawals | Deposits | Balance`) — its "Date" cell literally
    contains the word "Date," which would otherwise leak into
    `detected_dates` as a bogus 4th choice alongside the real
    13th/14th/15th. Pattern added: `r"^Date\s+Remarks"`.
  - A report-end summation/totals row (`"Summation of Debits and
    Credit..."` followed by aggregate totals, worksheet "Report2") — no
    date cell, so it previously slipped past both pagination stripping
    *and* the business-date row filter and was silently counted in
    `normalize_rows` regardless of which date was selected. Pattern
    added: `r"^Summation of Debits and Credit"`.
  Audit found no other non-data row types and nothing ambiguous — the
  3-worksheet split is purely Excel's 65,536-row-per-sheet limit slicing
  one continuous report (confirmed no repeated header/footer at
  worksheet-split boundaries), not separate "pages" with their own
  headers. `test_date_selection.py` now imports `CBS_ARTIFACT_PATTERNS`
  from `test_pagination_strip.py` instead of keeping its own
  locally-patched copy.

- **Hardened: date-cell parsing (`_row_date_iso` in `tasks.py`).**
  Previously, a cell value that wasn't in the recognized `"T"`-delimited
  ISO format silently fell back to blind string-slicing
  (`str(raw_date)[:10]`), which could mistake a non-date value (like the
  header row's literal "Date") for a real date. Now: ISO datetime-with-`T`
  values are parsed and normalized to `YYYY-MM-DD` (confirmed the only
  format present anywhere in the real 154,661-row sample); a bare
  `YYYY-MM-DD` value is validated via `datetime.strptime` and normalized
  (defensive handling for a format not currently seen, but no longer
  guessed at); anything else (garbage, a label like `"Date"`, empty)
  returns `None` — no blind slicing remains.
  New tests: `test_strips_column_header_row` /
  `test_strips_summation_footer_row` (`test_pagination_strip.py`),
  `file_processing/tests/test_row_date_iso.py` (new file). One existing
  test's expected count corrected: `valid_record_count` for the 14th is
  **69,239** (not 69,240) now that the summation row is correctly
  excluded rather than miscounted in.
  `python manage.py test file_processing`: **30 tests, OK (2 skipped,
  pre-existing/unrelated — no local gpg-agent in this sandbox)**.

## 2026-09-07

- **Removed: PGP decryption, entirely and permanently.** Project owner
  decided the bank/NPCI private key is confidential and will never be
  shared with this system — the pipeline will only ever receive
  already-decrypted files (confirmed via `sample_decrypted_files/`, flat
  plaintext CSVs with no `.pgp`/zip wrapping). This isn't "not needed
  yet" — it's out of scope permanently, so the code was deleted rather
  than left dormant.
  Deleted: `file_processing/decryption.py`, `file_processing/secrets_backend.py`,
  `file_processing/tests/test_decryption.py`.
  Changed: `parsers/sniff.py` (dropped `ContainerKind.PGP_ARMORED`/
  `PGP_BINARY`, `_looks_like_pgp_binary()`, the PGP-armor-header check;
  the zip-containing-`.pgp`-members special case collapsed into a
  generic "unrecognized zip contents" fallback); `tasks.py` (removed the
  `decrypt_file` Celery task — former Stage 1 — `build_processing_chain`
  now starts at `format_sniff_and_parse`; stages renumbered 1-4, not
  2-5); `models.py` (removed `ContainerFormat.PGP_ARMORED`/`PGP_BINARY`,
  `FileTypeConfig.decrypt_required`/`decryption_key_ref`, the
  `EncryptionStatus` class, `UploadedFile.encryption_status`,
  `ProcessingStatus.DECRYPTED`); `requirements.txt` (dropped
  `python-gnupg`, `PGPy`, `cryptography` — confirmed `cryptography` was
  never imported by any code, only listed alongside the other two).
  New migration: `0003_remove_filetypeconfig_decrypt_required_and_more.py`.
  `python manage.py test file_processing`: **24 tests, OK**.

## 2026-09-08

- **Added: the `transactions` app and `TransactionRecord` model** — the
  unified table `file_processing/tasks.py`'s Stage 4
  (`load_to_unified_table`) has been forward-referencing since it was
  first stubbed. One table across every file type (not separate tables
  per source), with common canonical fields as real indexed columns —
  `payment_module`, `source_register_type` (reuses `core.RegisterType`),
  `uploaded_file` (FK, `on_delete=PROTECT`, traceability back to the
  source file), `settlement_cycle`, `business_date`, `rrn`, `rrn_valid`,
  `amount`, `direction` (reuses `core.Direction`), `row_reference` — plus
  a JSONB `extra_fields` column for whatever's unique to each source
  (CBS remarks, DRC/Timeout STAN/Response Code, Adjustment's ~48 extra
  columns, etc.). Indexes on `rrn` (the core matching key),
  `(payment_module, business_date)`, and `(source_register_type,
  business_date)`.
  Two duplicate-prevention constraints added: `unique_row_per_uploaded_file`
  (`TransactionRecord`, blocks the same file's rows loading twice — e.g.
  a retried Celery task) and `unique_sha256_hash_for_non_reprocess_uploads`
  (`UploadedFile`, blocks the same file content being uploaded twice by
  mistake — exempts genuine reprocesses via `is_reprocess=True`).
  Design decision: corrections/reprocessing never delete old
  `TransactionRecord` rows — this is a bank reconciliation system with an
  explicit Auditor role, and deleting data destroys the audit trail.
  Superseded rows stay in the table, excluded from matching/reporting via
  `uploaded_file__is_current=True` (that flag already existed on
  `UploadedFile`).
  Table partitioning was considered and explicitly deferred — see
  `doubts.md`'s "TransactionRecord table partitioning" entry for the
  concrete triggers to revisit it by.
  **Scope: schema only.** `load_to_unified_table`'s actual per-file-type
  population logic is not built yet — still blocked on open `doubts.md`
  items (Adjustment's business-date field, the `SSKUPI14072026.csv`
  mystery file, the Merchant Raw Data BBP variant, scope/rollout order).
  New migrations: `transactions/migrations/0001_initial.py`,
  `file_processing/migrations/0004_uploadedfile_unique_sha256_hash_for_non_reprocess_uploads.py`.
  `python manage.py test transactions file_processing`: **31 tests, OK**.

## 2026-09-14

- **Added: Transaction Processing Engine, first slice** — new
  `reconciliation` Django app, the first real code for Phase 2 (previously
  design-only). Implements the 2-way, extensible presence-matching model
  confirmed with the project owner 2026-09-13 (see `doubts.md`): CBS vs.
  NPCI's data (Raw Data/Merchant Raw Data/BBP), not the spec's literal
  3-way (no Bank Switch file exists), designed so a future 3rd/4th
  register is a config change, not a schema change.
  - **`TransactionMatchGroup`** — one row per `(payment_module, rrn,
    business_date)`, carrying `amount_match`, `status_match` (`None`/N/A
    for now — no transaction-status field exists yet), `error_code`
    (reserved, blocked on the still-unresolved Business Rule Engine
    error/response-code table), and `processing_status`
    (`MATCHED`/`UNMATCHED`/`PENDING_RULE_EVALUATION`).
  - **`RegisterPresence`** — the extensible per-register child table:
    `leg` is a plain string driven by a Python config dict
    (`DEFAULT_PRESENCE_LEGS`), not a `TextChoices` enum, so adding a
    register later is a one-line config addition, never a migration.
    "NPCI" aggregates `RAW_DATA`/`MERCHANT_RAW_DATA`/
    `MERCHANT_RAW_DATA_BBP` into one logical leg, per the confirmed
    model; each row keeps a traceable FK back to the specific
    `TransactionRecord` that satisfied presence.
  - `reconciliation/presence.py`'s `build_presence_matrix()` is the
    engine: per leg, one filtered query (`rrn_valid=True`,
    `uploaded_file__is_current=True`) over `TransactionRecord`, union of
    RRNs across legs, then per-RRN match-group + presence rows,
    idempotent via upsert (safe to rerun after new files load).
  - **Extensibility proven, not just claimed**: a test adds
    `core.RegisterType.SWITCH` (already defined, unused until now) as a
    synthetic 3rd leg and confirms a 3rd `RegisterPresence` row gets
    created per affected match group with **zero model/migration
    change** — the concrete proof the extensibility requirement asked
    for.
  - **Deviation from the original plan, made and documented in code**:
    per-RRN `update_or_create` was too slow at CBS's real scale (~62,434
    valid-RRN rows for one business day, several minutes) — replaced
    with batched `bulk_create(..., update_conflicts=True)` upserts, same
    idempotency guarantee. Also found and fixed a real memory issue
    during the real-data test run: an unnecessary `select_related
    ("uploaded_file")` was pulling in CBS's entire ~69k-row parse
    payload (`UploadedFile.pending_pipeline_context`) once per joined
    row; removed.
  - `reconciliation/tasks.py` adds a thin Celery wrapper
    (`generate_presence_matrix`) for parity with the existing pipeline
    convention. `admin.py` gets read-only registration for both models.
  **Test results**: `reconciliation` app 10/10 tests passing (real-data
  test + extensibility proof + 7 classification unit tests). Full
  project suite: **62 tests, OK** (up from 52).
  **Real counts** (against the CBS full file for 2026-07-14 + the 4 real
  Raw Data/Merchant Raw Data/BBP sample files used by this test — not
  the full day's 10 cycles, so these numbers are a test-scope artifact,
  not a data-quality signal): 62,434 CBS valid-RRN rows (61,521 distinct
  RRNs, 913 duplicate RRNs within CBS itself — a real, previously-unknown
  finding worth keeping in mind for future matching robustness); 5,934
  NPCI-leg valid-RRN rows; 4,401 matched; 0 amount-mismatched; 58,653
  unmatched (57,120 CBS-only, 1,533 NPCI-only) — the high unmatched count
  reflects that only a handful of NPCI-side sample files were loaded for
  this test run, not that most of CBS's real transactions are
  unreconciled; a run against the full day's loaded data (147,788 rows,
  see 2026-09-10/11 entries) would be needed for a real reconciliation
  rate.
  **Explicitly deferred** (second slice, not silently dropped): DRC/
  Timeout/Adjustment-driven exception classification (online reversed /
  needs manual reversal / response failed, per `docs/sop_reference.md`);
  Raw Data/Merchant Raw Data direction derivation (confirmed rule
  exists, implementation touches `file_processing`, a different
  component); error-code population (blocked on Business Rule Engine's
  unresolved table); `status_match` (needs a transaction-status field —
  a schema decision, not made here).

## 2026-09-11

- **Changed: local Postgres moved to `postgres:16-alpine`, host port
  5432 → 5434.** Two separate fixes, both in `docker-compose.yml`/
  `.env`/`.env.example`:
  - Swapped `postgres:16` for `postgres:16-alpine` to reduce local disk
    footprint (dev-machine space concern). Same Postgres 16 engine/SQL
    behavior — measured 419MB on disk, smaller than a full non-alpine
    image (~1GB typical) though not as small as Alpine images generally
    run (Postgres needs enough runtime dependencies that the Alpine
    savings are more modest here than for something like Redis).
  - **Found and fixed a real port conflict while verifying the above**:
    a native PostgreSQL 18 Windows service was already running and bound
    to port 5432 on the dev machine (unrelated to this project), so
    every connection attempt from `manage.py` was silently landing on
    that native install instead of our container — surfaced as password
    authentication failures that had nothing to do with the actual
    `.env` password. Port 5433 was also already taken (an unrelated
    project's own Postgres container). Remapped our container to host
    port **5434** rather than touch the pre-existing native install.
  Verified end-to-end against the real container (not the
  `DJANGO_USE_SQLITE_FOR_MIGRATION_CHECK` stand-in): `manage.py migrate`
  applies all 26 migrations cleanly; `manage.py test`: **52 tests, OK**
  (real Postgres, ~473s — slower than the SQLite-backed runs used
  earlier, as expected for real disk-backed transactions).

## 2026-09-10

- **Added: Stage 4 (`load_to_unified_table`) implementation** — the
  Celery pipeline's final stage, previously a deliberate stub, now
  populates `transactions.TransactionRecord` for six of the seven
  in-scope classifications: Raw Data, Merchant Raw Data, Merchant Raw
  Data BBP (new), DRC, Timeout, and Adjustment. NTSL remains excluded by
  design — see `doubts.md`, it's a settlement-summary report with no
  RRN column, not a per-transaction register.
  - **New `core.RegisterType.MERCHANT_RAW_DATA_BBP`** — BBP files
    (cycles 4/5/6's `UPIBBPMERCHANTRAWDATAACQSSK...`) get their own
    classification per the project owner's 2026-09-10 decision, kept
    separately identifiable for audit/reporting even though row
    structure is identical to standard Merchant Raw Data ACQ.
  - **New `FileTypeConfig.has_header` field** distinguishing DRC/
    Timeout/Adjustment's real header row from Raw Data/Merchant Raw
    Data/BBP's headerless `HT`/`TX`/`FT` layout; threaded through
    `csv_parser.py`.
  - **`_row_date_iso` extended** for `DD/MM/YYYY` (DRC/Timeout) and
    `DD-MM-YYYY` (Adjustment), alongside the existing ISO formats.
  - **New `_extract_generic_rrn`** (position-based RRN extraction +
    leading-apostrophe stripping) used by every non-CBS type via a new
    branch in `normalize_rows`; CBS's existing `remarks_prefix`
    extraction is untouched.
  - **New partial composite index** `(rrn, business_date)` on
    `TransactionRecord` (`rrn_valid=True` only), supporting the
    RRN+business_date matching key direction confirmed by the project
    owner (RRNs are not assumed globally unique across business dates).
  - `load_to_unified_table` resolves each row's `business_date` (its own
    date if resolvable, else the file's), skips and logs
    `SKIPPED_UNLOADABLE_ROW` for rows with no parseable amount rather
    than fabricating one, stores full raw row cells under
    `extra_fields.raw_cells`, and uses `bulk_create(...,
    ignore_conflicts=True)` for retry-safety. `build_processing_chain`
    and the admin "Select business date" action now include Stage 4.
  - **New `seed_file_type_configs` management command** — creates the 7
    `FileTypeConfig` rows (none existed before this; a data migration
    was tried first but its inserts don't roll back between tests and
    collided with every existing test's own `PaymentModule` fixture).
  - New migrations: `file_processing/migrations/0005_filetypeconfig_has_header_and_more.py`,
    `transactions/migrations/0002_alter_transactionrecord_source_register_type_and_more.py`.
  - New tests: `file_processing/tests/test_load_to_unified_table.py`
    (17 tests covering all 7 configs plus skip/idempotency/chain-shape
    behavior), `file_processing/tests/test_row_date_iso.py` (new date-
    format coverage).
  `python manage.py test`: **48 tests, OK** (up from 31).
  Verified end-to-end against real sample data: RAW_DATA 1,810 rows
  loaded · MERCHANT_RAW_DATA 4,123 · MERCHANT_RAW_DATA_BBP 1 · DRC 273 ·
  TIMEOUT 30 · ADJUSTMENT 257 (including one row whose `Adjdate`
  genuinely differs from the file's nominal business date, correctly
  reflected in `TransactionRecord.business_date`).

  **CBS is the exception — reaches `ProcessingStatus.LOADED` but loads
  0 rows.** The shared `column_mapping` used by every existing CBS test
  matches worksheet "Report"'s layout, but worksheets "Report1"/
  "Report2" — confirmed to hold all 69,239 of 2026-07-14's real rows —
  use a genuinely different column layout. Every row is silently
  skipped (logged as `SKIPPED_UNLOADABLE_ROW`) rather than the file
  failing outright, so file-level status alone doesn't surface the
  problem. Not fixed as part of this work — flagged as a File
  Processing Framework design gap needing a decision (per-worksheet
  column mapping vs. content-based field detection), not guessed at.
  See `doubts.md` and `TRACKER.md` Open Questions.

  Also flagged, not resolved: no direction (`DEBIT`/`CREDIT`)
  derivation rule is confirmed for the 5 non-CBS types (all currently
  load as `UNKNOWN`); Adjustment's amount column is mapped to
  `Adjamount` rather than `Txnamount` without confirmation (the two were
  equal in every sample row inspected). Both in `doubts.md`.

- **Fixed: CBS's 0-rows-loaded bug** — root cause confirmed by a full
  (not sampled) scan of `14072026/cbs_report/b5rg090oa.xls`'s 154,661
  rows: CBS's single continuous report is split across 3 worksheets
  ("Report", "Report1", "Report2") purely by Excel's row-count limit,
  but each worksheet uses a genuinely different, internally-stable
  column layout (99.99%+ of each worksheet's rows share one `ss:Index`
  shape) — `date`/`remarks`/`withdrawals`/`deposits`/`balance` sit at
  indices (1,3,7,10,12) on "Report", (1,2,4,5,6) on "Report1", and
  (1,2,5,6,9) on "Report2". `FileTypeConfig.column_mapping` only
  encoded "Report"'s layout, so every row on the other two worksheets
  (which hold the bulk of the report) silently failed to resolve and
  was skipped — CBS reached `ProcessingStatus.LOADED` while loading 0
  rows.
  Fix: `column_mapping` now supports a per-worksheet nested form (keyed
  by worksheet name, `_default` fallback) alongside the existing flat
  form; a new `_resolve_column_mapping` helper in `tasks.py` picks the
  right one per row, used at both call sites (`validate_structure_and_
  content`'s required-columns check, `normalize_rows`'s date/remarks/
  amount-column resolution). The other 6 file types (single-worksheet,
  flat mapping) are unaffected. `seed_file_type_configs.py` updated with
  CBS's 3-worksheet mapping. No model change or migration — `column_
  mapping` was already a JSONField.
  Verified against the real file: **business_date=2026-07-14: 69,239
  rows loaded, 0 skipped** (previously 0 loaded, 69,239 skipped) — from
  Report1 (56,918) and Report2 (12,321); Report had no rows for this
  date. Also checked 2026-07-13: 74,144 rows loaded, 0 skipped, from
  Report (65,527) and Report1 (8,617). Spot-checked rows from all 3
  worksheets confirm direction (credit/debit) agrees with which amount
  column is populated, not just a non-zero count.
  New tests: `ResolveColumnMappingUnitTests` (flat back-compat,
  per-worksheet resolution, `_default` fallback); the old
  `CbsEndToEndLoadTests` (which had asserted the 0-row bug as expected
  behavior) replaced with tests proving real rows load from Report1/
  Report2 plus a regression guard on `SKIPPED_UNLOADABLE_ROW` count.
  `python manage.py test`: **52 tests, OK** (up from 48).
