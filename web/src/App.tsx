import { Dispatch, FormEvent, SetStateAction, useCallback, useEffect, useMemo, useState } from "react";
import "./styles.css";

type Page = "Start" | "Automation" | "Jobs" | "Outreach" | "Settings";
type JobStatus = "new" | "interested" | "applied" | "archived";

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
  posted_at?: string;
  quality_status?: string;
  extraction_error?: string;
  priority?: number;
  priority_reasons?: string[];
  contacts?: ContactDetail[];
  drafts?: Draft[];
};

type Contact = { id: number; name: string; title: string; company: string; profile_url?: string };
type Evidence = { id: number; title: string; source_url: string; excerpt: string; kind: string };
type Angle = { id: number; angle: string; question: string; evidence_ids: number[] };
type ContactDetail = Contact & {
  rank: number;
  score: number;
  rationale: string;
  evidence: Evidence[];
  emails: { id: number; email: string; confidence: string; source_url?: string }[];
  angles: Angle[];
};
type Draft = { id: number; contact_id: number; angle_id?: number; kind: string; subjects: string[]; body: string };
type OutreachItem = {
  id: number;
  state: string;
  channel: string;
  created_at?: string;
  sent_at?: string;
  follow_up_at?: string;
  draft: Draft;
  job: Job;
  contact: Contact;
};
type Dashboard = {
  jobs: { total: number; new: number; applied: number; archived: number };
  quality?: Record<string, number>;
  contacts: number;
  follow_ups: number;
  runs: { id: number; kind: string; status: string; started_at: string; error?: string }[];
  automation?: { last_run?: { status: string; started_at: string; error?: string } | null; next_run: string };
  next_action?: { type: string; job?: Job; draft?: Draft; contact?: Contact; state?: string };
  queues?: { new_jobs: Job[]; interested_jobs: Job[]; drafts: OutreachItem[]; follow_ups: OutreachItem[] };
};
type Settings = {
  openrouter_configured: boolean;
  brave_search_configured: boolean;
  gmail_authorized: boolean;
  openrouter_model?: string;
  target_job_queries?: string[];
  target_location?: string;
};
type JobResponse = { items: Job[]; total: number; offset: number; limit: number; has_more: boolean; facets?: { source?: Record<string, number> } };
type WorkflowResult = { stage: string; warnings: string[]; job: Job };
type WorkflowEvent =
  | { type: "stage"; stage: number; total_stages: number; message: string; elapsed_ms: number }
  | { type: "detail"; stage: number; message: string; detail: Record<string, unknown>; elapsed_ms: number }
  | { type: "warning"; message: string; elapsed_ms: number }
  | { type: "complete"; result: WorkflowResult; elapsed_ms: number }
  | { type: "error"; message: string; elapsed_ms: number };
type WorkflowLogEvent = Extract<WorkflowEvent, { type: "stage" | "detail" | "warning" }>;
type JobFilters = { query: string; status: string; quality: string; location: string; posted: string; source: string; sort: string; offset: number };

const emptyDashboard: Dashboard = {
  jobs: { total: 0, new: 0, applied: 0, archived: 0 },
  contacts: 0,
  follow_ups: 0,
  runs: [],
  automation: { last_run: null, next_run: "Not scheduled" },
  queues: { new_jobs: [], interested_jobs: [], drafts: [], follow_ups: [] },
};
const emptyJobs: JobResponse = { items: [], total: 0, offset: 0, limit: 25, has_more: false };

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail ?? `Request failed (${response.status})`);
  return payload as T;
}

async function readNdjson(
  response: Response,
  onEvent: (event: WorkflowEvent) => void,
): Promise<void> {
  if (!response.body) throw new Error("The workflow response could not be streamed.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const parseLines = (complete: boolean) => {
    const lines = buffer.split("\n");
    buffer = complete ? "" : (lines.pop() ?? "");
    for (const line of lines) {
      if (line.trim()) onEvent(JSON.parse(line) as WorkflowEvent);
    }
    if (complete && buffer.trim()) onEvent(JSON.parse(buffer) as WorkflowEvent);
  };
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    parseLines(done);
    if (done) break;
  }
}

function statusLabel(status: string) {
  return { new: "Needs decision", interested: "Interested", applied: "Applied", archived: "Not for me" }[status] ?? status;
}

function StatusBadge({ status }: { status: string }) {
  return <span className={`status status-${status}`}>{statusLabel(status)}</span>;
}

