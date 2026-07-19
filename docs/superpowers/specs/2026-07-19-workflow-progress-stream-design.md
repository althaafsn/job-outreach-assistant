# Workflow Progress Stream Design

## Goal

Replace the opaque “Analyzing and researching…” wait with accurate,
incremental feedback for the paste-to-people workflow. The default view stays
concise. A user-controlled disclosure shows the underlying operational trace.

The same workflow must stop treating profile/login pages as evidence and
perform bounded person-specific research before generating conversation
ideas.

## Transport

`POST /api/workflow/analyze` will return newline-delimited JSON through
FastAPI's existing `StreamingResponse`. The browser will consume the response
with native `fetch()` and `ReadableStream`; no dependency, WebSocket, polling
table, or background worker is needed.

Each line is one event:

```json
{"type":"progress","stage":"extract","status":"started","message":"Cleaning and verifying job","completed":0,"total":4,"elapsed_ms":12,"detail":"OpenRouter extraction started"}
```

The final line has `type: "complete"` and the existing workflow result. A
terminal `error` event contains a safe user message. Warnings may accompany
either progress or completion.

## Stages and Detail

The four stable stages are:

1. Clean and verify the job.
2. Find relevant people.
3. Research their public work.
4. Prepare evidence-grounded conversation ideas.

Events may include real sub-progress: query count, sanitized Brave query,
result count, retained candidate count, contact name, public source URL,
evidence count, routed model name, validation outcome, angle count, warning,
and elapsed time.

The trace will not contain API keys, authorization data, complete model
prompts, raw model responses, private profile text, or hidden model
chain-of-thought. “Technical details” means observable operations and
metadata, not private reasoning.

No percentage will imply elapsed-time certainty. The UI displays completed
stages as `n of 4`; the current external call remains indeterminate.

## Contact Selection and Research

Contact discovery is a bounded two-pass process:

1. Search for people connected to the hiring organization and role.
2. Ask OpenRouter to select up to three candidates who are supported by the
   supplied search results and relevant to the job.
3. For each retained person, search separately for an official biography,
   publications/research, and public projects, talks, interviews, products, or
   other distinctive professional work.
4. Read at most three meaningful public sources per person.

Candidate selection must cite supplied search-result IDs. Names, titles,
companies, and URLs must remain grounded in those results. LinkedIn may be
stored as a profile link, but never as professional evidence.

Evidence is rejected before storage when it is an authentication, consent,
legal, cookie, search, or generic directory page; contains only a title or
profile metadata; lacks substantive text; or cannot be connected to the
person by name. Rejection reasons appear in technical progress events.

Conversation angles may cite only retained evidence. When no meaningful
evidence exists, the workflow reports that honestly and does not synthesize a
generic title-based question. Existing invalid evidence can be removed safely,
along with angles that cite it.

## Backend

The existing workflow remains the single implementation. Its route becomes a
streaming generator and emits events immediately before and after existing
calls. The existing extraction, contact-research, and angle-generation
functions gain an optional progress callback only where internal events such
as search queries, candidate selection, per-contact sources, evidence
acceptance/rejection, and routed models are otherwise invisible. CLI callers
continue unchanged.

Database writes remain incremental, as they are today. If the client
disconnects or an optional integration fails, already-saved job and research
data is retained. Expected integration failures become warning events and a
partial completion. Unexpected failures emit one safe error event and are
logged server-side.

## Frontend

After submission, the form remains visible but disabled. A progress panel
shows:

- current stage and `n of 4`;
- elapsed time;
- completed-stage checkmarks;
- live counts where known;
- the latest warning or failure.

An accessible `<details>` disclosure labelled “Technical details” contains a
timestamped event log and stays collapsed by default. New status text is
announced through `role="status"`/polite live updates without moving focus.
The final event replaces progress with the existing job, contacts, evidence,
and conversation-angle result.

If streaming is unavailable or the response is malformed, the interface
shows a retryable error rather than pretending the workflow completed.

## Testing

- Backend API test asserts ordered NDJSON events, technical metadata, and the
  unchanged final result.
- Backend failure test asserts a safe terminal event and retained partial
  state.
- Research tests reproduce the LinkedIn login-shell evidence, reject it at the
  shared evidence boundary, verify grounded contact selection, and retain
  person-specific project/publication evidence.
- Angle tests prove no conversation idea is created without retained evidence.
- Frontend test feeds a chunked stream and asserts live stage/count updates,
  collapsed technical details, accessible status, and final result.
- Existing synchronous workflow tests are adapted to read the final stream
  event.
- Full backend, type, frontend, lint, build, secret-scan, live-server, and CI
  checks remain required before completion.
