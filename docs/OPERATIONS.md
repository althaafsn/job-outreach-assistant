# Local operations

## First run

```bash
cp .env.example .env
uv sync --all-groups
cd web && npm install && npm run build && cd ..
uv run job-outreach init-db
uv run job-outreach doctor
uv run job-outreach serve
```

The server binds to `127.0.0.1:8000`, serves the production React build, and
does not expose the application to the LAN.

## Integration setup

### Gmail alerts

1. Create a new Google Cloud project for this application.
2. Enable the Gmail API.
3. Create a Desktop OAuth client and download the JSON.
4. Save it at `./secrets/gmail_credentials.json`.
5. Run `uv run job-outreach gmail-auth`.

The only requested scope is `gmail.readonly`. Full message bodies are parsed in
memory and discarded; the database keeps message ID, subject, received time,
processing status, extracted job fields, and source lineage.

### Brave Search

Create a Brave Search API key, then set `BRAVE_API_KEY`. The application does
not add a daily Brave cap; Brave's account quota, rate limits, and credentials
remain authoritative. Public pages are fetched directly first; Jina Reader is
used only when direct retrieval fails and requires no separate configuration.

### OpenRouter

Set `OPENROUTER_API_KEY`. The default model route is `openrouter/free`, with a
25-request application budget per day. Actual routed model names are stored
with generated records. Model output is schema-validated and rejected if it
cites evidence IDs not supplied in the request.

## Daily automation

Copy `scripts/cron.example` into `crontab -e` and replace `REPO` with the
absolute repository path. The CLI uses a non-blocking file lock, records a
pipeline run, and is safe to invoke again after interruption.

Failures are visible in the dashboard and logs. Missing credentials or
exhausted quotas defer optional work; existing jobs, notes, and outreach state
remain available.

## Backups and deletion

Stop the server, then copy `data/job_outreach.db` to encrypted storage. To
remove all private data, stop the server and delete `data/job_outreach.db`,
`data/profile.md`, `secrets/gmail_token.json`, and generated exports. These
paths are ignored by Git.

## Public release checklist

```bash
uv run python -m pytest
uv run ruff check .
uv run mypy app
cd web && npm test && npm run lint && npm run build && cd ..
git ls-files | rg '(^|/)(\\.env|secrets|data/.+\\.db|token.+\\.json)$' && exit 1 || true
gitleaks git --redact
```

Only synthetic fixtures may be committed.