function formatDate(value?: string) {
  if (!value) return "Date unknown";
  return new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function routeFromLocation(): { page: Page; jobId?: number } {
  const parts = window.location.pathname.split("/").filter(Boolean);
  if (parts[0] === "jobs" && parts[1] && Number.isFinite(Number(parts[1]))) return { page: "Jobs", jobId: Number(parts[1]) };
  if (parts[0] === "jobs") return { page: "Jobs" };
  if (parts[0] === "automation" || parts[0] === "today") return { page: "Automation" };
  if (parts[0] === "outreach") return { page: "Outreach" };
  if (parts[0] === "settings") return { page: "Settings" };
  return { page: "Start" };
}

function App() {
  const initialRoute = routeFromLocation();
  const initialJobId = initialRoute.jobId;
  const [page, setPage] = useState<Page>(initialRoute.page);
  const [dashboard, setDashboard] = useState<Dashboard>(emptyDashboard);
  const [jobsData, setJobsData] = useState<JobResponse>(emptyJobs);
  const [outreach, setOutreach] = useState<OutreachItem[]>([]);
  const [settings, setSettings] = useState<Settings>({ openrouter_configured: false, brave_search_configured: false, gmail_authorized: false });
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [filters, setFilters] = useState<JobFilters>({ query: "", status: "", quality: "verified", location: "", posted: "", source: "", sort: "recommended", offset: 0 });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [importOpen, setImportOpen] = useState(false);

  const loadJobs = useCallback(async () => {
    const params = new URLSearchParams({ limit: "25", offset: String(filters.offset), sort: filters.sort });
    if (filters.query) params.set("q", filters.query);
    if (filters.status) params.set("status_filter", filters.status);
    if (filters.location) params.set("location_group", filters.location);
    if (filters.posted) params.set("posted_within", filters.posted);
    if (filters.source) params.set("source", filters.source);
    params.set("quality_filter", filters.quality);
    setJobsData(await api<JobResponse>(`/jobs?${params}`));
  }, [filters]);
  const loadAll = async () => {
    try {
      const [dashboardData, outreachData, settingsData] = await Promise.all([
        api<Dashboard>("/dashboard"),
        api<{ items: OutreachItem[] }>("/outreach"),
        api<Settings>("/settings"),
      ]);
      setDashboard(dashboardData);
      setOutreach(outreachData.items);
      setSettings(settingsData);
      setError("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Could not load the workspace.");
    }
  };
  useEffect(() => { void loadAll(); }, []);
  useEffect(() => {
    const onPopState = () => {
      const route = routeFromLocation();
      setPage(route.page);
      if (route.jobId) void api<Job>(`/jobs/${route.jobId}`).then(setSelectedJob).catch((requestError) => setError(requestError instanceof Error ? requestError.message : "Could not open the job."));
      else setSelectedJob(null);
    };
    if (initialJobId) void api<Job>(`/jobs/${initialJobId}`).then(setSelectedJob).catch((requestError) => setError(requestError instanceof Error ? requestError.message : "Could not open the job."));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [initialJobId]);
  useEffect(() => {
    void loadJobs().catch((requestError) => setError(requestError instanceof Error ? requestError.message : "Could not load jobs."));
  }, [loadJobs]);

  const navigate = (next: Page) => { window.history.pushState({}, "", next === "Start" ? "/" : `/${next.toLowerCase()}`); setPage(next); setSelectedJob(null); setError(""); };
  const openJob = async (id: number, pushHistory = true) => {
    setBusy("open-job");
    try { setSelectedJob(await api<Job>(`/jobs/${id}`)); if (pushHistory) window.history.pushState({}, "", `/jobs/${id}`); setPage("Jobs"); setError(""); }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Could not open the job."); }
    finally { setBusy(""); }
  };
  const refreshAfterChange = async () => { await Promise.all([loadAll(), loadJobs()]); };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><strong>REACHBOARD</strong><span>PRIVATE WORKSPACE</span></div>
        <nav aria-label="Primary">
          {(["Start", "Automation", "Jobs", "Outreach", "Settings"] as Page[]).map((item) => (
            <button className={page === item ? "nav-item active" : "nav-item"} key={item} onClick={() => navigate(item)}>
              <span aria-hidden="true">{item === "Start" ? "◎" : item === "Automation" ? "↻" : item === "Jobs" ? "▤" : item === "Outreach" ? "✉" : "⚙"}</span>{item === "Start" ? "Find people" : item}
            </button>
          ))}
        </nav>
        <div className="privacy-note"><span className="privacy-dot" /><div><strong>Local & private</strong><small>Nothing is sent without review.</small></div></div>
      </aside>
      <main className="main">
        {error && <div className="alert" role="alert">{error}<button onClick={() => setError("")} aria-label="Dismiss error">×</button></div>}
        {page === "Start" && <QuickStartView onError={setError} />}
        {page === "Automation" && <AutomationView dashboard={dashboard} onOpen={openJob} onNavigate={navigate} busy={busy} />}
        {page === "Jobs" && (selectedJob ? <JobDetailView job={selectedJob} settings={settings} onBack={() => { window.history.pushState({}, "", "/jobs"); setSelectedJob(null); }} onRefresh={async () => { await openJob(selectedJob.id, false); await refreshAfterChange(); }} onError={setError} onStatus={async (status) => { await api(`/jobs/${selectedJob.id}`, { method: "PATCH", body: JSON.stringify({ status }) }); await refreshAfterChange(); await openJob(selectedJob.id, false); }} /> : <JobsView data={jobsData} filters={filters} setFilters={setFilters} onOpen={openJob} onImport={() => setImportOpen(true)} />)}
        {page === "Outreach" && <OutreachView items={outreach} onRefresh={refreshAfterChange} onError={setError} />}
        {page === "Settings" && <SettingsView settings={settings} onDeleted={refreshAfterChange} onError={setError} />}
      </main>
      {importOpen && <ImportDialog onClose={() => setImportOpen(false)} onImported={async () => { setImportOpen(false); await refreshAfterChange(); }} onError={setError} />}
    </div>
  );
}

function QuickStartView({ onError }: { onError: (message: string) => void }) {
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [result, setResult] = useState<WorkflowResult | null>(null);
  const [working, setWorking] = useState(false);
  const [stage, setStage] = useState(0);
  const [stageMessage, setStageMessage] = useState("");
  const [elapsedMs, setElapsedMs] = useState(0);
  const [peopleFound, setPeopleFound] = useState(0);
  const [sourcesRetained, setSourcesRetained] = useState(0);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [technicalEvents, setTechnicalEvents] = useState<WorkflowLogEvent[]>([]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setWorking(true);
    setStage(0);
    setStageMessage("Starting research…");
    setElapsedMs(0);
    setPeopleFound(0);
    setSourcesRetained(0);
    setWarnings([]);
    setTechnicalEvents([]);
    setResult(null);
    try {
      const response = await fetch("/api/workflow/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, url: url || null }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail ?? `Request failed (${response.status})`);
      }
      let completed = false;
      await readNdjson(response, (workflowEvent) => {
        setElapsedMs(workflowEvent.elapsed_ms);
        if (workflowEvent.type === "stage") {
          setStage(workflowEvent.stage);
          setStageMessage(workflowEvent.message);
          setTechnicalEvents((current) => [...current, workflowEvent]);
        } else if (workflowEvent.type === "detail") {
          setStageMessage(workflowEvent.message);
          setTechnicalEvents((current) => [...current, workflowEvent]);
          if (
            workflowEvent.detail.event === "contact"
            && workflowEvent.detail.decision === "accepted"
          ) setPeopleFound((count) => count + 1);
          if (
            workflowEvent.detail.event === "source"
            && workflowEvent.detail.decision === "accepted"
          ) setSourcesRetained((count) => count + 1);
        } else if (workflowEvent.type === "warning") {
          setWarnings((current) => [...current, workflowEvent.message]);
          setTechnicalEvents((current) => [...current, workflowEvent]);
        } else if (workflowEvent.type === "complete") {
          completed = true;
          setResult(workflowEvent.result);
        } else {
          throw new Error(workflowEvent.message);
        }
      });
      if (!completed) throw new Error("The workflow ended before returning a result.");
      onError("");
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : "Could not analyze this posting.");
    } finally {
      setWorking(false);
    }
  };

  const reset = () => {
    setText("");
    setUrl("");
    setResult(null);
    setStage(0);
    setStageMessage("");
    setTechnicalEvents([]);
  };

  if (result) {
    return <WorkflowResultView result={result} onReset={reset} />;
  }

  return <div className="quick-shell">
    <header className="quick-intro">
      <div>
        <p className="eyebrow">QUICK WORKFLOW</p>
        <h1>Find people for this job</h1>
        <p>Paste a public job posting. Reachboard will clean it, find relevant people, and turn their public work into conversation ideas.</p>
      </div>
      <span className="local-badge">● Local & private</span>
    </header>
    <section className="panel quick-form-panel">
      <form className="quick-form" onSubmit={submit}>
        <label>Public job URL (optional)
          <input type="url" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://…" />
        </label>
        <label>Job description
          <textarea required rows={17} value={text} onChange={(event) => setText(event.target.value)} placeholder="Paste the complete job posting here…" />
        </label>
        {working && <section className="workflow-progress">
          <div className="progress-heading">
            <div>
              <strong role="status" aria-live="polite">{stageMessage}</strong>
              <span>Stage {stage} of 4 · {(elapsedMs / 1000).toFixed(1)}s</span>
            </div>
            <span>{peopleFound} people · {sourcesRetained} sources</span>
          </div>
          <progress aria-label="Research progress" max={4} value={stage} />
          {warnings.length > 0 && <div className="progress-warnings">
            {warnings.map((warning) => <span key={warning}>{warning}</span>)}
          </div>}
          <details className="technical-log">
            <summary>Technical details</summary>
            <ol>
              {technicalEvents.map((item, index) => <li key={`${item.elapsed_ms}-${index}`}>
                <span>{(item.elapsed_ms / 1000).toFixed(1)}s</span>
                <div>
                  <strong>{item.message}</strong>
                  {item.type === "detail" && <>
                    {item.detail.query && <code>{String(item.detail.query)}</code>}
                    {item.detail.model && <small>Model: {String(item.detail.model)}</small>}
                    {item.detail.url && <small>Source: {String(item.detail.url)}</small>}
                    {item.detail.reason && <small>Decision: {String(item.detail.reason)}</small>}
                  </>}
                </div>
              </li>)}
            </ol>
          </details>
        </section>}
        <div className="quick-actions">
          <small>Uses public sources. Nothing is contacted automatically.</small>
          <button className="primary" disabled={working || !text.trim()}>{working ? "Analyzing and researching…" : "Find people to contact"}</button>
        </div>
      </form>
    </section>
  </div>;
}

