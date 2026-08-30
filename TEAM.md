# TEAM.md — Agent Team Operating Model

Companion to `claude.md` and `TRACKER.md`. Explains how the three agents
on this project work together. Read this once when setting up the team;
individual agent files (`.claude/agents/*.md`) carry the day-to-day
role detail.

## The team

| Agent | File | Role |
|---|---|---|
| PM agent | `claude.md` (main agent) | Orchestrator. Tracks status/schedule, surfaces gaps, produces status reports, delegates to the two engineer subagents, is the only agent that edits `TRACKER.md`. |
| backend-engineer | `.claude/agents/backend-engineer.md` | Owns Database Design, File Processing Framework, Transaction Processing Engine, Business Rule Engine, Settlement Control/NTSL, NPCI File Generation, API. |
| frontend-engineer | `.claude/agents/frontend-engineer.md` | Owns Front End Design, Web Interface, and the UI side of Exception Management, Maker-Checker, and Reporting. |

## Why this structure

Claude Code subagents don't share a conversation with each other — each
runs in its own context. So "working as a team" here means: shared
files as the coordination layer, PM agent as the single writer to avoid
conflicting edits, and everyone reading the same source-of-truth order
before acting.

## Current gate: Phase 0 is partly closed

Tech stack was locked 2026-08-28 (see `TRACKER.md` → Tech Stack table):
Django/DRF, PostgreSQL, Python (pandas/openpyxl), Celery+Redis,
`cryptography` for encrypted-file handling (pending confirmation
against the actual NPCI spec). The frontend framework is the one
exception still open — React vs. Django templates+HTMX.

Spec gaps are **not** closed: Database Design, NPCI File Generation,
API, and Front End Design are still empty stubs, and IMPS cycle-wise
handling, the transaction register flowchart, and the error/response
code table remain unresolved. Both engineer agents are built to
recognize this and still refuse to start real implementation until
the human owner explicitly says Phase 0 is closed — see the "Hard
gate" section in each agent file. Right now, the correct use of the
two engineer agents is:

- Backend-engineer: propose against the locked stack, but still no
  scaffolding/application code for stub components (or non-stub ones,
  without an explicit go-ahead).
- Frontend-engineer: help settle React vs. HTMX, and produce static,
  framework-agnostic mockups if asked.

Neither should be asked to scaffold a real codebase yet.

## Handoff protocol

1. **PM assigns work.** The PM agent decides which subagent a task goes
   to, based on the ownership tables in each agent file. If a task spans
   both, the PM splits it into a backend piece and a frontend piece
   rather than handing the whole thing to one agent.
2. **Subagents work from files, not memory of past chats.** Each
   subagent re-reads `claude.md` → `TRACKER.md` → the relevant spec
   section at the start of every task — there's no persistent memory
   between subagent invocations.
3. **Subagents report back in the PM's fixed format** (Done / In
   progress / Blocked / Not started), same structure the PM already
   uses for status updates, so their output can be folded in directly.
4. **Only the PM agent edits `TRACKER.md`.** Subagents propose status
   changes in their response; they don't write the tracker themselves.
   This avoids two agents editing the same file in the same session.
5. **Cross-agent dependencies get named, not assumed.** If backend
   defines an API contract frontend needs, or frontend needs a field
   list backend hasn't specified yet, that dependency gets written into
   `TRACKER.md`'s Open Questions table with which agent it's waiting on
   — the same way human-owned open questions are tracked today.

## What stays a human decision

Unchanged from `claude.md`, and binding on all three agents, not just
the PM:

- Tech stack (backend framework, database, frontend framework,
  encrypted-file handling).
- Business rule logic and the maker-checker design.
- Re-baselining the schedule.

Any agent — PM, backend, or frontend — that finds itself about to make
one of these calls should stop and flag it instead.

## Setup checklist

- [ ] Place `backend-engineer.md` and `frontend-engineer.md` in
      `.claude/agents/` in the project root.
- [ ] Confirm `claude.md` and `TRACKER.md` are in the project root
      (already the case here).
- [ ] Leave Phase 0 gate in place until the human owner actually
      updates `TRACKER.md`'s tech-stack rows with real decisions —
      don't hand-wave this open by editing the agent files instead.
- [ ] Once Phase 0 closes, come back and add real build/test/run
      commands to both engineer agent files, and add a
      "Development" section to `claude.md` as it already instructs.
