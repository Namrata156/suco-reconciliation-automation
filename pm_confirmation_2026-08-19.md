# PM Status Confirmation — Reconciliation Automation Project (2026-08-19)

This is not an implementation plan. Per CLAUDE.md, no code exists yet and
we're in Phase 0, so this document is the confirmation exchanged before
owners/dates were assigned in TRACKER.md. Nothing here proposes code or
architecture.

## 1. Role as PM agent

- Track status of each of the 11 major components (Database Design, File
  Processing Framework, Transaction Processing Engine, Business Rule Engine,
  Exception Management, Maker-Checker Workflow, Settlement Control/NTSL,
  NPCI File Generation, API, Front End, Reporting) against TRACKER.md.
- Track schedule against the 8-phase plan (Phase 0–7), flag slippage against
  estimate, don't re-baseline it unilaterally.
- Keep the open-gaps/open-questions list visible every time — never let it
  silently drop when the spec doc changes.
- Produce status summaries in the fixed format: Current phase / Done / In
  progress / Blocked / Not started, cross-checked against open gaps before
  calling anything "done."
- Treat `docs/reconciliation_automation.docx` as the single source of
  requirements truth — never fork or restate requirements into CLAUDE.md,
  TRACKER.md, or elsewhere; only link back to it.
- Ask before scope/architecture decisions (rule logic, maker-checker design,
  tech stack) and before schedule re-baselining. Flag; don't decide.

## 2. Current phase and what's blocking it

**Current phase: Phase 0 — "Close spec gaps + lock tech stack."** TRACKER.md
lists it as *Not Started* (no start/end dates filled in yet).

Phase 0 is explicitly scoped to close 3 things, and all 3 are still open in
the spec doc:

- **5 stub sections with zero design content**: Exception Management,
  Maker-Checker Workflow, NPCI File Generation, Database Design, Front End
  Design — confirmed still literally just section headers in the docx (API
  is also unaddressed, marked "API?" in the table of contents/body). NPCI
  File Generation has one functional-requirement line ("Response/
  acknowledgement files will be generated in NPCI formats") but no design.
- **Tech stack "under consideration"**: Django/DRF, Postgres-or-MySQL,
  Python for parsing, encrypted-file handling approach — the docx's own
  "Tools under consideration" section ends mid-sentence on encrypted file
  handling ("Encrypted file handling –"), confirming it's genuinely
  undecided, not just unrecorded.
- **IMPS cycle-wise file handling**: the spec has a literal bracketed
  placeholder — "[Add imps cycle-wise information and how these cycle-wise
  files will be handled]" — right after describing UPI's 8 settlement
  cycles. Confirmed still open.

Nothing else is blocking Phase 0 from starting — these three items are Phase
0's entire scope, so Phase 0 isn't blocked so much as not yet worked.

Nuance flagged: TRACKER.md's component table shows File Processing
Framework, Transaction Processing Engine, Business Rule Engine, and
Settlement Control as "In Progress" with real design detail already written
in the spec — substantive Phase 1/2/4 spec content exists even though Phase
0 (the gating phase) hasn't formally started/closed. Worth being aware of
when sequencing owner assignments: Phase 0's specific closure items are
narrower than "the whole spec is unfinished."

## 3. Full open-questions list (from TRACKER.md, as of 2026-08-19)

| Item | Location in spec | Owner | Due |
|---|---|---|---|
| IMPS cycle-wise file handling | File Processing Framework > Cycle-Wise File Processing | — | — |
| Transaction register flowchart | Transaction Processing Engine > Transaction Registers | — | — |
| Presence-matrix example | Transaction Processing Engine > Transaction Presence Verification | — | — |
| Error/response code repository table | Business Rule Engine > Error Code and Response Code Management | — | — |
| Main app framework: Django vs alternatives | Tools under consideration | — | — |
| Database: PostgreSQL vs MySQL | Tools under consideration | — | — |
| Encrypted file handling approach | Tools under consideration | — | — |

All 7 rows had no owner and no due date at the time of this confirmation.

Note: this list (7 items) is narrower than CLAUDE.md's "Open gaps" list,
which also folds in the 5 zero-content stub sections as a standalone gap.
Both should stay visible — TRACKER.md's table hadn't been expanded to list
each stub section as its own row yet.

## 4. Phase timeline summary + realism flags

| Phase | Scope | Estimate | Flag? |
|---|---|---|---|
| 0 | Close spec gaps + lock tech stack | 1.5–2 wks | OK |
| 1 | Database Design + File Processing Framework | 3–4 wks | **Flag** |
| 2 | Transaction Processing Engine + Business Rule Engine | 5–6 wks | OK, with a caveat |
| 3 | Exception Management + Maker-Checker Workflow | 3–4 wks | **Flag** |
| 4 | Settlement Control (NTSL) + NPCI File Generation | 3 wks | **Flag** |
| 5 | API + Front End | 4–5 wks | **Flag** |
| 6 | Reporting Module | 2–3 wks | Mild flag |
| 7 | UAT, hardening, rollout | 2–3 wks | Mild flag |

Reasoning, based on spec detail actually read (not guessed):

- **Phase 1 (flag)**: Database Design has zero spec content today, and
  everything downstream (transactions, presence matrix, exceptions,
  approvals, audit, rule versioning, file config repository) depends on
  that schema. File Processing Framework, by contrast, is already
  well-specified (file types per module, RRN extraction, normalization,
  intake, validation, reprocessing). Designing a schema of this scope from
  nothing and building the full file framework in 3–4 weeks with 1–2
  engineers looks tight.
- **Phase 2 (caveat, not a hard flag)**: both components have solid spec
  detail already (presence matrix, matching keys, 3-way/4-way recon; rule
  evaluation, external rule repo, versioning). The one real risk: the
  Error/Response Code repository table is still a placeholder (open
  question above) — if that's not resolved before or early in Phase 2, it
  can stall Business Rule Engine work.
- **Phase 3 (flag)**: both components are complete stubs in System Design
  (only functional-requirement bullets exist, e.g. "assign priorities,
  track ageing" / "maker generates reversal, checker approves").
  Maker-Checker also touches approval security and audit history, which
  tends to need more design rigor than a first estimate assumes. 3–4 weeks
  for two undesigned modules, one of which is an approval/audit workflow,
  looks optimistic.
- **Phase 4 (flag)**: Settlement Control/NTSL is well-specified, but NPCI
  File Generation is a one-line stub with no NPCI file format given yet.
  Generating NPCI-compliant upload files depends on external NPCI format
  specs the team may not fully control the timing of — a schedule risk,
  not just an estimate tightness issue, since it can introduce an external
  dependency/wait.
- **Phase 5 (flag)**: API and Front End are both complete stubs today, and
  the frontend alone needs distinct screens for 5 user roles
  (Administrator, Operations User, Maker, Checker, Auditor) plus exception
  review, approval, and reporting UI. 4–5 weeks for both API design/build
  and a multi-role frontend, starting from zero design, looks tight.
- **Phase 6 (mild flag)**: 7 distinct report types (volume, reconciliation,
  exception, ageing, settlement, approval, user activity) in 2–3 weeks is
  plausible only if the data model is genuinely stable by then — worth
  watching given Phase 1's own risk above.
- **Phase 7 (mild flag)**: 2–3 weeks for UAT/hardening/rollout on a
  platform touching bank settlement money typically involves ops/audit/
  compliance sign-off cycles in a banking environment that can run longer
  than a pure engineering estimate — a general pattern flag, not based on
  anything spec-specific.

These were flags for visibility only — re-baselining is a human call per
CLAUDE.md.