function WorkflowResultView({ result, onReset }: { result: WorkflowResult; onReset: () => void }) {
  const job = result.job;
  return <div className="quick-shell">
    <header className="quick-result-header">
      <div><p className="eyebrow">ANALYSIS COMPLETE</p><h1>{job.title}</h1><p>{job.company} · {job.location || "Location unknown"}</p></div>
      <button className="secondary" onClick={onReset}>Analyze another job</button>
    </header>
    {result.warnings.length > 0 && <div className="workflow-warning">{result.warnings.map((warning) => <p key={warning}>{warning}</p>)}</div>}
    <section className="panel quick-job-summary">
      <div><span className={`quality-pill quality-${job.quality_status ?? "pending"}`}>{job.quality_status === "verified" ? "Cleaned and verified" : "Needs review"}</span>{job.requisition_id && <span>{job.requisition_id}</span>}</div>
      <details><summary>View cleaned job description</summary><div className="long-copy">{job.description}</div></details>
    </section>
    <section className="quick-people">
      <div className="section-heading"><div><p className="eyebrow">PUBLIC RESEARCH</p><h2>People worth learning from</h2><p>Choose someone whose work you genuinely want to ask about.</p></div></div>
      {job.contacts?.length ? job.contacts.map((contact) => <QuickContactCard contact={contact} key={contact.id} />) : <Empty title="No people found yet" text="The job was saved. Add Brave Search in Settings, or retry from the job library after configuring it." />}
    </section>
  </div>;
}

