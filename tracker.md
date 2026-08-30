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
| Database Design | 1 | Not Started | backend-engineer | Header only, no content |
| File Processing Framework | 1 | In Progress | backend-engineer | Source file details, normalization, RRN extraction defined; IMPS cycle-wise handling still open |
| Transaction Processing Engine | 2 | In Progress | backend-engineer | Presence matrix, matching keys, 3-way/4-way recon defined |
| Business Rule Engine | 2 | In Progress | backend-engineer | Rule evaluation & repository defined; error/response code table missing |
| Exception Management | 3 | Not Started | backend-engineer (logic) / frontend-engineer (UI) | Header only, no content |
| Maker-Checker Workflow | 3 | Not Started | backend-engineer (logic) / frontend-engineer (UI) | Header only, no content |
| Settlement Control / NTSL Validation | 4 | In Progress | backend-engineer | Validation and exceptions defined |
| NPCI File Generation | 4 | Not Started | backend-engineer | Header only, no content (functional requirement exists, design doesn't) |
| API | 5 | Not Started | backend-engineer | Header only; framework now locked (Django/DRF), no endpoints designed yet |
| Front End Design | 5 | Not Started | frontend-engineer | Header only, no content; framework choice (React vs. Django templates+HTMX) still open |
| Reporting Module | 6 | Not Started | backend-engineer (data/queries) / frontend-engineer (UI) | Not yet started |

## Open Questions

| Item | Location in spec | Owner | Due |
|---|---|---|---|
| IMPS cycle-wise file handling | File Processing Framework > Cycle-Wise File Processing | | |
| Transaction register flowchart | Transaction Processing Engine > Transaction Registers | | |
| Presence-matrix example | Transaction Processing Engine > Transaction Presence Verification | | |
| Error/response code repository table | Business Rule Engine > Error Code and Response Code Management | | |
| Encrypted file handling approach | Tools under consideration | | |

## Tech Stack (approved 2026-08-28)

| Layer | Choice | Notes |
|---|---|---|
| Backend / API | Django + Django REST Framework | Fits rule-engine and maker-checker patterns well |
| Database | PostgreSQL | Stronger transactional integrity for settlement/recon; JSONB for rule definitions; better reporting support |
| File parsing | Python (pandas / openpyxl) | UPI file intake, normalization, RRN extraction, cycle-wise handling |
| Task/queue | Celery + Redis | Async file processing, scheduled cycle-wise ingestion, NPCI file generation jobs |
| Encrypted-file handling | Python `cryptography` | Scheme may be externally mandated by NPCI/bank partners — confirm against file-exchange spec before finalizing implementation |
| Frontend | React (or Django templates + HTMX as lighter alternative) | Depends on richness needed for Exception Management / Maker-Checker UI |
| Auth | Django built-in auth + role-based permissions | Administrator / Maker / Checker / Auditor roles |
