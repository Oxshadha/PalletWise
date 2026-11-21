import "./admin.css";

import { useCallback, useEffect, useState } from "react";

import {
  type AdminAgent,
  ApiProblem,
  type AuditRow,
  auditQuery,
  fetchAdminAgent,
  fetchAudit,
  fetchUnanswered,
  fetchUsage,
  setAgentStatus,
  SignedOut,
  signIn,
  type UnansweredRow,
  type UsageRow,
} from "../../shared/api/client";
import { formatNumber } from "../../shared/format";

type Tab = "copilot" | "usage" | "unanswered" | "audit";

const TABS: { id: Tab; label: string }[] = [
  { id: "copilot", label: "Copilot" },
  { id: "usage", label: "Usage and cost" },
  { id: "unanswered", label: "Unanswered questions" },
  { id: "audit", label: "Audit log" },
];

/** Loads once per change of its inputs; a failure shows the API's message. */
function useLoad<T>(load: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<{ data: T | null; error: string | null }>({
    data: null,
    error: null,
  });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal)
      .then((data) => {
        setState({ data, error: null });
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted) return;
        if (e instanceof SignedOut) signIn();
        setState({
          data: null,
          error: e instanceof ApiProblem ? e.message : "This could not be loaded.",
        });
      });
    return () => {
      controller.abort();
    };
  }, [load, attempt]);
  const reload = useCallback(() => {
    setAttempt((n) => n + 1);
  }, []);
  return { ...state, reload };
}

function when(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(iso),
  );
}

function Failed({ message }: { message: string }) {
  return (
    <p className="pw-admin-error" role="alert">
      {message}
    </p>
  );
}

