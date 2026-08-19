# Reconciliation Automation — Tracker

Status values: Not Started / In Progress / Blocked / Done
Team size: 1–2 engineers

## Timeline (planning estimate — fill in dates once Phase 0 starts)

| Phase | Scope | Estimate | Start | Target end | Status |
|---|---|---|---|---|---|
| 0 | Close spec gaps + lock tech stack | 1.5–2 weeks | | | Not Started |
| 1 | Database Design + File Processing Framework | 3–4 weeks | | | Not Started |
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

## Components

| Component | Phase | Status | Owner | Notes |
|---|---|---|---|---|
| Database Design | 1 | Not Started | | Header only, no content |
| File Processing Framework | 1 | In Progress | | Source file details, normalization, RRN extraction defined; IMPS cycle-wise handling still open |
| Transaction Processing Engine | 2 | In Progress | | Presence matrix, matching keys, 3-way/4-way recon defined |
| Business Rule Engine | 2 | In Progress | | Rule evaluation & repository defined; error/response code table missing |
| Exception Management | 3 | Not Started | | Header only, no content |
| Maker-Checker Workflow | 3 | Not Started | | Header only, no content |
| Settlement Control / NTSL Validation | 4 | In Progress | | Validation and exceptions defined |
| NPCI File Generation | 4 | Not Started | | Header only, no content (functional requirement exists, design doesn't) |
| API | 5 | Not Started | | Header only; tooling still "under consideration" |
| Front End Design | 5 | Not Started | | Header only, no content |
| Reporting Module | 6 | Not Started | | Not yet started |

## Open Questions

| Item | Location in spec | Owner | Due |
|---|---|---|---|
| IMPS cycle-wise file handling | File Processing Framework > Cycle-Wise File Processing | | |
| Transaction register flowchart | Transaction Processing Engine > Transaction Registers | | |
| Presence-matrix example | Transaction Processing Engine > Transaction Presence Verification | | |
| Error/response code repository table | Business Rule Engine > Error Code and Response Code Management | | |
| Main app framework: Django vs alternatives | Tools under consideration | | |
| Database: PostgreSQL vs MySQL | Tools under consideration | | |
| Encrypted file handling approach | Tools under consideration | | |
