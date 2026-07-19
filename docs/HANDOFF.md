# Continuation handoff

## Delivered objective

The local, single-user v1 described in `IMPLEMENTATION_PLAN.md` is implemented.
Deployment to AWS is explicitly outside this milestone.

## Product boundaries

- Read-only Gmail ingestion and manual imports are allowed.
- Public search and public-page retrieval are allowed within quotas and site
  rules.
- Authenticated LinkedIn scraping and automatic outreach are prohibited.
- Every generated claim must cite stored evidence.
- The user always reviews and manually sends outreach.

## Resume relevance

This project demonstrates data engineering through ingestion, normalization,
deduplication, lineage, idempotent pipelines, quotas, and observability. It
demonstrates AI engineering through evidence-grounded extraction, structured
generation, schema validation, evaluation fixtures, and human review.

## Verified release baseline

- Repository: https://github.com/althaafsn/job-outreach-assistant
- Backend: 58 tests passed; 82% statement coverage.
- Static checks: Ruff formatting/checks and strict mypy passed.
- Frontend: 4 Vitest tests, ESLint, TypeScript, and Vite production build passed.
- Runtime: schema initialization, server startup, SPA load, health endpoint,
  manual job import, dashboard, and job detail were exercised against an
  isolated SQLite database.
- Privacy: local secret-pattern scan found no credentials; runtime data,
  `.env`, OAuth files, databases, and build outputs are ignored. CI runs
  Gitleaks against full Git history.
- GitHub Actions release run:
  https://github.com/althaafsn/job-outreach-assistant/actions/runs/29666410407

## Safe continuation sequence

1. Run `git status --short --branch`.
2. Read the unchecked phase in `IMPLEMENTATION_PLAN.md`.
3. Write one failing test for the next behavior.
4. Run that test and confirm the expected failure.
5. Implement the smallest production change.
6. Run the focused test and the full related suite.
7. Update the plan checkbox and commit the phase checkpoint.

Never inspect or print secret values while debugging. Tests and public examples
must use synthetic companies, people, emails, and job descriptions.
