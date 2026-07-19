# Workflow Progress and Grounded Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the paste-a-job workflow into a transparent, streamed research process that finds relevant people, retains only meaningful public evidence about their actual work, and proposes source-grounded coffee-chat topics.

**Architecture:** The existing workflow endpoint remains a single `POST`, but returns newline-delimited JSON events while a standard-library worker thread performs the database and external-service work. Contact discovery becomes a bounded two-pass pipeline: Brave finds candidates, OpenRouter selects up to three candidates by search-result ID, then Brave and the existing safe page reader collect up to three usable non-LinkedIn sources per person. The React client incrementally parses the stream, shows four concise stages in an accessible progress surface, and keeps sanitized technical events inside a collapsed disclosure.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/SQLite, Pydantic, Brave Search API, OpenRouter, React 19, TypeScript, Vite, Vitest, Testing Library, native `fetch`/`ReadableStream`, NDJSON.

## Global Constraints

- Do not add runtime dependencies.
- Keep one user action and one `POST /api/workflow/analyze` request; do not add WebSockets, a polling table, or an open-ended agent loop.
- Use at most one OpenRouter contact-selection call, select at most three people, and retain at most three public evidence sources per person.
- LinkedIn URLs may be stored as profile links but must never be stored or cited as research evidence.
- Reject authentication, consent, cookie, legal, search-result, generic-directory, title-only, and person-unrelated pages.
- Do not generate a conversation angle unless at least one retained evidence source supports it.
- The concise progress view shows stage, elapsed time, counts, and actionable warnings; technical detail is collapsed by default.
- Technical events may expose sanitized queries, public source URLs, model names, counts, and acceptance/rejection reasons, but never credentials, authorization headers, complete prompts, raw model responses, private profile data, or hidden chain-of-thought.
- Respect the existing safe-fetch allowlist/SSRF protections and only research publicly accessible pages.
- Preserve current job import, deduplication, scoring, status, Gmail, and automation behavior outside this workflow.

---

### Task 1: Reject Junk Pages at the Evidence Boundary

**Files:**
- Modify: `app/integrations.py`
- Modify: `app/research.py`
- Test: `tests/test_integrations.py`
- Test: `tests/test_research.py`

**Interfaces:**
- Consumes: existing `read_public_page(url: str, *, fetcher: SafeFetcher | None = None) -> str` and `ContactEvidence`.
- Produces: `evidence_rejection_reason(text: str, *, person_name: str, source_url: str) -> str | None`; `save_evidence(...) -> ContactEvidence | None`.

- [ ] **Step 1: Add failing fallback-login-shell tests**

Add a `read_public_page` test whose direct fetch fails and whose Jina fallback returns a LinkedIn `Sign Up | LinkedIn`, `Agree & Join LinkedIn`, privacy-policy shell. Assert that `FetchRejected` is raised. Add `save_evidence` tests asserting that a LinkedIn URL, an authentication shell, a short title-only excerpt, and an excerpt that never names the person return `None`, while a substantive official biography naming the person is saved.

- [ ] **Step 2: Run the focused tests and verify the failures**

Run:

```bash
uv run pytest tests/test_integrations.py tests/test_research.py -q
```

Expected: failures show that the Jina result is returned without shell validation and junk evidence is currently persisted.

- [ ] **Step 3: Implement the minimum shared evidence checks**

In `app/integrations.py`, run the same authentication/consent-shell check after direct fetch and after Jina fallback. In `app/research.py`, add:

```python
def evidence_rejection_reason(
    text: str,
    *,
    person_name: str,
    source_url: str,
) -> str | None:
    ...
```

Normalize whitespace and case, reject LinkedIn hosts, known shell/legal markers, fewer than 20 words, and pages that do not contain the normalized person name or its final name component. Call this function from `save_evidence` before inserting, returning `None` on rejection.

- [ ] **Step 4: Run the focused tests and existing evidence/angle tests**

Run:

```bash
uv run pytest tests/test_integrations.py tests/test_research.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the evidence boundary**

```bash
git add app/integrations.py app/research.py tests/test_integrations.py tests/test_research.py
git commit -m "fix: reject unusable contact evidence"
```

---

### Task 2: Select Contacts from Search Results and Research Their Actual Work

**Files:**
- Modify: `app/ai.py`
- Modify: `app/research.py`
- Modify: `app/pipeline.py`
- Modify: `app/api.py`
- Modify: `app/cli.py`
- Test: `tests/test_ai.py`
- Test: `tests/test_research.py`
- Test: `tests/test_pipeline.py`
- Test: `tests/test_api.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `SearchResult`, `JobPosting`, `ContactCandidate`, `OpenRouterClient`, `evidence_rejection_reason`.
- Produces:
  - `ContactChoice(result_id: int, name: str, title: str, company: str, rationale: str)`.
  - `ContactSelection(contacts: list[ContactChoice])`.
  - `OpenRouterClient.select_contacts(prompt: str, *, allowed_result_ids: set[int]) -> ContactSelection`.
  - `person_research_queries(name: str, company: str) -> list[str]`.
  - `research_job(..., ai: OpenRouterClient, progress: Callable[[dict[str, Any]], None] | None = None) -> int`.

