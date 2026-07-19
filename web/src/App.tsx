import { FormEvent, useEffect, useMemo, useState } from "react";
import "./styles.css";

type Page = "Dashboard" | "Jobs" | "Contacts" | "Pipeline runs" | "Settings";

type Job = {
  id: number;
  title: string;
  company: string;
  location: string;
  description: string;
  requisition_id?: string;
  url?: string;
  status: string;
  notes: string;
  contacts?: ContactDetail[];
  drafts?: Draft[];
};

type Evidence = {
  id: number;
  title: string;
  source_url: string;
  excerpt: string;
  kind: string;
};

type Angle = {
  id: number;
  angle: string;
  question: string;
  evidence_ids: number[];
};

type Contact = {
  id: number;
  name: string;
  title: string;
  company: string;
  profile_url?: string;
};

type ContactDetail = Contact & {
  rank: number;
  score: number;
  rationale: string;
  evidence: Evidence[];
  emails: { id: number; email: string; confidence: string; source_url?: string }[];
  angles: Angle[];
};

type Draft = {
  id: number;
  contact_id: number;
  angle_id?: number;
  kind: string;
  subjects: string[];
  body: string;
};

type Dashboard = {
  jobs: { total: number; new: number; applied: number; archived: number };
  contacts: number;
  follow_ups: number;
  usage: { day: string; kind: string; used: number }[];
  runs: {
    id: number;
    kind: string;
    status: string;
    started_at: string;
    error?: string;
  }[];
};

type Settings = {
  openrouter_configured: boolean;
  google_search_configured: boolean;
  gmail_authorized: boolean;
  openrouter_model?: string;
  openrouter_daily_limit?: number;
  google_daily_limit?: number;
  target_job_queries?: string[];
  target_location?: string;
};

const emptyDashboard: Dashboard = {
  jobs: { total: 0, new: 0, applied: 0, archived: 0 },
  contacts: 0,
  follow_ups: 0,
  usage: [],
  runs: [],
};

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail ?? `Request failed (${response.status})`);
  }
  return payload as T;
}

function StatusBadge({ status }: { status: string }) {
  return <span className={`status status-${status}`}>{status.replaceAll("_", " ")}</span>;
}