function QuickContactCard({ contact }: { contact: ContactDetail }) {
  return <article className="panel quick-contact">
    <div className="contact-heading">
      <div className="avatar">{contact.name.slice(0, 2).toUpperCase()}</div>
      <div><h3>{contact.name}</h3><p>{contact.title} · {contact.company}</p></div>
      {contact.profile_url && <a href={contact.profile_url} target="_blank" rel="noreferrer">Open profile ↗</a>}
    </div>
    <p className="contact-rationale">{contact.rationale}</p>
    {contact.evidence?.length > 0 && <div className="quick-evidence">
      <h4>What they have worked on</h4>
      {contact.evidence.map((item) => <blockquote key={item.id}><p>{item.excerpt}</p><a href={item.source_url} target="_blank" rel="noreferrer">{item.title || "Read public source"} ↗</a></blockquote>)}
    </div>}
    <div className="quick-angles">
      <h4>Conversation ideas</h4>
      {contact.angles?.length ? contact.angles.map((angle) => <div className="quick-angle" key={angle.id}><strong>{angle.angle}</strong><p>{angle.question}</p></div>) : <p className="muted">No grounded conversation angle was generated for this person.</p>}
    </div>
  </article>;
}

function AutomationCard({ dashboard }: { dashboard: Dashboard }) {
  const run = dashboard.automation?.last_run;
  return <div className="automation-card"><div><span className={`health ${run?.status === "completed" ? "on" : ""}`} /><strong>{run?.status === "completed" ? "Last search completed" : run ? "Search needs attention" : "Search has not run yet"}</strong></div><span>{run ? formatDate(run.started_at) : "Run the daily command to collect jobs"}</span><small>Next search: {dashboard.automation?.next_run ?? "Not scheduled"}</small></div>;
}

