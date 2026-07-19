# Clean Job Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a paste-first job-to-people workflow backed by strict LLM job
verification, while moving discovery automation and backlog controls to a
separate page.

**Architecture:** Existing `Job` rows serve as the pending and verified queue.
Gmail/Brave discover URLs, the safe page reader and OpenRouter produce grounded
job fields, and only verified jobs flow into the existing public contact
research and evidence-grounded angle generation.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Alembic, SQLite, Pydantic,
httpx, OpenRouter, Brave Search, React, TypeScript, Vitest, and plain CSS.

## Global Constraints

- Preserve application state, notes, contacts, drafts, and source lineage.
- Never scrape authenticated LinkedIn pages or send outreach.
- Treat fetched and pasted text as untrusted data.
- Use `openrouter/free` and a 50-request daily application allowance.
- Add no dependency for parsing, state management, or UI.
- Write and run a failing test before every behavior change.

---

### Task 1: Job quality schema and migration

**Files:**
- Modify: `app/models.py`
- Create: `alembic/versions/20260719_0002_job_quality.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Produces: `Job.quality_status`, extraction metadata, and pending defaults.

- [ ] Add a database test asserting new jobs default to `pending` and application
  status remains independent.
- [ ] Run the focused test and confirm it fails because the fields are absent.
- [ ] Add indexed quality status plus extraction error, model, prompt version,
  attempt count, extracted timestamp, and source hash fields.
- [ ] Add an Alembic migration with non-null defaults for existing rows.
- [ ] Run database and ingestion tests and commit.

### Task 2: Strict LLM extraction contract

**Files:**
- Modify: `app/ai.py`
- Modify: `tests/test_ai.py`

**Interfaces:**
- Produces: `JobExtraction`, `JobPageType`, `build_job_extraction_prompt()`,
  `OpenRouterClient.extract_job()`, and grounded section validation.

- [ ] Add failing tests for individual postings, common free-model aliases,
  collection classification, and ungrounded section rejection.
- [ ] Confirm each test fails for the missing contract or validator.
- [ ] Extend the strict Pydantic schema with page type, metadata, description
  sections, and reason.
- [ ] Build a prompt that requests faithful source sections and treats source
  text as data.
- [ ] Require structured-output-capable routing and reuse the existing single
  repair attempt.
- [ ] Implement whitespace-normalized grounding and minimum-description checks.
- [ ] Run AI tests and commit.

### Task 3: Extraction service and verified upsert

**Files:**
- Modify: `app/pipeline.py`
- Modify: `app/ingest.py`
- Modify: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `extract_job(session, job, ai, read_page=...)` and
  `extract_pending_jobs(...)`.

- [ ] Add a failing test that a pending placeholder becomes verified without
  changing its application status or notes.
- [ ] Add failing cases for collection rejection, grounding failure, fetch
  failure, and quota deferral.
- [ ] Implement source selection: manual description first, otherwise direct
  public page with the existing Jina-aware reader.
- [ ] Apply extracted fields only after validation; record quality outcome and
  model metadata atomically.
- [ ] Process pending rows newest-first and stop cleanly at the daily allowance.
- [ ] Run pipeline and ingestion tests and commit.

### Task 4: Discovery and daily pipeline integration

**Files:**
- Modify: `app/pipeline.py`
- Modify: `app/cli.py`
- Modify: `app/config.py`
- Modify: `.env.example`
- Modify: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `job-outreach extract-pending` and updated `run-daily`.

- [ ] Add failing tests proving Gmail/Brave imports remain pending and snippets
  are never verified descriptions.
- [ ] Add a failing test proving contact research runs only for verified jobs.
- [ ] Tighten Brave discovery queries toward individual job URLs.
- [ ] Insert extraction between discovery and contact research.
- [ ] Remove automatic angle generation from `run-daily`; retain on-demand
  generation.
- [ ] Change the documented/default OpenRouter limit to 50 and cron to daily.
- [ ] Run pipeline/CLI tests and commit.

### Task 5: Quality-aware API and immediate workflow endpoint

**Files:**
- Modify: `app/api.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Changes: `GET /api/jobs` defaults to verified.
- Adds: quality filters, extraction metadata, `POST /api/jobs/{id}/extract`,
  and `POST /api/workflow/analyze`.

- [ ] Add failing API tests for verified-default listing and Needs Review
  filtering.
- [ ] Add a failing end-to-end mocked API test: pasted description creates a
  verified job, finds contacts, and generates cited angles.
- [ ] Expose quality metadata and queue counts.
- [ ] Implement single-job retry/extraction.
- [ ] Implement the immediate workflow endpoint by composing existing
  extraction, contact research, and angle generation functions.
- [ ] Return partial results with a clear stage/error if an optional integration
  is unavailable.
- [ ] Run API tests and commit.

### Task 6: Paste-first React workspace

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/styles.css`
- Modify: `web/src/App.test.tsx`

**Interfaces:**
- Changes: `/` becomes the paste-first workflow.
- Adds: `/automation` for the existing Today/Jobs pipeline workspace.

- [ ] Add failing tests asserting the default route shows the paste form and
  progression from analysis to people and conversation angles.
- [ ] Add a failing navigation test showing automation is separate.
- [ ] Build the single-page form with required job text and optional URL.
- [ ] Show explicit stages: analyzing job, finding people, researching public
  work, preparing conversation ideas.
- [ ] Reuse existing contact evidence/angle presentation and preserve manual
  review.
- [ ] Move existing dashboards, collected jobs, quality filters, and settings
  under Automation without deleting them.
- [ ] Add accessible loading, empty, partial-failure, retry, and keyboard states.
- [ ] Run frontend tests, lint, and build; commit.

### Task 7: Existing database rollout and verification

**Files:**
- Modify: `README.md`
- Modify: `docs/OPERATIONS.md`

- [ ] Back up the ignored SQLite database.
- [ ] Run the Alembic migration and verify all existing rows are pending while
  application state and related-row counts are unchanged.
- [ ] Run a bounded configured extraction smoke test without committing runtime
  data.
- [ ] Verify `/` supports the immediate paste-to-people workflow.
- [ ] Run `uv run python -m pytest`, `uv run ruff check .`, and
  `uv run mypy app`.
- [ ] Run `npm test`, `npm run lint`, and `npm run build` from `web/`.
- [ ] Review the full diff, scan tracked files for secrets/runtime data, commit,
  and push the branch.

