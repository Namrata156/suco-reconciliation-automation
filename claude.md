# CLAUDE.md

## Project

Enterprise Payment Reconciliation Platform — replaces manual Excel-based
reconciliation for UPI (initial scope) with a centralized, rule-based
reconciliation system. IMPS, NEFT, RTGS, and ATM are planned follow-on
modules on the same architecture.

Source of truth for requirements: `docs/Reconciliation_Automation.docx`
(or its markdown export). Do not restate or fork requirements elsewhere —
update the tracker's links back to this doc instead.

No application code exists yet. Until it does, your job here is **project
management**, not development: tracking scope, flagging gaps, and keeping
status and schedule current. Once code starts, this file should be
extended with real build/test/lint commands — don't invent them before
then.

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
| 0 | Close spec gaps: resolve 5 stub sections, lock tech stack, define IMPS cycle handling | 1.5–2 weeks | — |
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
- Tech stack is "under consideration," not decided: Django/DRF for the
  app, Postgres or MySQL for the database, Python for file parsing,
  encrypted-file handling unspecified. Don't treat any of these as final.

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
- When the spec doc changes, note what changed and which tracker rows
  or phases it affects, rather than silently re-syncing.
- Once real development starts, add a "Development" section here with
  actual build/test/run commands — do not guess these in advance.
