# Job Outreach Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended when authorized) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a private local application that imports jobs, discovers
relevant public professional contacts, grounds conversation ideas in cited
evidence, drafts short outreach for manual sending, and tracks the workflow.

**Architecture:** A FastAPI application owns an idempotent SQLite pipeline and
serves a React single-page client. Deterministic parsing runs before optional
Brave/Gmail/OpenRouter integrations. All external text is untrusted, AI output
is schema-validated, and no outreach is sent automatically.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, SQLite, Pydantic 2,
httpx, BeautifulSoup, Google Gmail API, Brave Search API, Jina Reader fallback,
OpenRouter free router, React 19, TypeScript, Vite, Vitest, and plain CSS.

## Global constraints

- Repository path:
  `/home/althaaf/JOB_SEARCH/project/PYTHON/JOB_OUTREACH_ASSISTANT`.
- Local single-user v1 binds to `127.0.0.1`; AWS deployment is deferred.
- Keep credentials and data separate from every other project.
- Default OpenRouter model: `openrouter/free`; maximum 25 application requests/day.
- Do not add an application-level Brave daily cap; Brave's own quota and rate limits remain authoritative.
- Gmail permission is read-only: `https://www.googleapis.com/auth/gmail.readonly`.
- No direct or authenticated LinkedIn scraping and no automated sending.
- Store only short public professional evidence excerpts and source URLs.
- Never infer protected or sensitive traits; hobbies require explicit professional disclosure.
- Cap contact recommendations at three per job and deduplicate contacts globally.
- Use UTC timestamps, SQLite WAL, foreign keys, and a busy timeout.
- Use test-first development for every behavior.

---

## Task 1: Repository, configuration, and privacy boundary

**Files:** `.gitignore`, `.env.example`, `pyproject.toml`, `README.md`,
`AGENTS.md`, `docs/*`, `data/README.md`

- [x] Initialize an independent Git repository on `feat/local-mvp`.
- [x] Add Python/Node metadata and documented local commands.
- [x] Ignore secrets, OAuth tokens, data, databases, logs, builds, and Terraform state.
- [x] Run `uv sync --all-groups` with Python 3.12.
- [x] Commit the scaffold after the first green backend check.

## Task 2: Domain model and deterministic core

**Files:** `app/config.py`, `app/db.py`, `app/models.py`, `app/domain.py`,
`tests/test_domain.py`, `tests/test_db.py`, `alembic/*`

**Produces:** normalized jobs and contacts, strong duplicate keys, quota
counters, state transitions, and database initialization.

- [x] Test title/company/location normalization and tracking-parameter removal.
- [x] Test strong job deduplication by source ID, requisition ID/company,
  canonical URL, and normalized tuple plus description hash.
- [x] Test contact deduplication by public email, profile URL, then name/company.
- [x] Test SQLite foreign keys, WAL, busy timeout, and idempotent schema creation.
- [x] Implement the planned tables and indexes.
- [x] Add the initial Alembic migration.

## Task 3: Trust-boundary utilities

**Files:** `app/security.py`, `app/quotas.py`, `tests/test_security.py`,
`tests/test_quotas.py`

**Produces:** `SafeFetcher`, URL validation, email confidence classification,
and daily quota reservation.

- [x] Test rejection of loopback, private, link-local, metadata, non-HTTP,
  redirect-to-private, oversized, and non-text fetches.
- [x] Test public URL acceptance with mocked DNS/HTTP.
- [x] Test confidence classes for public and inferred work emails.
- [x] Test transactional daily quota limits and next-day reset.

## Task 4: Job ingestion and lineage

**Files:** `app/ingest.py`, `app/integrations.py`, `tests/test_ingest.py`,
`tests/fixtures/*`

**Produces:** Gmail MIME parsing, deterministic job extraction, manual
URL/text import, ATS refresh, deduplication, and source lineage.

