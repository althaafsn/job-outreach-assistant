# Job Outreach Assistant

A private, local-first research workspace for turning job alerts into a
reviewable outreach pipeline:

1. Import jobs from LinkedIn alert emails or pasted job text/URLs.
2. Normalize and deduplicate jobs.
3. Find up to three relevant professional contacts from public sources.
4. Collect cited public evidence and generate grounded conversation angles.
5. Draft short LinkedIn notes and coffee-chat emails for manual sending.
6. Track applications, outreach, follow-ups, quotas, and pipeline failures.

The tool does **not** scrape authenticated LinkedIn pages or send messages.
Humans choose the contact, angle, wording, and whether to send.

## Local setup

Requirements: `uv`, Node.js 20+, and Git.

```bash
cp .env.example .env
mkdir -p data secrets
uv sync --all-groups
cd web && npm install && npm run build && cd ..
uv run job-outreach init-db
uv run job-outreach serve
```

Open <http://127.0.0.1:8000>. During frontend development, run
`npm run dev` from `web/` and open <http://127.0.0.1:5173>.

## How to use the workspace

Start on **Find people**. Paste the complete text of a public job posting and,
optionally, its URL. One action cleans and verifies the posting, finds up to
three relevant people from public sources, and prepares cited conversation
questions. Nothing is sent automatically.

Use **Automation** to monitor recurring discovery and queues, **Jobs** to search
the clean-job library or inspect pending/rejected records, and **Outreach** to
review drafts and follow-ups.

## Optional integrations

- Gmail: create a separate Google Desktop OAuth client with the read-only
  `gmail.readonly` scope, save its downloaded JSON at the configured
  `GMAIL_CREDENTIALS_FILE`, then run `uv run job-outreach gmail-auth`.
- Brave Search: create a Search API key and set `BRAVE_API_KEY`. The application
  reads public pages directly and uses Jina Reader only when direct retrieval fails.
- OpenRouter: set `OPENROUTER_API_KEY`. The default router is
  `openrouter/free`; the application validates model output and records the
  actual model returned.

## Commands

```text
job-outreach init-db
job-outreach gmail-auth
job-outreach ingest
job-outreach import-text FILE
job-outreach backfill --months 6
job-outreach extract-pending
job-outreach research-pending
job-outreach run-daily
job-outreach eval-ai
job-outreach export OUTPUT.json
job-outreach doctor
job-outreach serve
```

## Development

```bash
uv run python -m pytest
uv run ruff check .
uv run mypy app
cd web
npm test
npm run lint
npm run build
```

The complete build contract and continuation checklist are in
[`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).
