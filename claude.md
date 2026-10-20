# claude.md

## Project

Enterprise Payment Reconciliation Platform — replaces manual Excel-based
reconciliation for UPI (initial scope) with a centralized, rule-based
reconciliation system. IMPS, NEFT, RTGS, and ATM are planned follow-on
modules on the same architecture.

Source of truth for requirements: `docs/Reconciliation_Automation.docx`
(or its markdown export). Do not restate or fork requirements elsewhere —
update the tracker's links back to this doc instead.

Your job here is **project management**, not development: tracking
scope, flagging gaps, and keeping status and schedule current. Engineer
agents build; real build/test/run commands are in the "Development"
section at the end of this file.

Team size: 1–2 engineers. Use this when reasoning about what can run in
parallel vs what must be sequential.

## Your role

- Track status of every component below against `TRACKER.md`.
- Track schedule against the phase plan below — flag if a phase is
  running past its estimate, and note it in status updates.
- Surface the open gaps list (below) proactively — don't let a blank
  section sit unflagged.
- Produce status summaries on request: what's done, what's blocked,
  what's still an open question, and which phase the project is
  currently in.
- Ask before making scope or architecture calls. Approving rule logic,
  the maker-checker design, and the tech stack are human decisions.
- Ask before making schedule calls too — you can flag that a phase is
  slipping, but re-baselining the timeline is a human decision.

## Major components (track each independently)

| Component | Spec section | Phase |
|---|---|---|
| Database Design | *stub in spec — no content yet* | Phase 1 |
| File Processing Framework | file intake, validation, normalization, RRN extraction, cycle-wise handling | Phase 1 |
| Transaction Processing Engine | transaction ID, matching, presence verification, Transaction Presence Matrix | Phase 2 |
| Business Rule Engine | rule evaluation, exception classification, rule repository/versioning | Phase 2 |
| Exception Management | *stub in spec — no content yet* | Phase 3 |
| Maker-Checker Workflow | *stub in spec — no content yet* | Phase 3 |
| Settlement Control / NTSL Validation | NTSL validation, settlement reconciliation, settlement exceptions | Phase 4 |
| NPCI File Generation | *stub in spec — no content yet* | Phase 4 |
| API | *stub in spec — no content yet* | Phase 5 |
| Front End Design | *stub in spec — no content yet* | Phase 5 |
| Reporting Module | daily/monthly volume, reconciliation, exception, ageing, settlement, approval, user activity reports | Phase 6 |

## Phases & timeline (planning estimate, 1–2 engineers)

These are rough planning-level estimates, not a committed schedule.
Re-baseline once Phase 0 closes and real velocity is known. Full detail
and dates live in `TRACKER.md` — this is the reference plan.

| Phase | Scope | Estimate | Depends on |
|---|---|---|---|
| 0 | Close spec gaps: resolve 5 stub sections, define IMPS cycle handling (tech stack locked 2026-08-28, frontend framework still open) | 1.5–2 weeks | — |
| 1 | Database Design + File Processing Framework | 3–4 weeks | Phase 0 |
| 2 | Transaction Processing Engine + Business Rule Engine | 5–6 weeks | Phase 1 |
| 3 | Exception Management + Maker-Checker Workflow | 3–4 weeks | Phase 2 |
| 4 | Settlement Control (NTSL) + NPCI File Generation | 3 weeks | Phase 2 (can overlap Phase 3 with 2 engineers) |
| 5 | API + Front End | 4–5 weeks | Phase 3, Phase 4 |
| 6 | Reporting Module | 2–3 weeks | Phase 5 (data model stable) |
| 7 | UAT, hardening, rollout | 2–3 weeks | Phase 6 |

Total: ~26–30 weeks (6–7 months) end to end, sequential-leaning given
team size. Phases 3 and 4 can overlap if 2 engineers are staffed at that
point.

## Open gaps to keep visible

Carry these on the tracker until resolved — don't let them silently
disappear when the spec doc is next edited:

- IMPS cycle-wise file handling — not yet defined (UPI has 8 cycles;
  IMPS equivalent is unspecified).
- Transaction register flowchart for the Transaction Processing Engine —
  not yet drawn.
- Error Code / Response Code repository table — placeholder only.
- Five stub sections with zero content: Exception Management,
  Maker-Checker Workflow, NPCI File Generation, Database Design, Front
  End Design, API.
