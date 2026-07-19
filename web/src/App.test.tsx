import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";

const dashboard = {
  jobs: { total: 2, new: 1, applied: 1, archived: 0 },
  contacts: 3,
  follow_ups: 1,
  usage: [],
  runs: [],
};
const jobs = {
  items: [
    {
      id: 1,
      title: "Junior Data Coordinator",
      company: "Example University",
      location: "Vancouver",
      description: "Support secure research data.",
      requisition_id: "JR25237",
      status: "new",
      notes: "",
    },
  ],
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.includes("/dashboard")
        ? dashboard
        : url.includes("/jobs")
          ? jobs
          : url.includes("/contacts")
            ? { items: [] }
            : {
                openrouter_configured: false,
                google_search_configured: false,
                gmail_authorized: false,
              };
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("shows the daily evidence-first dashboard", async () => {
  render(<App />);
  expect(screen.getByText("REACHBOARD")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("2")).toBeInTheDocument());
  expect(screen.getAllByText("Junior Data Coordinator")).toHaveLength(2);
  expect(screen.getByText("Local & private")).toBeInTheDocument();
});

test("opens the job workspace and exposes manual import", async () => {
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Jobs" }));
  expect(await screen.findByRole("heading", { name: "Job workspace" })).toBeInTheDocument();
  expect(screen.getByLabelText("Paste a job description")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Import job" })).toBeInTheDocument();
});

test("settings exposes export and guarded deletion controls", async () => {
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(await screen.findByRole("link", { name: "Export JSON" })).toHaveAttribute(
    "href",
    "/api/export",
  );
  expect(screen.getByRole("button", { name: "Delete local data" })).toBeInTheDocument();
});

test("records a reviewed draft as manually sent without sending it", async () => {
  const detailedJob = {
    ...jobs.items[0],
    contacts: [
      {
        id: 7,
        name: "Ada Lovelace",
        title: "Research Data Manager",
        company: "Example University",
        rank: 1,
        score: 10,
        rationale: "Relevant team lead",
        evidence: [],
        emails: [],
        angles: [],
      },
    ],
    drafts: [
      {
        id: 12,
        contact_id: 7,
        kind: "connection_note",
        subjects: [],
        body: "Hi Ada, I would value your perspective on research data services.",
      },
    ],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.endsWith("/api/jobs/1")
        ? detailedJob
        : url.includes("/outreach-events")
          ? { id: 30, type: "connection_sent" }
          : url.includes("/dashboard")
            ? dashboard
            : url.endsWith("/api/jobs")
              ? jobs
              : url.includes("/contacts")
                ? { items: [] }
                : {
                    openrouter_configured: false,
                    google_search_configured: false,
                    gmail_authorized: false,
                  };
      return new Response(JSON.stringify(payload), {
        status: url.includes("/outreach-events") ? 201 : 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );

  render(<App />);
  await userEvent.click(
    await screen.findByRole("button", { name: /Junior Data Coordinator.*Example University/ }),
  );
  await userEvent.click(await screen.findByRole("button", { name: "Mark sent" }));
  expect(await screen.findByText("Sent locally")).toBeInTheDocument();
});
