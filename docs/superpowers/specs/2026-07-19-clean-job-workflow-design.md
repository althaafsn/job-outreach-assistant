# Clean Job Workflow Design

## Goal

Make the default page useful immediately: paste a public job posting, process
it into a clean job record, automatically find relevant public professional
contacts, and generate grounded conversation angles for a coffee-chat request.
Keep automated Gmail/Brave discovery and backlog management on a separate
Automation page.

## Product flow

The default route is a focused four-stage workspace:

1. Paste a job description and, optionally, its public URL.
2. Extract and validate the posting with OpenRouter.
3. Find up to three relevant people from public sources with Brave Search.
4. Generate cited conversation angles about those people's public work.

The user reviews all results. The application never logs into LinkedIn, sends a
connection request, sends a message, or sends email.

## Verified job boundary

Gmail alerts and Brave results discover URLs; their snippets are never trusted
as job records. Jobs have a quality state independent from application status:

- `pending`: waiting for extraction
- `verified`: confirmed individual posting with grounded content
- `needs_review`: fetching or extraction could not be verified
- `rejected`: collection, expired, or irrelevant page

Only verified jobs appear in the normal job feed or enter automatic contact
research.

## LLM extraction

OpenRouter receives cleaned public page text or manually pasted posting text as
untrusted data. Strict structured output classifies the page and returns title,
company, location, requisition ID, posting date, and ordered description
sections. Section bodies must faithfully copy the source rather than summarize
or rewrite it.

Validation requires an individual posting, grounded title/company, grounded
section text, and at least 400 characters and 60 words of description. Invalid
JSON receives one repair attempt. Collection, expired, and irrelevant pages are
rejected; blocked, unreadable, or ungrounded pages need review. Quota exhaustion
leaves work pending.

Direct public retrieval remains first. Jina Reader is used only when direct
retrieval fails or returns a short/login/search shell.

## Automation

Daily automation runs Gmail discovery, Brave discovery, newest-first extraction,
then automatic contact research for verified jobs without contacts. The free
OpenRouter budget is 50 requests per UTC day and is shared with manual AI
actions. Expected exhaustion ends extraction cleanly and leaves the backlog for
the next run. Conversation-angle generation is immediate in the paste-first
workflow and otherwise remains user-triggered.

Existing records become pending and are reprocessed newest-first without
changing application status, notes, contacts, outreach history, URLs, or source
lineage.

## Interface

`/` is the paste-first workflow. `/automation` owns the collected-job database,
quality queues, Gmail/Brave status, backlog counts, and run history. The normal
jobs list defaults to verified records. Needs Review exposes nonverified records,
reasons, and a retry action.

The immediate UI change stays intentionally small: no match-score system and no
large Jobright clone. The clean job description and existing contact/evidence
cards are reused.

## Testing

All external services are mocked in CI. Tests cover manual paste through
contacts and angles, Gmail/Brave discovery, collection rejection, direct/Jina
fallback, grounding failures, quota deferral, newest-first ordering,
verification-only feeds and contact research, migration preservation, and the
new default route.