function App() {
  const [page, setPage] = useState<Page>("Dashboard");
  const [dashboard, setDashboard] = useState<Dashboard>(emptyDashboard);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [settings, setSettings] = useState<Settings>({
    openrouter_configured: false,
    google_search_configured: false,
    gmail_authorized: false,
  });
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const refresh = async () => {
    try {
      const [dashboardData, jobsData, contactsData, settingsData] = await Promise.all([
        api<Dashboard>("/dashboard"),
        api<{ items: Job[] }>("/jobs"),
        api<{ items: Contact[] }>("/contacts"),
        api<Settings>("/settings"),
      ]);
      setDashboard(dashboardData);
      setJobs(jobsData.items);
      setContacts(contactsData.items);
      setSettings(settingsData);
      setError("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Could not load data.");
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const openJob = async (id: number) => {
    setBusy("job");
    try {
      setSelectedJob(await api<Job>(`/jobs/${id}`));
      setPage("Jobs");
      setError("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Could not open job.");
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <strong>REACHBOARD</strong>
          <span>PRIVATE WORKSPACE</span>
        </div>
        <nav aria-label="Primary">
          {(["Dashboard", "Jobs", "Contacts", "Pipeline runs", "Settings"] as Page[]).map(
            (item) => (
              <button
                className={page === item ? "nav-item active" : "nav-item"}
                key={item}
                onClick={() => {
                  setPage(item);
                  if (item !== "Jobs") setSelectedJob(null);
                }}
              >
                <span aria-hidden="true">
                  {item === "Dashboard"
                    ? "⌂"
                    : item === "Jobs"
                      ? "▤"
                      : item === "Contacts"
                        ? "◉"
                        : item === "Pipeline runs"
                          ? "↻"
                          : "⚙"}
                </span>
                {item}
              </button>
            ),
          )}
        </nav>
        <div className="privacy-note">
          <span className="privacy-dot" />
          <div>
            <strong>Local & private</strong>
            <small>Nothing is sent without review.</small>
          </div>
        </div>
      </aside>

      <main className="main">
        {error && (
          <div className="alert" role="alert">
            {error}
            <button onClick={() => setError("")} aria-label="Dismiss error">
              ×
            </button>
          </div>
        )}
        {page === "Dashboard" && (
          <DashboardView dashboard={dashboard} jobs={jobs} onOpen={openJob} busy={busy} />
        )}
        {page === "Jobs" &&
          (selectedJob ? (
            <JobDetailView
              job={selectedJob}
              settings={settings}
              onBack={() => setSelectedJob(null)}
              onRefresh={() => openJob(selectedJob.id)}
              onError={setError}
            />
          ) : (
            <JobsView jobs={jobs} onOpen={openJob} onImported={refresh} onError={setError} />
          ))}
        {page === "Contacts" && <ContactsView contacts={contacts} />}
        {page === "Pipeline runs" && <PipelineView dashboard={dashboard} />}
        {page === "Settings" && (
          <SettingsView settings={settings} onDeleted={refresh} onError={setError} />
        )}
      </main>
    </div>
  );
}

function DashboardView({
  dashboard,
  jobs,
  onOpen,
  busy,
}: {
  dashboard: Dashboard;
  jobs: Job[];
  onOpen: (id: number) => void;
  busy: string;
}) {
  const cards = [
    ["Total jobs", dashboard.jobs.total, "blue"],
    ["Need review", dashboard.jobs.new, "amber"],
    ["Contacts found", dashboard.contacts, "violet"],
    ["Follow-ups due", dashboard.follow_ups, "green"],
  ] as const;
  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">DAILY REVIEW</p>
          <h1>Good morning, Althaaf</h1>
          <p>Review today&apos;s jobs, evidence, and next outreach steps.</p>
        </div>
        <span className="local-badge">● Local & private</span>
      </header>
      <section className="metrics" aria-label="Workflow summary">
        {cards.map(([label, value, tone]) => (
          <article className={`metric metric-${tone}`} key={label}>
            <strong>{value}</strong>
            <span>{label}</span>
          </article>
        ))}
      </section>
      <section className="dashboard-grid">
        <div className="panel">
          <div className="panel-title">
            <div>
              <h2>Jobs to review</h2>
              <p>Newest records that still need a decision.</p>
            </div>
            <span>{jobs.length}</span>
          </div>
          <div className="job-stack">
            {jobs.length ? (
              jobs.slice(0, 7).map((job) => (
                <button className="job-row" key={job.id} onClick={() => onOpen(job.id)}>
                  <span>
                    <strong>{job.title}</strong>
                    <small>
                      {job.company} · {job.location || "Location unknown"}
                    </small>
                  </span>
                  <StatusBadge status={job.status} />
                </button>
              ))
            ) : (
              <Empty title="No jobs yet" text="Import a posting or authorize Gmail alerts." />
            )}
          </div>
        </div>
        <div className="panel next-step">
          <div className="panel-title">
            <div>
              <h2>Next best step</h2>
              <p>The workflow stays useful even before every integration is configured.</p>
            </div>
          </div>
          {jobs[0] ? (
            <>
              <span className="kicker">REVIEW THE SOURCE</span>
              <h3>{jobs[0].title}</h3>
              <p className="muted">{jobs[0].company}</p>
              <p className="excerpt">{jobs[0].description.slice(0, 260)}</p>
              <button className="primary" disabled={busy === "job"} onClick={() => onOpen(jobs[0].id)}>
                Open research workspace
              </button>
            </>
          ) : (
            <Empty title="Start with one posting" text="Paste a Workday or careers-page description." />
          )}
        </div>
      </section>
    </>
  );
}

function JobsView({
  jobs,
  onOpen,
  onImported,
  onError,
}: {
  jobs: Job[];
  onOpen: (id: number) => void;
  onImported: () => Promise<void>;
  onError: (message: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [text, setText] = useState("");
  const [company, setCompany] = useState("");
  const [url, setUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const filtered = useMemo(
    () =>
      jobs.filter((job) =>
        `${job.title} ${job.company} ${job.requisition_id ?? ""}`
          .toLowerCase()
          .includes(query.toLowerCase()),
      ),
    [jobs, query],
  );

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      await api("/jobs/import", {
        method: "POST",
        body: JSON.stringify({ text, company, url: url || null }),
      });
      setText("");
      setCompany("");
      setUrl("");
      await onImported();
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : "Import failed.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <header className="page-header compact">
        <div>
          <p className="eyebrow">SOURCE → STRUCTURE</p>
          <h1>Job workspace</h1>
          <p>Import, correct, and decide which postings deserve outreach.</p>
        </div>
      </header>
      <div className="workspace-grid">
        <section className="panel import-panel">
          <h2>Import a posting</h2>
          <p className="muted">Paste the public description. The parser extracts the core fields.</p>
          <form onSubmit={submit}>
            <label>
              Company
              <input value={company} onChange={(event) => setCompany(event.target.value)} />
            </label>
            <label>
              Public URL
              <input type="url" value={url} onChange={(event) => setUrl(event.target.value)} />
            </label>
            <label>
              Paste a job description
              <textarea
                required
                rows={12}
                value={text}
                onChange={(event) => setText(event.target.value)}
                placeholder="Job title, location, requisition ID, summary…"
              />
            </label>
            <button className="primary" disabled={saving || !text.trim()}>
              {saving ? "Importing…" : "Import job"}
            </button>
          </form>
        </section>
        <section className="panel">
          <div className="panel-title">
            <div>
              <h2>All jobs</h2>
              <p>{filtered.length} structured records</p>
            </div>
          </div>
          <label className="search">
            <span className="sr-only">Search jobs</span>
            <input
              placeholder="Search title, company, or ID"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <div className="job-stack scroll">
            {filtered.map((job) => (
              <button className="job-row" key={job.id} onClick={() => onOpen(job.id)}>
                <span>
                  <strong>{job.title}</strong>
                  <small>
                    {job.company} · {job.requisition_id || "No requisition ID"}
                  </small>
                </span>
                <StatusBadge status={job.status} />
              </button>
            ))}
          </div>
        </section>
      </div>
    </>
  );
}

function JobDetailView({
  job,
  settings,
  onBack,
  onRefresh,
  onError,
}: {
  job: Job;
  settings: Settings;
  onBack: () => void;
  onRefresh: () => Promise<void>;
  onError: (message: string) => void;
}) {
  const [busy, setBusy] = useState("");
  const [perspective, setPerspective] = useState("");
  const run = async (kind: "research" | "angles") => {
    setBusy(kind);
    try {
      await api(kind === "research" ? `/jobs/${job.id}/research` : `/jobs/${job.id}/angles/generate`, {
        method: "POST",
        body: kind === "angles" ? JSON.stringify({ perspective }) : undefined,
      });
      await onRefresh();
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : `${kind} failed.`);
    } finally {
      setBusy("");
    }
  };
  const updateStatus = async (status: string) => {
    await api(`/jobs/${job.id}`, { method: "PATCH", body: JSON.stringify({ status }) });
    await onRefresh();
  };

  return (
    <>
      <button className="back" onClick={onBack}>
        ← Back to jobs
      </button>
      <header className="detail-header">
        <div>
          <p className="eyebrow">{job.requisition_id || "NO REQUISITION ID"}</p>
          <h1>{job.title}</h1>
          <p>
            {job.company} · {job.location || "Location unknown"}
          </p>
        </div>
        <div className="actions">
          <select
            aria-label="Application status"
            value={job.status}
            onChange={(event) => void updateStatus(event.target.value)}
          >
            <option value="new">New</option>
            <option value="interested">Interested</option>
            <option value="applied">Applied</option>
            <option value="archived">Archived</option>
          </select>
          {job.url && (
            <a className="secondary button-link" href={job.url} target="_blank" rel="noreferrer">
              View source ↗
            </a>
          )}
        </div>
      </header>
      <section className="detail-grid">
        <div className="panel">
          <div className="panel-title">
            <div>
              <h2>People worth learning from</h2>
              <p>Up to three contacts, reused across related jobs.</p>
            </div>
            <button
              className="secondary"
              disabled={busy === "research" || !settings.google_search_configured}
              onClick={() => void run("research")}
            >
              {busy === "research" ? "Researching…" : job.contacts?.length ? "Research more" : "Find contacts"}
            </button>
          </div>
          {!settings.google_search_configured && (
            <SetupHint text="Add Google Custom Search credentials in .env to discover public profiles." />
          )}
          {job.contacts?.length ? (
            job.contacts.map((contact) => (
              <ContactCard
                key={contact.id}
                contact={contact}
                job={job}
                settings={settings}
                onRefresh={onRefresh}
                onError={onError}
              />
            ))
          ) : (
            <Empty
              title="No contacts researched"
              text="You can configure public search or add one through the API."
            />
          )}
        </div>
        <aside className="panel source-panel">
          <span className="kicker">JOB CONTEXT</span>
          <h2>What the role actually needs</h2>
          <p className="long-copy">{job.description}</p>
          <div className="angle-controls">
            <label>
              Shift the research perspective
              <textarea
                rows={4}
                value={perspective}
                onChange={(event) => setPerspective(event.target.value)}
                placeholder="Example: focus on their transition into research data or a recent public project."
              />
            </label>
            <button
              className="primary"
              disabled={busy === "angles" || !settings.openrouter_configured || !job.contacts?.length}
              onClick={() => void run("angles")}
            >
              {busy === "angles" ? "Finding angles…" : "Find conversation angles"}
            </button>
            {!settings.openrouter_configured && (
              <SetupHint text="Add an OpenRouter key to create evidence-grounded angles and drafts." />
            )}
          </div>
        </aside>
      </section>
    </>
  );
}

function ContactCard({
  contact,
  job,
  settings,
  onRefresh,
  onError,
}: {
  contact: ContactDetail;
  job: Job;
  settings: Settings;
  onRefresh: () => Promise<void>;
  onError: (message: string) => void;
}) {
  const [kind, setKind] = useState("connection_note");
  const [context, setContext] = useState("I recently applied for this role.");
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recordedDraftId, setRecordedDraftId] = useState<number | null>(null);
  const latestDraft = job.drafts?.find((draft) => draft.contact_id === contact.id);
  const generate = async (angle: Angle) => {
    setBusy(true);
    try {
      await api(`/jobs/${job.id}/contacts/${contact.id}/drafts/generate`, {
        method: "POST",
        body: JSON.stringify({ kind, angle_id: angle.id, user_context: context }),
      });
      await onRefresh();
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : "Draft failed.");
    } finally {
      setBusy(false);
    }
  };
  const recordSent = async (draft: Draft) => {
    const eventType =
      draft.kind === "email"
        ? "email_sent"
        : draft.kind === "post_connection"
          ? "message_sent"
          : "connection_sent";
    setRecording(true);
    try {
      await api("/outreach-events", {
        method: "POST",
        body: JSON.stringify({
          job_id: job.id,
          contact_id: contact.id,
          draft_id: draft.id,
          type: eventType,
        }),
      });
      setRecordedDraftId(draft.id);
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : "Could not record outreach.");
    } finally {
      setRecording(false);
    }
  };
  return (
    <article className="contact-card">
      <div className="contact-heading">
        <div className="avatar" aria-hidden="true">
          {contact.name
            .split(" ")
            .slice(0, 2)
            .map((part) => part[0])
            .join("")}
        </div>
        <div>
          <h3>{contact.name}</h3>
          <p>
            {contact.title} · {contact.company}
          </p>
        </div>
        {contact.profile_url && (
          <a href={contact.profile_url} target="_blank" rel="noreferrer" aria-label={`Open ${contact.name}'s public profile`}>
            ↗
          </a>
        )}
      </div>
      {contact.evidence.map((item) => (
        <blockquote key={item.id}>
          <span>PUBLIC EVIDENCE · {item.kind.replaceAll("_", " ")}</span>
          <p>“{item.excerpt}”</p>
          <a href={item.source_url} target="_blank" rel="noreferrer">
            {item.title} ↗
          </a>
        </blockquote>
      ))}
      {contact.emails?.map((item) => (
        <div className="email-row" key={item.id}>
          <span>
            <strong>{item.email}</strong>
            <small>{item.confidence.replaceAll("_", " ")}</small>
          </span>
          {item.source_url && (
            <a href={item.source_url} target="_blank" rel="noreferrer">
              Evidence ↗
            </a>
          )}
        </div>
      ))}
      {contact.angles.map((angle) => (
        <div className="angle-card" key={angle.id}>
          <span className="kicker">CONVERSATION ANGLE</span>
          <p>{angle.angle}</p>
          <strong>{angle.question}</strong>
          <div className="draft-controls">
            <select aria-label="Draft channel" value={kind} onChange={(event) => setKind(event.target.value)}>
              <option value="connection_note">Connection note</option>
              <option value="post_connection">Post-connection message</option>
              <option value="email">Coffee-chat email</option>
            </select>
            <input
              aria-label="Truthful outreach context"
              value={context}
              onChange={(event) => setContext(event.target.value)}
            />
            <button
              className="secondary"
              disabled={!settings.openrouter_configured || busy}
              onClick={() => void generate(angle)}
            >
              {busy ? "Drafting…" : "Draft for review"}
            </button>
          </div>
        </div>
      ))}
      {latestDraft && (
        <div className="draft">
          <div>
            <span className="kicker">LATEST DRAFT · MANUAL SEND ONLY</span>
            <span className="draft-actions">
              <button onClick={() => void navigator.clipboard.writeText(latestDraft.body)}>Copy</button>
              <button
                disabled={recording || recordedDraftId === latestDraft.id}
                onClick={() => void recordSent(latestDraft)}
              >
                {recordedDraftId === latestDraft.id
                  ? "Sent locally"
                  : recording
                    ? "Recording…"
                    : "Mark sent"}
              </button>
            </span>
          </div>
          {latestDraft.subjects.map((subject) => (
            <strong key={subject}>Subject: {subject}</strong>
          ))}
          <p>{latestDraft.body}</p>
          <small>
            {latestDraft.body.length} characters · {latestDraft.body.split(/\s+/).length} words
          </small>
        </div>
      )}
    </article>
  );
}

