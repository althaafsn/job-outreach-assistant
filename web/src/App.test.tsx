import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";

const job = {
  id: 1,
  title: "Junior Data Coordinator",
  company: "Example University",
  location: "Vancouver, BC",
  description: "Support secure research data platforms.",
  requisition_id: "JR25237",
  status: "new",
  quality_status: "verified",
  notes: "",
  priority: 85,
  priority_reasons: ["Matches target role", "Vancouver"],
};

const dashboard = {
  jobs: { total: 2, new: 1, applied: 1, archived: 0 },
  contacts: 1,
  follow_ups: 0,
  usage: [],
  runs: [],
  automation: {
    last_run: { status: "completed", kind: "daily", started_at: "2026-07-18T08:05:00Z" },
    next_run: "Weekdays at 08:05",
  },
  next_action: { type: "review_job", job },
  queues: { new_jobs: [job], interested_jobs: [], drafts: [], follow_ups: [] },
};

const settings = {
  openrouter_configured: true,
  brave_search_configured: true,
  gmail_authorized: true,
  target_job_queries: ["junior data engineer"],
  target_location: "Canada",
};

const jobDetail = {
  ...job,
  contacts: [
    {
      id: 7,
      name: "Ada Lovelace",
      title: "Research Data Manager",
      company: job.company,
      profile_url: "https://example.org/ada",
      rank: 1,
      score: 90,
      rationale: "Leads work related to this team.",
      evidence: [
        {
          id: 9,
          title: "Public program",
          source_url: "https://example.org/program",
          excerpt: "Ada launched a public research data training program.",
          kind: "official",
        },
      ],
      emails: [],
      angles: [
        {
          id: 11,
          angle: "Ask about launching the public data program.",
          question: "What did its first users change about your approach?",
          evidence_ids: [9],
        },
      ],
    },
  ],
  drafts: [],
};

const workflowResult = {
  stage: "complete",
  warnings: [],
  job: jobDetail,
};

function stubApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.includes("/workflow/analyze")
        ? workflowResult
        : url.includes("/dashboard")
        ? dashboard
        : url.includes("/outreach")
          ? { items: [] }
          : url.endsWith("/jobs/1")
            ? jobDetail
            : url.includes("/jobs")
              ? { items: [job], total: 1, offset: 0, limit: 25, has_more: false }
              : url.includes("/settings")
                ? settings
                : {};
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

beforeEach(stubApi);

afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
  vi.unstubAllGlobals();
});

test("default page is a focused job-to-people form", async () => {
  render(<App />);
  expect(
    screen.getByRole("heading", { name: "Find people for this job" }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Job description")).toBeInTheDocument();
  expect(screen.getByLabelText("Public job URL (optional)")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Find people to contact" })).toBeDisabled();
});

test("pasted job flows to public evidence and conversation ideas", async () => {
  render(<App />);
  await userEvent.type(
    screen.getByLabelText("Job description"),
    "Data Engineer at Example University with a full public description.",
  );
  await userEvent.click(screen.getByRole("button", { name: "Find people to contact" }));

  expect(await screen.findByRole("heading", { name: "Junior Data Coordinator" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Ada Lovelace" })).toBeInTheDocument();
  expect(screen.getByText("Ada launched a public research data training program.")).toBeInTheDocument();
  expect(
    screen.getByText("What did its first users change about your approach?"),
  ).toBeInTheDocument();
});

test("automation is separate from the default workflow", async () => {
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Automation" }));
  expect(await screen.findByRole("heading", { name: "Automation" })).toBeInTheDocument();
  expect(screen.getByText("Last search completed")).toBeInTheDocument();
  expect(screen.getByText("Next search: Weekdays at 08:05")).toBeInTheDocument();
});

test("Jobs is a searchable database with filters and pagination", async () => {
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Jobs" }));
  expect(await screen.findByRole("heading", { name: "Jobs" })).toBeInTheDocument();
  expect(screen.getByLabelText("Search jobs")).toBeInTheDocument();
  expect(screen.getByLabelText("Job status")).toBeInTheDocument();
  expect(screen.getByLabelText("Location")).toBeInTheDocument();
  expect(screen.getByLabelText("Source")).toBeInTheDocument();
  expect(screen.getByText("1 job")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Import job/ })).toBeInTheDocument();
});

test("opening a job shows the guided workflow and decision actions", async () => {
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Jobs" }));
  await userEvent.click(await screen.findByRole("button", { name: /Junior Data Coordinator/ }));
  expect(await screen.findByRole("heading", { name: "Review this job" })).toBeInTheDocument();
  expect(screen.getByText("1. Review posting")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Interested" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Not for me" })).toBeInTheDocument();
  expect(screen.getByText("Why this job is here")).toBeInTheDocument();
});

test("Outreach separates drafts from sent messages and follow-ups", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.includes("/outreach")
        ? {
            items: [
              {
                id: 12,
                state: "draft",
                channel: "connection_note",
                job,
                contact: { id: 7, name: "Ada Lovelace", title: "Data Lead", company: job.company },
                draft: { id: 12, kind: "connection_note", subjects: [], body: "Hi Ada" },
              },
            ],
          }
        : url.includes("/dashboard")
          ? dashboard
          : url.includes("/jobs")
            ? { items: [job], total: 1, offset: 0, limit: 25, has_more: false }
            : url.includes("/settings")
              ? settings
              : {};
      return new Response(JSON.stringify(payload), { status: 200 });
    }),
  );
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Outreach" }));
  expect(await screen.findByRole("heading", { name: "Outreach" })).toBeInTheDocument();
  expect(screen.getByText("Needs review")).toBeInTheDocument();
  expect(screen.getByText("Hi Ada")).toBeInTheDocument();
});

test("settings keeps integration setup and privacy controls", async () => {
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
  expect(screen.getByText("Automated search")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Export JSON" })).toHaveAttribute("href", "/api/export");
});