function AutomationView({ dashboard, onOpen, onNavigate, busy }: { dashboard: Dashboard; onOpen: (id: number) => void; onNavigate: (page: Page) => void; busy: string }) {
  const action = dashboard.next_action;
  const actionButton = action?.type === "review_draft" || action?.type === "follow_up" ? <button className="primary" onClick={() => onNavigate("Outreach")}>Open outreach</button> : action?.job ? <button className="primary" disabled={busy === "open-job"} onClick={() => onOpen(action.job!.id)}>{busy === "open-job" ? "Opening…" : "Review job"}</button> : <button className="primary" onClick={() => onNavigate("Jobs")}>Open job library</button>;
  return <>
    <header className="page-header"><div><p className="eyebrow">AUTOMATED PIPELINE</p><h1>Automation</h1><p>Monitor collected jobs and work through the queues that need your attention.</p></div><span className="local-badge">● Local & private</span></header>
    <AutomationCard dashboard={dashboard} />
    <section className="today-layout">
      <article className="panel next-action"><div className="panel-title"><div><p className="eyebrow">DO THIS NEXT</p><h2>{action?.type === "follow_up" ? "Follow up with someone" : action?.type === "review_draft" ? "Review a draft" : action?.type === "continue_job" ? "Continue this job" : "Review a new job"}</h2></div></div>{action?.job ? <><h3>{action.job.title}</h3><p className="muted">{action.job.company} · {action.job.location || "Location unknown"}</p><div className="reason-list">{(action.job.priority_reasons ?? []).map((reason) => <span key={reason}>✓ {reason}</span>)}</div>{actionButton}</> : <Empty title="Your queue is clear" text="Import a job or wait for the next automated search." />}</article>
      <section className="panel"><div className="panel-title"><div><h2>Queue overview</h2><p>Only actions that need your attention.</p></div></div><QueueCount label="New jobs to review" value={dashboard.queues?.new_jobs.length ?? dashboard.jobs.new} onClick={() => onNavigate("Jobs")} /><QueueCount label="Interested jobs in progress" value={dashboard.queues?.interested_jobs.length ?? 0} onClick={() => onNavigate("Jobs")} /><QueueCount label="Drafts awaiting review" value={dashboard.queues?.drafts.length ?? 0} onClick={() => onNavigate("Outreach")} /><QueueCount label="Follow-ups due" value={dashboard.queues?.follow_ups.length ?? dashboard.follow_ups} onClick={() => onNavigate("Outreach")} /></section>
    </section>
    <section className="secondary-summary"><article><strong>{dashboard.jobs.total}</strong><span>Total jobs in library</span></article><article><strong>{dashboard.contacts}</strong><span>People researched</span></article><article><strong>{dashboard.jobs.applied}</strong><span>Applications recorded</span></article><article><strong>{dashboard.jobs.archived}</strong><span>Skipped jobs</span></article></section>
  </>;
}

function QueueCount({ label, value, onClick }: { label: string; value: number; onClick: () => void }) { return <button className="queue-count" onClick={onClick}><span>{label}</span><strong>{value}</strong><span aria-hidden="true">→</span></button>; }

function JobsView({ data, filters, setFilters, onOpen, onImport }: { data: JobResponse; filters: JobFilters; setFilters: Dispatch<SetStateAction<JobFilters>>; onOpen: (id: number) => void; onImport: () => void }) {
  const update = (key: keyof JobFilters, value: string) => setFilters((current) => ({ ...current, [key]: value, offset: 0 }));
  return <>
    <header className="page-header compact"><div><p className="eyebrow">COMPLETE JOB LIBRARY</p><h1>Jobs</h1><p>Search every collected posting, then open one to begin the outreach workflow.</p></div><button className="primary" onClick={onImport}>＋ Import job</button></header>
    <section className="panel library-panel"><div className="library-toolbar"><label className="search-field"><span className="sr-only">Search jobs</span><input aria-label="Search jobs" placeholder="Search title, company, description, or ID" value={filters.query} onChange={(event) => update("query", event.target.value)} /></label><label><span className="sr-only">Data quality</span><select aria-label="Data quality" value={filters.quality} onChange={(event) => update("quality", event.target.value)}><option value="verified">Clean jobs</option><option value="pending">Waiting for extraction</option><option value="needs_review">Needs review</option><option value="rejected">Rejected pages</option><option value="all">All collected records</option></select></label><label><span className="sr-only">Job status</span><select aria-label="Job status" value={filters.status} onChange={(event) => update("status", event.target.value)}><option value="">All statuses</option><option value="new">Needs decision</option><option value="interested">Interested</option><option value="applied">Applied</option><option value="archived">Not for me</option></select></label><label><span className="sr-only">Location</span><select aria-label="Location" value={filters.location} onChange={(event) => update("location", event.target.value)}><option value="">All Canada</option><option value="vancouver">Vancouver</option><option value="toronto">Toronto</option><option value="elsewhere_canada">Elsewhere in Canada</option><option value="unknown">Unknown</option></select></label><label><span className="sr-only">Source</span><select aria-label="Source" value={filters.source} onChange={(event) => update("source", event.target.value)}><option value="">All sources</option>{Object.keys(data.facets?.source ?? {}).sort().map((source) => <option key={source} value={source}>{source}</option>)}</select></label><label><span className="sr-only">Posted within</span><select aria-label="Posted within" value={filters.posted} onChange={(event) => update("posted", event.target.value)}><option value="">Any age</option><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option></select></label><label><span className="sr-only">Sort jobs</span><select aria-label="Sort jobs" value={filters.sort} onChange={(event) => update("sort", event.target.value)}><option value="recommended">Recommended</option><option value="newest">Newest</option><option value="company">Company</option></select></label></div><div className="library-meta"><strong>{data.total.toLocaleString()} {data.total === 1 ? "job" : "jobs"}</strong><span>Page {Math.floor(data.offset / data.limit) + 1}</span></div><div className="job-list">{data.items.length ? data.items.map((job) => <button className="job-row library-row" key={job.id} onClick={() => onOpen(job.id)}><span><strong>{job.title}</strong><small>{job.company} · {job.location || "Location unknown"} · {formatDate(job.posted_at)}</small>{job.priority_reasons?.length ? <em>{job.priority_reasons.slice(0, 3).join(" · ")}</em> : null}</span><StatusBadge status={job.status} /></button>) : <Empty title="No matching jobs" text="Try clearing a filter or import a new posting." />}</div><div className="pagination"><button className="secondary" disabled={data.offset === 0} onClick={() => setFilters((current: typeof filters) => ({ ...current, offset: Math.max(0, current.offset - 25) }))}>← Previous</button><button className="secondary" disabled={!data.has_more} onClick={() => setFilters((current: typeof filters) => ({ ...current, offset: current.offset + 25 }))}>Next →</button></div></section>
  </>;
}