function ContactsView({ contacts }: { contacts: Contact[] }) {
  return (
    <>
      <header className="page-header compact">
        <div>
          <p className="eyebrow">GLOBAL DEDUPLICATION</p>
          <h1>People</h1>
          <p>One professional record can connect to several relevant jobs.</p>
        </div>
      </header>
      <section className="panel card-grid">
        {contacts.length ? (
          contacts.map((contact) => (
            <article className="person-card" key={contact.id}>
              <div className="avatar">{contact.name.slice(0, 2).toUpperCase()}</div>
              <h2>{contact.name}</h2>
              <p>{contact.title}</p>
              <small>{contact.company}</small>
              {contact.profile_url && (
                <a href={contact.profile_url} target="_blank" rel="noreferrer">
                  Public profile ↗
                </a>
              )}
            </article>
          ))
        ) : (
          <Empty title="No contacts yet" text="Open a job and run public contact research." />
        )}
      </section>
    </>
  );
}

function PipelineView({ dashboard }: { dashboard: Dashboard }) {
  return (
    <>
      <header className="page-header compact">
        <div>
          <p className="eyebrow">IDEMPOTENT DAILY RUN</p>
          <h1>Pipeline runs</h1>
          <p>See exactly what ran, what deferred, and what needs attention.</p>
        </div>
      </header>
      <section className="panel">
        {dashboard.runs.length ? (
          <div className="run-list">
            {dashboard.runs.map((run) => (
              <article key={run.id}>
                <StatusBadge status={run.status} />
                <div>
                  <strong>{run.kind}</strong>
                  <small>{new Date(run.started_at).toLocaleString()}</small>
                </div>
                <p>{run.error || "Completed without a recorded error."}</p>
              </article>
            ))}
          </div>
        ) : (
          <Empty title="No pipeline runs" text="Run `uv run job-outreach run-daily` when ready." />
        )}
      </section>
    </>
  );
}