function CopilotTab() {
  const { data, error, reload } = useLoad<AdminAgent>(fetchAdminAgent);
  const [confirming, setConfirming] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  if (error) return <Failed message={error} />;
  if (!data) return <p className="pw-muted">Loading.</p>;
  const active = data.status === "ACTIVE";
  const change = async () => {
    setConfirming(false);
    try {
      await setAgentStatus(active ? "SUSPENDED" : "ACTIVE");
      setProblem(null);
      reload();
    } catch (e: unknown) {
      setProblem(e instanceof ApiProblem ? e.message : "The change could not be made.");
    }
  };
  return (
    <div className="pw-admin-section">
      <dl className="pw-admin-facts">
        <dt>Status</dt>
        <dd>
          <strong className={active ? "pw-admin-ok" : "pw-admin-off"}>
            {active ? "Active" : "Suspended"}
          </strong>
        </dd>
        <dt>Agent</dt>
        <dd>
          {data.id}, version {data.version}
        </dd>
        <dt>Model</dt>
        <dd>{data.primary_model}</dd>
        <dt>Prompt version</dt>
        <dd>{data.prompt_version}</dd>
        <dt>Tokens used today</dt>
        <dd>
          {formatNumber(data.used_tokens_today)} of {formatNumber(data.daily_token_budget)}
        </dd>
      </dl>
      <p className="pw-muted">
        {active
          ? "Suspending stops every new copilot question at once, suggested questions included. The map, inbox and brief numbers keep working."
          : "The copilot is suspended: every question is refused with a clear message."}
      </p>
      {confirming ? (
        <div className="pw-confirm" role="alertdialog" aria-label="Confirm">
          <p>
            {active
              ? "Suspend the copilot for everyone in your organisation?"
              : "Resume the copilot?"}
          </p>
          <div className="pw-admin-actions">
            <button
              type="button"
              className={active ? "pw-button pw-danger-fill" : "pw-button"}
              onClick={() => void change()}
            >
              {active ? "Suspend the copilot" : "Resume the copilot"}
            </button>
            <button
              type="button"
              className="pw-button-quiet"
              onClick={() => {
                setConfirming(false);
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="pw-admin-actions">
          <button
            type="button"
            className={active ? "pw-button-quiet pw-danger" : "pw-button"}
            onClick={() => {
              setConfirming(true);
            }}
          >
            {active ? "Suspend the copilot" : "Resume the copilot"}
          </button>
        </div>
      )}
      {problem && <Failed message={problem} />}
    </div>
  );
}

function UsageTab() {
  const [days, setDays] = useState(7);
  const load = useCallback((signal: AbortSignal) => fetchUsage(days, signal), [days]);
  const { data, error } = useLoad<UsageRow[]>(load);
  const total = (data ?? []).reduce((sum, r) => sum + r.cost_estimated, 0);
  return (
    <div className="pw-admin-section">
      <div className="pw-segmented" role="group" aria-label="Period">
        {[1, 7, 30].map((d) => (
          <button
            key={d}
            type="button"
            aria-pressed={days === d}
            onClick={() => {
              setDays(d);
            }}
          >
            {d === 1 ? "Today" : `${String(d)} days`}
          </button>
        ))}
      </div>
      {error && <Failed message={error} />}
      {data && (
        <>
          <p>
            Estimated cost: <strong>{total.toFixed(4)} USD</strong>{" "}
            <span className="pw-muted">(provider-reported tokens times the configured prices)</span>
          </p>
          {data.length === 0 ? (
            <p className="pw-muted">No model calls in this period.</p>
          ) : (
            <div className="pw-admin-scroll">
              <table className="pw-table">
                <thead>
                  <tr>
                    <th scope="col">Day</th>
                    <th scope="col">Model</th>
                    <th scope="col">Purpose</th>
                    <th scope="col" className="pw-num">
                      Calls
                    </th>
                    <th scope="col" className="pw-num">
                      Tokens in
                    </th>
                    <th scope="col" className="pw-num">
                      Tokens out
                    </th>
                    <th scope="col" className="pw-num">
                      Cost (USD)
                    </th>
                    <th scope="col" className="pw-num">
                      Avg latency
                    </th>
                    <th scope="col" className="pw-num">
                      Errors
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((r) => (
                    <tr key={`${r.day}-${r.model}-${r.purpose}`}>
                      <td>{r.day}</td>
                      <td>{r.model}</td>
                      <td>{r.purpose.toLowerCase()}</td>
                      <td className="pw-num">{formatNumber(r.calls)}</td>
                      <td className="pw-num">{formatNumber(r.input_tokens)}</td>
                      <td className="pw-num">{formatNumber(r.output_tokens)}</td>
                      <td className="pw-num">{r.cost_estimated.toFixed(4)}</td>
                      <td className="pw-num">{formatNumber(r.avg_latency_ms)} ms</td>
                      <td className="pw-num">{formatNumber(r.errors)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function UnansweredTab() {
  const load = useCallback((signal: AbortSignal) => fetchUnanswered(30, signal), []);
  const { data, error } = useLoad<UnansweredRow[]>(load);
  if (error) return <Failed message={error} />;
  if (!data) return <p className="pw-muted">Loading.</p>;
  if (data.length === 0)
    return <p className="pw-muted">Every question in the last 30 days was answered.</p>;
  return (
    <div className="pw-admin-section">
      <p className="pw-muted">
        Questions the copilot declined or could not answer in the last 30 days, most frequent first.
        Frequent ones are candidates for a new tool.
      </p>
      <div className="pw-admin-scroll">
        <table className="pw-table">
          <thead>
            <tr>
              <th scope="col">Question</th>
              <th scope="col" className="pw-num">
                Times
              </th>
              <th scope="col">Last asked</th>
              <th scope="col">Reason</th>
            </tr>
          </thead>
          <tbody>
            {data.map((r) => (
              <tr key={r.question}>
                <td>{r.question}</td>
                <td className="pw-num">{formatNumber(r.times)}</td>
                <td>{when(r.last_asked)}</td>
                <td>{r.reasons.map((x) => x.toLowerCase().replaceAll("_", " ")).join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const FILTERS = [
  { key: "user", label: "User" },
  { key: "agent", label: "Agent" },
  { key: "tool", label: "Tool" },
  { key: "action", label: "Action" },
] as const;

function AuditTab() {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [applied, setApplied] = useState<Record<string, string>>({});
  const load = useCallback((signal: AbortSignal) => fetchAudit(applied, signal), [applied]);
  const { data, error } = useLoad<AuditRow[]>(load);
  const query = auditQuery(applied);
  return (
    <div className="pw-admin-section">
      <form
        className="pw-admin-filters"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied({ ...draft });
        }}
      >
        {FILTERS.map((f) => (
          <label key={f.key}>
            <span>{f.label}</span>
            <input
              value={draft[f.key] ?? ""}
              onChange={(event) => {
                setDraft({ ...draft, [f.key]: event.target.value });
              }}
            />
          </label>
        ))}
        <label>
          <span>Decision</span>
          <select
            value={draft["decision"] ?? ""}
            onChange={(event) => {
              setDraft({ ...draft, decision: event.target.value });
            }}
          >
            <option value="">Any</option>
            <option value="ALLOW">Allowed</option>
            <option value="DENY">Denied</option>
          </select>
        </label>
        <button type="submit" className="pw-button">
          Apply
        </button>
        <a className="pw-button-quiet" href={`/api/admin/audit.csv${query ? `?${query}` : ""}`}>
          Export CSV
        </a>
      </form>
      {error && <Failed message={error} />}
      {data && (
        <>
          <p className="pw-muted" role="status">
            {data.length === 500
              ? "The newest 500 entries; narrow the filters to see older ones."
              : `${String(data.length)} entries, newest first.`}
          </p>
          <div className="pw-admin-scroll">
            <table className="pw-table">
              <thead>
                <tr>
                  <th scope="col">Time</th>
                  <th scope="col">Actor</th>
                  <th scope="col">User</th>
                  <th scope="col">Agent</th>
                  <th scope="col">Action</th>
                  <th scope="col">Tool</th>
                  <th scope="col">Decision</th>
                  <th scope="col">Outcome</th>
                  <th scope="col">Trace</th>
                </tr>
              </thead>
              <tbody>
                {data.map((r) => (
                  <tr key={`${r.trace_id}-${r.occurred_at}-${r.action}-${r.tool ?? ""}`}>
                    <td>{when(r.occurred_at)}</td>
                    <td>{r.actor_type.toLowerCase()}</td>
                    <td>
                      {r.user_subject ?? ""}
                      {r.user_role ? ` (${r.user_role})` : ""}
                    </td>
                    <td>
                      {r.agent_id
                        ? `${r.agent_id}${r.agent_version ? ` ${r.agent_version}` : ""}`
                        : ""}
                    </td>
                    <td>{r.action}</td>
                    <td>{r.tool ?? ""}</td>
                    <td>{r.decision?.toLowerCase() ?? ""}</td>
                    <td>{r.outcome.toLowerCase()}</td>
                    <td className="pw-admin-trace">{r.trace_id.slice(0, 12)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

/** The admin panel core (FR-ADM-03, 05, 06, 07), for the Admin only. */
export function AdminPanel() {
  const [tab, setTab] = useState<Tab>("copilot");
  return (
    <main className="pw-admin pw-panel" aria-labelledby="pw-admin-title">
      <header className="pw-admin-head">
        <h2 id="pw-admin-title" tabIndex={-1}>
          Admin
        </h2>
        <div className="pw-segmented" role="group" aria-label="Admin section">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              aria-pressed={tab === t.id}
              onClick={() => {
                setTab(t.id);
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
      </header>
      {tab === "copilot" && <CopilotTab />}
      {tab === "usage" && <UsageTab />}
      {tab === "unanswered" && <UnansweredTab />}
      {tab === "audit" && <AuditTab />}
    </main>
  );
}