function ImportDialog({ onClose, onImported, onError }: { onClose: () => void; onImported: () => Promise<void>; onError: (message: string) => void }) {
  const [text, setText] = useState(""); const [company, setCompany] = useState(""); const [url, setUrl] = useState(""); const [saving, setSaving] = useState(false);
  const submit = async (event: FormEvent) => { event.preventDefault(); setSaving(true); try { await api("/jobs/import", { method: "POST", body: JSON.stringify({ text, company, url: url || null }) }); await onImported(); } catch (error) { onError(error instanceof Error ? error.message : "Import failed."); } finally { setSaving(false); } };
  return <div className="modal-backdrop" role="presentation"><dialog open className="modal"><div className="modal-header"><div><p className="eyebrow">MANUAL IMPORT</p><h2>Import a job</h2><p className="muted">Paste a public posting when it is not in your automated alerts.</p></div><button className="icon-button" onClick={onClose} aria-label="Close import dialog">×</button></div><form onSubmit={submit}><label>Company<input value={company} onChange={(event) => setCompany(event.target.value)} /></label><label>Public URL<input type="url" value={url} onChange={(event) => setUrl(event.target.value)} /></label><label>Job description<textarea required rows={12} value={text} onChange={(event) => setText(event.target.value)} placeholder="Paste the full posting here" /></label><div className="modal-actions"><button type="button" className="secondary" onClick={onClose}>Cancel</button><button className="primary" disabled={saving || !text.trim()}>{saving ? "Importing…" : "Import job"}</button></div></form></dialog></div>;
}

function JobDetailView({ job, settings, onBack, onRefresh, onError, onStatus }: { job: Job; settings: Settings; onBack: () => void; onRefresh: () => Promise<void>; onError: (message: string) => void; onStatus: (status: JobStatus) => Promise<void> }) {
  const [busy, setBusy] = useState(""); const [perspective, setPerspective] = useState("");
  const run = async (kind: "research" | "angles") => { setBusy(kind); try { await api(kind === "research" ? `/jobs/${job.id}/research` : `/jobs/${job.id}/angles/generate`, { method: "POST", body: kind === "angles" ? JSON.stringify({ perspective }) : undefined }); await onRefresh(); } catch (error) { onError(error instanceof Error ? error.message : `${kind} failed.`); } finally { setBusy(""); } };
  const step = job.status === "new" ? 1 : job.contacts?.length ? 3 : 2;
  return <>
    <button className="back-link" onClick={onBack}>← Back to jobs</button><header className="detail-header"><div><p className="eyebrow">JOB WORKFLOW</p><h1>Review this job</h1><p>{job.company} · {job.location || "Location unknown"} · {formatDate(job.posted_at)}</p></div><StatusBadge status={job.status} /></header>
    <div className="workflow-steps" aria-label="Job workflow"><span className={step >= 1 ? "current" : ""}>1. Review posting</span><span className={step >= 2 ? "current" : ""}>2. Decide</span><span className={step >= 3 ? "current" : ""}>3. Find someone</span><span className={step >= 4 ? "current" : ""}>4. Prepare outreach</span></div>
    <section className="detail-layout"><article className="panel detail-main"><div className="job-title-block"><p className="eyebrow">{job.requisition_id || "COLLECTED POSTING"}</p><h2>{job.title}</h2><p>{job.company} · {job.location || "Location unknown"}</p><a href={job.url} target="_blank" rel="noreferrer">Open original source ↗</a></div><section className="decision-block"><div><p className="eyebrow">STEP 2 · DECIDE</p><h2>Is this worth your time?</h2><p className="muted">You can change this later. Choosing Not for me removes it from your daily queue.</p></div><div className="decision-actions"><button className="primary" disabled={busy === "status"} onClick={() => { setBusy("status"); void onStatus("interested").finally(() => setBusy("")); }}>Interested</button><button className="secondary" onClick={() => void onStatus("applied")}>Already applied</button><button className="quiet-button" onClick={() => void onStatus("archived")}>Not for me</button></div></section><section className="research-block"><div className="section-heading"><div><p className="eyebrow">STEP 3 · PEOPLE</p><h2>Find someone worth learning from</h2><p className="muted">Public research is saved with sources. Nothing is contacted automatically.</p></div><button className="secondary" disabled={busy === "research" || !settings.brave_search_configured} onClick={() => void run("research")}>{busy === "research" ? "Researching…" : "Find people"}</button></div>{!settings.brave_search_configured && <SetupHint text="Add BRAVE_API_KEY in Settings to research public profiles." />}{job.contacts?.map((contact) => <ContactCard key={contact.id} job={job} contact={contact} settings={settings} perspective={perspective} setPerspective={setPerspective} busy={busy} run={run} onRefresh={onRefresh} onError={onError} />)}{job.status === "new" && !job.contacts?.length ? <Empty title="Decide first" text="Mark the job Interested to begin contact research." /> : null}</section></article><aside className="panel source-panel"><p className="eyebrow">STEP 1 · REVIEW POSTING</p><h2>Why this job is here</h2><h3>{job.title}</h3><div className="reason-list">{(job.priority_reasons ?? []).map((reason) => <span key={reason}>✓ {reason}</span>)}</div><details><summary>Show full job description</summary><div className="long-copy">{job.description || "No description was stored."}</div></details></aside></section>
  </>;
}