function SettingsView({
  settings,
  onDeleted,
  onError,
}: {
  settings: Settings;
  onDeleted: () => Promise<void>;
  onError: (message: string) => void;
}) {
  const [deleting, setDeleting] = useState(false);
  const integrations = [
    ["Gmail read-only", settings.gmail_authorized, "uv run job-outreach gmail-auth"],
    ["Google Custom Search", settings.google_search_configured, "GOOGLE_API_KEY + GOOGLE_SEARCH_ENGINE_ID"],
    ["OpenRouter", settings.openrouter_configured, "OPENROUTER_API_KEY"],
  ] as const;
  const deleteData = async () => {
    const confirmation = window.prompt(
      "This permanently deletes every local job, contact, draft, and pipeline record. Type DELETE to continue.",
    );
    if (confirmation !== "DELETE") return;
    setDeleting(true);
    try {
      await api("/data?confirm=DELETE", { method: "DELETE" });
      await onDeleted();
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : "Could not delete local data.");
    } finally {
      setDeleting(false);
    }
  };
  return (
    <>
      <header className="page-header compact">
        <div>
          <p className="eyebrow">SEPARATE CREDENTIALS</p>
          <h1>Settings & privacy</h1>
          <p>Configuration is read from your local .env; secret values are never displayed.</p>
        </div>
      </header>
      <section className="settings-grid">
        {integrations.map(([name, configured, instruction]) => (
          <article className="panel integration" key={name}>
            <div>
              <span className={configured ? "health on" : "health"} />
              <h2>{name}</h2>
            </div>
            <StatusBadge status={configured ? "configured" : "not_configured"} />
            <p>{configured ? "Ready." : `Configure: ${instruction}`}</p>
          </article>
        ))}
        <article className="panel guardrails">
          <h2>Hard boundaries</h2>
          <ul>
            <li>No authenticated LinkedIn scraping</li>
            <li>No automated connection requests, messages, or email</li>
            <li>Public professional evidence only</li>
            <li>AI output must cite stored evidence</li>
            <li>Gmail bodies are discarded after extraction</li>
          </ul>
        </article>
        <article className="panel data-controls">
          <div>
            <h2>Your local data</h2>
            <p>Export a portable JSON backup or permanently clear the private workspace.</p>
          </div>
          <div className="data-actions">
            <a className="secondary button-link" href="/api/export" download="job-outreach-export.json">
              Export JSON
            </a>
            <button className="danger" disabled={deleting} onClick={() => void deleteData()}>
              {deleting ? "Deleting…" : "Delete local data"}
            </button>
          </div>
        </article>
      </section>
    </>
  );
}

function Empty({ title, text }: { title: string; text: string }) {
  return (
    <div className="empty">
      <span aria-hidden="true">◇</span>
      <strong>{title}</strong>
      <p>{text}</p>
    </div>
  );
}

function SetupHint({ text }: { text: string }) {
  return <p className="setup-hint">Setup needed · {text}</p>;
}

export default App;