- [x] Test multipart Gmail alerts, encoded headers, HTML links, and repeated ingestion.
- [x] Test manual text containing a Workday-style job description.
- [x] Test source lineage survives cross-source deduplication.
- [x] Implement read-only OAuth and Gmail query pagination.
- [x] Discard full Gmail bodies after extracted fields and source metadata are persisted.

## Task 5: Search, contact discovery, and evidence

**Files:** `app/research.py`, `app/integrations.py`,
`tests/test_research.py`

**Produces:** six-month search backfill, ranked contact candidates, globally
deduplicated contacts, evidence excerpts, and refresh timestamps.

- [x] Test bounded search queries and pagination within the daily budget.
- [x] Test role-aware contact scoring and the three-contact cap.
- [x] Test evidence sanitization, excerpt limits, provenance, and duplicate removal.
- [x] Test contact reuse across multiple jobs.
- [x] Implement safe public-page retrieval with clear partial/failure states.

## Task 6: Grounded OpenRouter workflows

**Files:** `app/ai.py`, `app/prompts.py`, `evals/fixtures/*`,
`tests/test_ai.py`

**Produces:** validated extraction, cited angle suggestions, message drafts,
model/request records, and offline contract evaluation.

- [x] Define strict Pydantic response models and versioned prompts.
- [x] Test malformed JSON, unknown evidence IDs, unsupported claims, 429 deferral,
  and one repair attempt.
- [x] Test connection notes at 280 characters or fewer, messages at 50–90 words,
  and emails at 90–140 words with two subjects.
- [x] Test that prompts exclude emails, Gmail metadata, private notes, and full pages.
- [x] Add synthetic evaluation packs and `job-outreach eval-ai`.

## Task 7: API and command-line workflow

**Files:** `app/api.py`, `app/cli.py`, `tests/test_api.py`,
`tests/test_pipeline.py`

**Produces:** dashboard/job/contact/draft/settings/run endpoints and commands:
`init-db`, `gmail-auth`, `ingest`, `import-text`, `backfill`,
`research-pending`, `run-daily`, `eval-ai`, `export`, `doctor`, and `serve`.

- [x] Test API validation, pagination, state changes, regeneration, and delete/export.
- [x] Test daily-run file locking, idempotence, interruption recording, and resume.
- [x] Test exhausted quotas defer work rather than losing it.
- [x] Implement structured run records without secrets or full content.

## Task 8: React review workspace

**Files:** `web/package.json`, `web/src/*`, `web/tests/*`

**Produces:** responsive dashboard, jobs, contacts/evidence, angle review,
draft composer, pipeline status, duplicate review, and settings/profile UI.

- [x] Add API types/client and route shell.
- [x] Build dashboard cards for pending work, follow-ups, errors, quotas, and runs.
- [x] Build job list/detail and three-contact research workspace.
- [x] Build cited angle cards with custom-perspective and research-more actions.
- [x] Build draft controls with counts, copy, regenerate, and manual sent tracking.
- [x] Add keyboard focus, labels, contrast, empty/error/loading states.
- [x] Run Vitest, ESLint, and production build.

## Task 9: Automation, verification, and public release

**Files:** `scripts/cron.example`, `.github/workflows/ci.yml`,
`docs/OPERATIONS.md`

- [x] Add a sample cron entry invoking the same `run-daily` command.
- [x] Add CI with synthetic fixtures only.
- [x] Run backend tests, coverage, Ruff, mypy, frontend tests/lint/build.
- [x] Run a secret scan and inspect tracked files for personal/runtime data.
- [x] Exercise import → research → angle → draft → state tracking locally with mocks.
- [x] Commit verified checkpoints, create the public GitHub repository, and push.
- [x] Record the repository URL and exact verification results in the handoff.

## Definition of done

The local application starts with documented commands; the critical workflow
works with mocked integrations and degrades clearly when optional credentials
are absent; generated statements are traceable to public evidence; no secret,
private data, or automatic outreach capability is present; all automated checks
pass; and the public GitHub repository contains only safe source and synthetic
fixtures.