function ContactCard({ job, contact, settings, perspective, setPerspective, busy, run, onRefresh, onError }: { job: Job; contact: ContactDetail; settings: Settings; perspective: string; setPerspective: (value: string) => void; busy: string; run: (kind: "research" | "angles") => Promise<void>; onRefresh: () => Promise<void>; onError: (message: string) => void }) {
  const [channel, setChannel] = useState("connection_note"); const [context, setContext] = useState(""); const [followUp, setFollowUp] = useState(""); const [drafting, setDrafting] = useState(false); const latest = job.drafts?.filter((draft) => draft.contact_id === contact.id).at(-1);
  const draft = async (angleId: number) => { setDrafting(true); try { await api(`/jobs/${job.id}/contacts/${contact.id}/drafts/generate`, { method: "POST", body: JSON.stringify({ kind: channel, angle_id: angleId, user_context: context }) }); await onRefresh(); } catch (error) { onError(error instanceof Error ? error.message : "Draft generation failed."); } finally { setDrafting(false); } };
  const recordSent = async (draftValue: Draft) => { try { await api("/outreach-events", { method: "POST", body: JSON.stringify({ job_id: job.id, contact_id: contact.id, draft_id: draftValue.id, type: draftValue.kind === "email" ? "email_sent" : draftValue.kind === "connection_note" ? "connection_sent" : "message_sent", follow_up_at: followUp ? new Date(followUp).toISOString() : null }) }); await onRefresh(); } catch (error) { onError(error instanceof Error ? error.message : "Could not record the send."); } };
  return <article className="contact-card"><div className="contact-heading"><div className="avatar">{contact.name.slice(0, 2).toUpperCase()}</div><div><h3>{contact.name}</h3><p>{contact.title} · {contact.company}</p></div>{contact.profile_url && <a href={contact.profile_url} target="_blank" rel="noreferrer">Profile ↗</a>}</div><p className="contact-rationale">{contact.rationale}</p>{contact.evidence?.map((item) => <blockquote key={item.id}><span>{item.kind} evidence</span><p>{item.excerpt}</p><a href={item.source_url} target="_blank" rel="noreferrer">Read source ↗</a></blockquote>)}<div className="angle-controls"><div className="section-heading"><div><p className="eyebrow">STEP 4 · CONVERSATION</p><h3>Choose what you genuinely want to ask</h3></div><button className="secondary" disabled={busy === "angles" || !settings.openrouter_configured} onClick={() => void run("angles")}>{busy === "angles" ? "Finding angles…" : "Find angles"}</button></div>{!settings.openrouter_configured && <SetupHint text="Add OPENROUTER_API_KEY in Settings to generate grounded angles." />}<textarea rows={2} placeholder="Optional perspective: what would you like to understand?" value={perspective} onChange={(event) => setPerspective(event.target.value)} />{contact.angles?.map((angle) => <div className="angle-card" key={angle.id}><strong>{angle.angle}</strong><p>{angle.question}</p><div className="draft-controls"><select aria-label="Outreach channel" value={channel} onChange={(event) => setChannel(event.target.value)}><option value="connection_note">LinkedIn connection note</option><option value="message">LinkedIn message</option><option value="email">Coffee-chat email</option></select><input aria-label="Truthful context" placeholder="Your context (optional)" value={context} onChange={(event) => setContext(event.target.value)} /><button className="primary" disabled={drafting} onClick={() => void draft(angle.id)}>{drafting ? "Drafting…" : "Draft"}</button></div></div>)}</div>{latest && <div className="draft"><div className="draft-header"><span className="eyebrow">DRAFT · MANUAL SEND ONLY</span><div><button onClick={() => void navigator.clipboard.writeText(latest.body)}>Copy</button><label>Follow up <input type="date" value={followUp} onChange={(event) => setFollowUp(event.target.value)} /></label><button onClick={() => void recordSent(latest)}>Mark sent</button></div></div>{latest.subjects.map((subject) => <strong key={subject}>Subject: {subject}</strong>)}<p>{latest.body}</p><small>{latest.body.length} characters</small></div>}</article>;
}