- [ ] **Step 1: Add failing grounded-selection tests**

Test that the contact-selection schema rejects more than three contacts and unknown result IDs. Test that `select_contacts` returns only choices whose IDs occur in `allowed_result_ids`. Test that the prompt numbers each Brave result and includes the job title, company, department, and requested target roles without asking the model to invent an email address.

- [ ] **Step 2: Run AI tests and verify the failures**

Run:

```bash
uv run pytest tests/test_ai.py -q
```

Expected: failures identify missing contact-selection types and client method.

- [ ] **Step 3: Implement one grounded OpenRouter selection call**

Add strict Pydantic response models with `contacts` capped at three. Use the existing JSON-schema OpenRouter request path. Validate every returned `result_id` against `allowed_result_ids`; derive the stored profile URL from the corresponding Brave result rather than from model output. Keep the model response responsible for selecting and extracting names/titles, not inventing sources.

- [ ] **Step 4: Add failing bounded deep-research tests**

Create fake Brave, page-reader, and OpenRouter clients. Assert that `research_job`:

- runs initial company/role discovery queries;
- sends deduplicated, numbered results to `select_contacts`;
- stores no more than three selected contacts;
- runs person-specific official-bio, publication/project, and talk/interview queries;
- never fetches or stores a LinkedIn page as evidence;
- retains no more than three usable sources per person;
- emits sanitized progress dictionaries containing query, result count, person, URL, and evidence acceptance/rejection reason;
- produces zero evidence for login shells and therefore no grounded angles from those pages.

- [ ] **Step 5: Run pipeline tests and verify the failures**

Run:

```bash
uv run pytest tests/test_pipeline.py tests/test_research.py -q
```

Expected: failures show the current single-pass profile-page research and missing progress callback.

- [ ] **Step 6: Implement the bounded two-pass pipeline**

Collect and deduplicate initial Brave results, call `select_contacts` once, save selected contacts, then run exactly three person-specific queries per contact. Skip LinkedIn before fetching. Combine result title/snippet with readable page text, apply `evidence_rejection_reason`, and save the first three accepted sources. Emit safe structured progress events through:

```python
ProgressCallback = Callable[[dict[str, Any]], None]
```

Update API and CLI callers to construct/pass the existing OpenRouter client. Keep non-workflow commands synchronous and preserve their return values.

- [ ] **Step 7: Run all affected backend tests**

Run:

```bash
uv run pytest tests/test_ai.py tests/test_research.py tests/test_pipeline.py tests/test_api.py tests/test_cli.py -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit grounded contact research**

```bash
git add app/ai.py app/research.py app/pipeline.py app/api.py app/cli.py tests/test_ai.py tests/test_research.py tests/test_pipeline.py tests/test_api.py tests/test_cli.py
git commit -m "feat: ground contact research in public sources"
```

---

### Task 3: Stream Honest Workflow Progress from the API

**Files:**
- Modify: `app/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: the progress callback from Task 2 and existing workflow result serializers.
- Produces: `POST /api/workflow/analyze` with `Content-Type: application/x-ndjson`; event shapes `progress`, `warning`, `complete`, and `error`.

- [ ] **Step 1: Add failing NDJSON endpoint tests**

Post a valid job and assert newline-delimited events arrive in order:

```json
{"type":"progress","stage":1,"total_stages":4,"message":"Cleaning and verifying the job posting…"}
{"type":"progress","stage":2,"total_stages":4,"message":"Finding relevant people…"}
{"type":"progress","stage":3,"total_stages":4,"message":"Researching public work…"}
{"type":"progress","stage":4,"total_stages":4,"message":"Preparing grounded conversation ideas…"}
{"type":"complete","result":{...}}
```

Assert `elapsed_ms` is non-negative, detailed research events are forwarded, the final result matches the previous JSON payload, and an injected exception yields a safe `error` event without a traceback, secret, prompt, or raw model response.

- [ ] **Step 2: Run endpoint tests and verify the failures**

Run:

```bash
uv run pytest tests/test_api.py -q
```

Expected: the endpoint currently returns one JSON document rather than an NDJSON stream.

- [ ] **Step 3: Implement the queue-backed streaming response**

Use only `queue.Queue`, `threading.Thread`, `time.monotonic`, and FastAPI `StreamingResponse`. The worker creates its own session from the app’s existing session factory, performs the current import/research/angle workflow, and pushes sanitized dictionaries to the queue. The response iterator serializes one dictionary per line and stops after `complete` or `error`. Use a daemon worker so disconnects do not block server shutdown; save normal workflow progress even if the browser disconnects.