- Tech stack locked 2026-08-28 (see `TRACKER.md` → Tech Stack table):
  Django/DRF, PostgreSQL, Python (pandas/openpyxl) for file parsing,
  Celery+Redis for async jobs. Frontend framework decided 2026-10-12:
  React.
  Encrypted-file handling was resolved 2026-09-07 by removing it from
  scope entirely — the bank/NPCI private key is confidential and will
  never be shared with this system, so the pipeline only ever receives
  already-decrypted files (see `TRACKER.md` → Tech Stack table).

## Reporting format

When asked for a status update, structure it as:
- **Current phase** — which phase is active, and whether it's on track,
  ahead, or slipping against its estimate
- **Done** — components with no open items
- **In progress** — components with an assigned owner and active work
- **Blocked** — components waiting on a decision or input, with what's
  blocking them
- **Not started** — components with no work begun

Always cross-check against the open gaps list above before calling
anything "done."

## Conventions

- Keep `TRACKER.md` as the single place for status and schedule — don't
  duplicate status inside this file or inside the spec doc itself.
- `CHANGELOG.md` is PM-owned, same as `TRACKER.md`: it's the running log
  of features added and modifications made. Engineer agents (backend/
  frontend) implement and report back what changed; the PM logs the
  entry once the work is confirmed done — engineers should not edit
  `CHANGELOG.md` directly.
- When the spec doc changes, note what changed and which tracker rows
  or phases it affects, rather than silently re-syncing.
- Once real development starts, add a "Development" section here with
  actual build/test/run commands — do not guess these in advance.

## Development

Commands verified 2026-10-15 (Phase 5 Slice 1), updated 2026-10-20
(Slice 2). Windows dev laptop, 11.6 GB RAM (was 7.6 GB): still run the
full Django suite alone — next to a frontend build it took 77 min
instead of ~15. Keep the laptop on mains power and awake during long
e2e runs (a standby mid-run froze the worker and expired the session).

**Services** (PostgreSQL on host port 5434, Redis 6379, Adminer 8083):
`docker compose up -d` from the project root.

**Backend** (project root; `.env` is loaded automatically by
`config/settings.py`, shell variables win):
- `.venv\Scripts\activate`
- `python manage.py migrate`
- `python manage.py runserver` (port 8000; also serves `frontend/dist`)
- `python manage.py test` — full suite (622 tests), takes ~15 min
- Brand-new database: `migrate` alone leaves no rule set or NTSL
  mapping (their seed migrations skip on an empty DB). Then run
  `seed_settlement_cycles`, `seed_file_type_configs`, `seed_rule_sets`,
  `seed_ntsl_category_map`. Tolerance v1 seeds itself on first use.
- Celery worker (needed for uploads and runs):
  `.venv\Scripts\celery.exe -A config worker --pool=solo -l info`

**Frontend** (`frontend/`; set `TEMP`/`TMP` to `D:\tmp` first; npm cache
and prefix are on D:):
- `npm run dev` — Vite on 5173, proxies `/api`, `/admin`, `/static` to
  8000 (needs `DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost:5173` in `.env`)
- `npm run build` — type-check + build into `frontend/dist`
- `npm run lint`, `npm run type-check`, `npm run test` (Vitest)
- `npm run e2e` — Playwright click-through (Edge, headless, 1 worker).
  Needs Django + Vite running and `E2E_MAKER_USER/PASSWORD`,
  `E2E_CHECKER_USER/PASSWORD`. Run it against the database copy, not dev
  data: start Django with `POSTGRES_DB=reconciliation_e2e` (users
  `e2e_maker` / `e2e_checker`). It changes cases, so re-clone the copy
  from `reconciliation` when it runs out of OPEN LOW R01 cases.
- Slice 2 click-through, run on its own:
  `npx playwright test e2e/slice2.spec.ts` with
  `E2E_BASE_URL=http://localhost:8000` (the build served by Django),
  `E2E_OPS_USER`/`E2E_OPS_PASSWORD` (user `suco`, Operations User), and
  Django + Celery started with `POSTGRES_DB=reconciliation_e2e_s2`,
  `FILE_PROCESSING_WORK_DIR=D:/tmp/e2e_s2_work`. It needs a brand-new
  DB each time (the same files are refused as duplicates): drop and
  re-create `reconciliation_e2e_s2`, migrate, seed (above), add the
  user, empty the work folder. Takes ~3 min.