function OutreachView({ items, onRefresh, onError }: { items: OutreachItem[]; onRefresh: () => Promise<void>; onError: (message: string) => void }) {
  const groups = useMemo(() => ({ draft: items.filter((item) => item.state === "draft"), sent: items.filter((item) => item.state === "sent"), follow: items.filter((item) => item.state.includes("follow_up")) }), [items]);
  const recordReply = async (item: OutreachItem) => { try { await api("/outreach-events", { method: "POST", body: JSON.stringify({ job_id: item.job.id, contact_id: item.contact.id, draft_id: item.draft.id, type: "reply_received" }) }); await onRefresh(); } catch (error) { onError(error instanceof Error ? error.message : "Could not record reply."); } };
  return <><header className="page-header compact"><div><p className="eyebrow">MANUAL OUTREACH</p><h1>Outreach</h1><p>Review drafts, record what you sent, and keep follow-ups visible.</p></div></header><section className="outreach-grid"><OutreachGroup title="Needs review" text="Drafts waiting for you to copy and send." items={groups.draft} actionLabel="Open job" /><OutreachGroup title="Follow-ups" text="People who need a response or follow-up." items={groups.follow} actionLabel="Record reply" onAction={recordReply} /><OutreachGroup title="Sent" text="Messages you have recorded as sent." items={groups.sent} actionLabel="Record reply" onAction={recordReply} /></section></>;
}

function OutreachGroup({ title, text, items, actionLabel, onAction }: { title: string; text: string; items: OutreachItem[]; actionLabel: string; onAction?: (item: OutreachItem) => void }) { return <section className="panel outreach-group"><div className="panel-title"><div><h2>{title}</h2><p>{text}</p></div><strong>{items.length}</strong></div>{items.length ? items.map((item) => <article className="outreach-item" key={item.id}><div><strong>{item.contact.name}</strong><small>{item.contact.title} · {item.job.title} at {item.job.company}</small></div><span className={`status status-${item.state}`}>{item.channel.replaceAll("_", " ")}</span><p>{item.draft.body}</p>{onAction && <button className="secondary" onClick={() => onAction(item)}>{actionLabel}</button>}</article>) : <Empty title="Nothing here" text="This queue is clear." />}</section>; }

function SettingsView({ settings, onDeleted, onError }: { settings: Settings; onDeleted: () => Promise<void>; onError: (message: string) => void }) {
  const [deleting, setDeleting] = useState(false); const integrations = [["Gmail read-only", settings.gmail_authorized, "Run: uv run job-outreach gmail-auth"], ["Brave Search", settings.brave_search_configured, "Set BRAVE_API_KEY in .env"], ["OpenRouter", settings.openrouter_configured, "Set OPENROUTER_API_KEY in .env"]] as const;
  const deleteData = async () => { if (window.prompt("Type DELETE to permanently clear this private workspace.") !== "DELETE") return; setDeleting(true); try { await api("/data?confirm=DELETE", { method: "DELETE" }); await onDeleted(); } catch (error) { onError(error instanceof Error ? error.message : "Could not delete local data."); } finally { setDeleting(false); } };
  return <><header className="page-header compact"><div><p className="eyebrow">WORKSPACE CONTROL</p><h1>Settings</h1><p>Connect services, understand automation, and manage your local data.</p></div></header><section className="settings-grid"><article className="panel automation-settings"><div className="section-heading"><div><h2>Automated search</h2><p>Daily collection finds jobs from configured alerts and public search. Research and messages always wait for your review.</p></div><span className="status status-configured">Local only</span></div></article>{integrations.map(([name, configured, instruction]) => <article className="panel integration" key={name}><div><span className={configured ? "health on" : "health"} /><h2>{name}</h2></div><StatusBadge status={configured ? "configured" : "not_configured"} /><p>{configured ? "Ready to use." : instruction}</p></article>)}<article className="panel guardrails"><h2>Privacy boundaries</h2><ul><li>No authenticated LinkedIn scraping</li><li>No automated connection requests, messages, or email</li><li>Public professional evidence only</li><li>AI output must cite stored evidence</li></ul></article><article className="panel data-controls"><div><h2>Your local data</h2><p>Export a portable backup or permanently clear this private workspace.</p></div><div className="data-actions"><a className="secondary button-link" href="/api/export" download="job-outreach-export.json">Export JSON</a><button className="danger" disabled={deleting} onClick={() => void deleteData()}>{deleting ? "Deleting…" : "Delete local data"}</button></div></article></section></>;
}

function SetupHint({ text }: { text: string }) { return <p className="setup-hint">Setup needed · {text}</p>; }
function Empty({ title, text }: { title: string; text: string }) { return <div className="empty"><span aria-hidden="true">◇</span><strong>{title}</strong><p>{text}</p></div>; }

export default App;