- [ ] **Step 4: Run API and backend regression tests**

Run:

```bash
uv run pytest tests/test_api.py -q
uv run pytest -q
```

Expected: API tests and the complete backend suite pass.

- [ ] **Step 5: Commit streaming API progress**

```bash
git add app/api.py tests/test_api.py
git commit -m "feat: stream workflow research progress"
```

---

### Task 4: Show Concise Progress and Expandable Technical Events

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/styles.css`
- Test: `web/src/App.test.tsx`

**Interfaces:**
- Consumes: Task 3 NDJSON event stream.
- Produces:
  - `readNdjson(response: Response, onEvent: (event: WorkflowEvent) => void) -> Promise<void>`.
  - A determinate four-stage progress bar, polite live status, elapsed time/count summary, warnings, and collapsed `<details>` technical log.

- [ ] **Step 1: Add failing incremental-stream UI tests**

Use a controllable `ReadableStream` response. After clicking **Find people to contact**, enqueue only the first two events and assert that the page already shows stage 2 of 4, the current message, elapsed time, and a progress bar before the final result exists. Assert **Technical details** is collapsed by default and, when opened, contains sanitized queries, model name, source decisions, and timestamps. Enqueue the final event and assert the contact result replaces the progress state.

- [ ] **Step 2: Run the frontend test and verify the failures**

Run:

```bash
cd web
npm test -- --run src/App.test.tsx
```

Expected: the existing client waits for `response.json()` and only shows “Analyzing and researching…”.

- [ ] **Step 3: Implement native incremental NDJSON parsing**

Read `response.body` with `getReader()` and `TextDecoder`, retaining incomplete line fragments between chunks. For each complete line, parse and dispatch a typed `WorkflowEvent`. Treat a missing body, malformed final event, or server `error` event as a user-visible failure.

- [ ] **Step 4: Implement the accessible progress surface**

Render:

- `<progress max={4} value={currentStage}>`;
- a concise `role="status" aria-live="polite"` current-stage sentence;
- `Stage N of 4`, elapsed seconds, people found, and sources retained;
- warning text that does not interrupt the stream;
- `<details><summary>Technical details</summary>` with chronological safe event rows.

Keep the existing form visible, disable duplicate submissions while active, and do not expose raw prompts or model output.

- [ ] **Step 5: Run frontend tests, typecheck, and build**

Run:

```bash
cd web
npm test -- --run
npm run lint
npm run build
```

Expected: all frontend tests pass, lint exits zero, and Vite produces `dist/`.

- [ ] **Step 6: Commit the progress UI**

```bash
git add web/src/App.tsx web/src/styles.css web/src/App.test.tsx
git commit -m "feat: show live workflow research progress"
```

---

### Task 5: Remove Existing Junk Evidence and Verify the Real Workflow

**Files:**
- Modify: `README.md`
- Runtime data cleanup: configured local SQLite database (no new migration or permanent cleanup command)

**Interfaces:**
- Consumes: `evidence_rejection_reason` from Task 1 and existing SQLAlchemy models.
- Produces: a clean current database with invalid evidence and unsupported angles removed; operator documentation for the new workflow.

- [ ] **Step 1: Update the operator documentation**

Document the four live stages, what **Technical details** contains, the three-person/three-source limits, that LinkedIn is a profile link rather than evidence, and that only public grounded sources are used for conversation ideas.

- [ ] **Step 2: Run a one-off shared-rule cleanup**

Run a short `uv run python` script that loads every existing `ContactEvidence`, calls `evidence_rejection_reason`, deletes rejected `AngleEvidence` links, deletes research angles left with zero evidence links, deletes rejected evidence, and commits. Print counts for rejected evidence, deleted links, and deleted unsupported angles. Do not delete contacts merely because their prior evidence was invalid.

- [ ] **Step 3: Run complete static and automated verification**

Run:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy app
cd web
npm test -- --run
npm run lint
npm run build
```

Expected: every command exits zero.

- [ ] **Step 4: Restart and smoke-test the configured local product**

Restart the existing local service using the repository’s documented command. Confirm `/api/health` succeeds. Submit one real job through `/api/workflow/analyze`, observe multiple NDJSON progress lines before `complete`, and inspect the resulting contacts: no evidence may cite LinkedIn/auth shells, and every shown conversation idea must cite at least one accepted public source.

- [ ] **Step 5: Commit documentation and final adjustments**

```bash
git add README.md
git commit -m "docs: explain grounded workflow research"
```

- [ ] **Step 6: Integrate, push, and verify CI**

Merge the isolated implementation branch into `main`, push `main`, and inspect the GitHub Actions run. If any CI job fails, reproduce it locally, fix it test-first, push the correction, and re-check until backend, frontend, and secret-scanning jobs all pass.

