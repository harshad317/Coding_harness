import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Download,
  FileCode2,
  FolderGit2,
  Play,
  RefreshCw,
  Settings2,
  XCircle
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";

type RunStatus = "queued" | "running" | "succeeded" | "failed";

type RunRecord = {
  passed_self: boolean | null;
  final_error_type: string | null;
  iterations_used: number;
  bash_calls_used: number;
  tokens_in: number;
  tokens_out: number;
  extra: {
    changed_paths?: string[];
    critical_suggestions?: string[];
    analysis_steps?: string[];
    final_diff?: string;
    last_test_output?: string;
    applied?: boolean;
    snapshot_omitted?: string[];
  };
};

type HarnessRun = {
  id: string;
  status: RunStatus;
  created_at: string;
  updated_at: string;
  events: string[];
  record: RunRecord | null;
  error: string | null;
  diff_path: string | null;
  repo_path: string;
  instruction: string;
  test_command: string;
  model: string;
  apply: boolean;
  max_iterations: number;
  max_bash_calls: number;
  repo_timeout: number;
  max_repo_bytes: number;
  max_file_bytes: number;
};

type RunForm = {
  repo_path: string;
  instruction: string;
  test_command: string;
  model: string;
  max_iterations: number;
  max_bash_calls: number;
  repo_timeout: number;
  max_tokens: number;
  temperature: number;
  model_timeout: number;
  max_repo_bytes: number;
  max_file_bytes: number;
  apply: boolean;
};

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

const initialForm: RunForm = {
  repo_path: "",
  instruction: "",
  test_command: "python -m pytest -q",
  model: "gpt-5.4-mini",
  max_iterations: 3,
  max_bash_calls: 10,
  repo_timeout: 60,
  max_tokens: 4096,
  temperature: 0,
  model_timeout: 300,
  max_repo_bytes: 200000,
  max_file_bytes: 30000,
  apply: false
};

export function App() {
  const [form, setForm] = useState<RunForm>(initialForm);
  const [runs, setRuns] = useState<HarnessRun[]>([]);
  const [activeRun, setActiveRun] = useState<HarnessRun | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void refreshRuns();
  }, []);

  useEffect(() => {
    if (!activeRun || activeRun.status === "succeeded" || activeRun.status === "failed") {
      return;
    }
    const source = new EventSource(`${API_BASE}/api/runs/${activeRun.id}/events`);
    source.onmessage = (event) => {
      const run = JSON.parse(event.data) as HarnessRun;
      setActiveRun(run);
      setRuns((current) => upsertRun(current, run));
      if (run.status === "succeeded" || run.status === "failed") {
        source.close();
      }
    };
    source.onerror = () => {
      source.close();
      void refreshRun(activeRun.id);
    };
    return () => source.close();
  }, [activeRun?.id, activeRun?.status]);

  const metrics = useMemo(() => {
    const record = activeRun?.record;
    if (!record) {
      return [];
    }
    return [
      ["Iterations", String(record.iterations_used)],
      ["Test Runs", String(record.bash_calls_used)],
      ["Tokens", String(record.tokens_in + record.tokens_out)],
      ["Applied", record.extra.applied ? "Yes" : "No"]
    ];
  }, [activeRun]);

  async function refreshRuns() {
    const response = await fetch(`${API_BASE}/api/runs`);
    if (!response.ok) {
      return;
    }
    const data = (await response.json()) as HarnessRun[];
    setRuns(data);
    if (!activeRun && data.length > 0) {
      setActiveRun(data[0]);
    }
  }

  async function refreshRun(id: string) {
    const response = await fetch(`${API_BASE}/api/runs/${id}`);
    if (!response.ok) {
      return;
    }
    const run = (await response.json()) as HarnessRun;
    setActiveRun(run);
    setRuns((current) => upsertRun(current, run));
  }

  async function submitRun(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/api/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form)
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? `Request failed with ${response.status}`);
      }
      const run = (await response.json()) as HarnessRun;
      setActiveRun(run);
      setRuns((current) => upsertRun(current, run));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to start run");
    } finally {
      setSubmitting(false);
    }
  }

  const changedPaths = activeRun?.record?.extra.changed_paths ?? [];
  const suggestions = activeRun?.record?.extra.critical_suggestions ?? [];
  const steps = activeRun?.record?.extra.analysis_steps ?? [];
  const omitted = activeRun?.record?.extra.snapshot_omitted ?? [];
  const diff = activeRun?.record?.extra.final_diff ?? "";
  const testOutput = activeRun?.record?.extra.last_test_output ?? "";

  return (
    <div className="shell">
      <header className="topbar">
        <div>
          <div className="eyebrow">D_val</div>
          <h1>Coding Harness</h1>
        </div>
        <button className="iconButton" type="button" onClick={refreshRuns} aria-label="Refresh runs" title="Refresh runs">
          <RefreshCw size={18} />
        </button>
      </header>

      <main className="layout">
        <section className="panel controlPanel" aria-label="Run configuration">
          <div className="panelHeader">
            <FolderGit2 size={18} />
            <h2>Repo Run</h2>
          </div>
          <form onSubmit={submitRun} className="runForm">
            <label>
              Repo Path
              <input
                value={form.repo_path}
                onChange={(event) => setForm({ ...form, repo_path: event.target.value })}
                placeholder="/absolute/path/to/repo"
              />
            </label>

            <label>
              Instruction
              <textarea
                value={form.instruction}
                onChange={(event) => setForm({ ...form, instruction: event.target.value })}
                placeholder="Fix the failing tests and keep changes minimal."
              />
            </label>

            <label>
              Test Command
              <input
                value={form.test_command}
                onChange={(event) => setForm({ ...form, test_command: event.target.value })}
              />
            </label>

            <div className="formGrid">
              <label>
                Model
                <input value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} />
              </label>
              <label>
                Iterations
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={form.max_iterations}
                  onChange={(event) => setForm({ ...form, max_iterations: Number(event.target.value) })}
                />
              </label>
              <label>
                Bash Budget
                <input
                  type="number"
                  min={1}
                  value={form.max_bash_calls}
                  onChange={(event) => setForm({ ...form, max_bash_calls: Number(event.target.value) })}
                />
              </label>
              <label>
                Timeout
                <input
                  type="number"
                  min={1}
                  value={form.repo_timeout}
                  onChange={(event) => setForm({ ...form, repo_timeout: Number(event.target.value) })}
                />
              </label>
              <label>
                Max Tokens
                <input
                  type="number"
                  min={256}
                  value={form.max_tokens}
                  onChange={(event) => setForm({ ...form, max_tokens: Number(event.target.value) })}
                />
              </label>
              <label>
                Temperature
                <input
                  type="number"
                  min={0}
                  max={2}
                  step={0.1}
                  value={form.temperature}
                  onChange={(event) => setForm({ ...form, temperature: Number(event.target.value) })}
                />
              </label>
              <label>
                Model Timeout
                <input
                  type="number"
                  min={1}
                  value={form.model_timeout}
                  onChange={(event) => setForm({ ...form, model_timeout: Number(event.target.value) })}
                />
              </label>
              <label>
                Repo Bytes
                <input
                  type="number"
                  min={1000}
                  value={form.max_repo_bytes}
                  onChange={(event) => setForm({ ...form, max_repo_bytes: Number(event.target.value) })}
                />
              </label>
              <label>
                File Bytes
                <input
                  type="number"
                  min={1000}
                  value={form.max_file_bytes}
                  onChange={(event) => setForm({ ...form, max_file_bytes: Number(event.target.value) })}
                />
              </label>
            </div>

            <label className="toggle">
              <input
                type="checkbox"
                checked={form.apply}
                onChange={(event) => setForm({ ...form, apply: event.target.checked })}
              />
              Apply clean runs to source repo
            </label>

            {error && <div className="errorLine">{error}</div>}

            <button className="primaryButton" disabled={submitting} type="submit">
              <Play size={18} />
              {submitting ? "Starting" : "Start Run"}
            </button>
          </form>
        </section>

        <section className="workspace" aria-label="Run details">
          <div className="panel statusPanel">
            <div className="panelHeader">
              <Activity size={18} />
              <h2>Run State</h2>
            </div>
            {activeRun ? (
              <>
                <div className="statusRow">
                  <StatusBadge status={activeRun.status} />
                  <span className="runId">{activeRun.id}</span>
                </div>
                <div className="metricGrid">
                  {metrics.map(([label, value]) => (
                    <div className="metric" key={label}>
                      <span>{label}</span>
                      <strong>{value}</strong>
                    </div>
                  ))}
                </div>
                <div className="eventLog">
                  {activeRun.events.map((item) => (
                    <div key={item}>{item}</div>
                  ))}
                </div>
              </>
            ) : (
              <EmptyState text="No run selected." />
            )}
          </div>

          <div className="detailGrid">
            <section className="panel">
              <div className="panelHeader">
                <FileCode2 size={18} />
                <h2>Changed Files</h2>
              </div>
              <ListBlock items={changedPaths} empty="No file changes yet." />
            </section>

            <section className="panel">
              <div className="panelHeader warningHeader">
                <AlertTriangle size={18} />
                <h2>Critical Suggestions</h2>
              </div>
              <ListBlock items={suggestions} empty="No suggestions yet." />
            </section>
          </div>

          <section className="panel">
            <div className="panelHeader">
              <Settings2 size={18} />
              <h2>Step Trace</h2>
            </div>
            <ListBlock items={[...steps, ...omitted.map((item) => `Omitted: ${item}`)]} empty="No step trace yet." />
          </section>

          <section className="panel outputPanel">
            <div className="panelHeader">
              <FileCode2 size={18} />
              <h2>Diff</h2>
              {activeRun?.diff_path && (
                <a className="iconButton linkButton" href={`${API_BASE}/api/runs/${activeRun.id}/diff`} title="Download diff">
                  <Download size={18} />
                </a>
              )}
            </div>
            <pre>{diff || "No diff yet."}</pre>
          </section>

          <section className="panel outputPanel">
            <div className="panelHeader">
              <Activity size={18} />
              <h2>Test Output</h2>
            </div>
            <pre>{testOutput || "No test output yet."}</pre>
          </section>
        </section>

        <aside className="panel historyPanel" aria-label="Run history">
          <div className="panelHeader">
            <RefreshCw size={18} />
            <h2>History</h2>
          </div>
          <div className="historyList">
            {runs.map((run) => (
              <button
                className={`historyItem ${activeRun?.id === run.id ? "selected" : ""}`}
                key={run.id}
                type="button"
                onClick={() => setActiveRun(run)}
              >
                <StatusDot status={run.status} />
                <span>{run.repo_path.split("/").pop() || run.repo_path}</span>
                <small>{new Date(run.created_at).toLocaleTimeString()}</small>
              </button>
            ))}
            {runs.length === 0 && <EmptyState text="No runs yet." />}
          </div>
        </aside>
      </main>
    </div>
  );
}

function StatusBadge({ status }: { status: RunStatus }) {
  const icon =
    status === "succeeded" ? <CheckCircle2 size={18} /> : status === "failed" ? <XCircle size={18} /> : <Activity size={18} />;
  return <span className={`statusBadge ${status}`}>{icon}{status}</span>;
}

function StatusDot({ status }: { status: RunStatus }) {
  return <span className={`statusDot ${status}`} />;
}

function ListBlock({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) {
    return <EmptyState text={empty} />;
  }
  return (
    <ul className="itemList">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="emptyState">{text}</div>;
}

function upsertRun(runs: HarnessRun[], run: HarnessRun) {
  const next = runs.filter((item) => item.id !== run.id);
  return [run, ...next].sort((a, b) => b.created_at.localeCompare(a.created_at));
}
