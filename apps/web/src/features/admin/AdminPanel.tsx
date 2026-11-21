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
  setGovernedQuery,
  SignedOut,
  signIn,
  type UnansweredRow,
  type UsageRow,
} from "../../shared/api/client";
import { formatNumber } from "../../shared/format";
import { Badge, type BadgeTone } from "../../shared/ui/Badge";
import { Icon, type IconName } from "../../shared/ui/Icon";

type Tab = "overview" | "usage" | "unanswered" | "audit";

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
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

function outcomeTone(value: string | null | undefined): BadgeTone {
  const v = (value ?? "").toUpperCase();
  if (["OK", "ALLOW", "ANSWER", "CHANGED", "SHIFTED", "STAGED", "RESET", "RAN"].includes(v))
    return "success";
  if (["DENY", "DENIED", "ERROR", "FAILED", "REFUSED"].includes(v)) return "critical";
  if (["DECLINE", "BUSY", "VALIDATION_FAILED", "RATE_LIMITED", "NO_RESULTS", "MIXED"].includes(v))
    return "warning";
  return "neutral";
}

function Metric({
  icon,
  label,
  value,
  note,
}: {
  icon: IconName;
  label: string;
  value: string;
  note?: string;
}) {
  return (
    <li className="pw-metric">
      <span className="pw-metric-icon">
        <Icon name={icon} size={20} />
      </span>
      <span className="pw-metric-text">
        <span className="pw-muted">{label}</span>
        <strong>{value}</strong>
        {note && <span className="pw-metric-note">{note}</span>}
      </span>
    </li>
  );
}

/** A donut of one share (0 to 1), with the figure in its centre. */
function Donut({ share, label, value }: { share: number; label: string; value: string }) {
  const r = 52;
  const length = 2 * Math.PI * r;
  const filled = Math.max(0, Math.min(1, share)) * length;
  return (
    <svg className="pw-donut" viewBox="0 0 140 140" role="img" aria-label={`${label}: ${value}`}>
      <circle cx="70" cy="70" r={r} className="pw-donut-track" />
      <circle
        cx="70"
        cy="70"
        r={r}
        className="pw-donut-value"
        strokeDasharray={`${String(filled)} ${String(length - filled)}`}
        transform="rotate(-90 70 70)"
      />
      <text x="70" y="66" textAnchor="middle" className="pw-donut-figure">
        {value}
      </text>
      <text x="70" y="86" textAnchor="middle" className="pw-donut-label">
        {label}
      </text>
    </svg>
  );
}

/** One bar per day, scaled to the largest; empty days keep their place. */
function BarStrip({ days, label }: { days: { day: string; value: number }[]; label: string }) {
  const top = Math.max(...days.map((d) => d.value), 0);
  if (days.length === 0) return <p className="pw-muted">No model calls in this period.</p>;
  return (
    <div
      className="pw-bars"
      role="img"
      aria-label={`${label}: ${days.map((d) => `${d.day} ${d.value.toFixed(4)}`).join(", ")}`}
    >
      {days.map((d) => (
        <span key={d.day} className="pw-bar" title={`${d.day}: ${d.value.toFixed(4)} USD`}>
          <span
            className="pw-bar-fill"
            style={{ height: `${String(top ? (d.value / top) * 100 : 0)}%` }}
          />
        </span>
      ))}
    </div>
  );
}

/** Estimated cost per day, from the days the API returned (oldest first). */
function costPerDay(rows: UsageRow[]): { day: string; value: number }[] {
  const byDay = new Map<string, number>();
  for (const r of rows) byDay.set(r.day, (byDay.get(r.day) ?? 0) + r.cost_estimated);
  return [...byDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([day, value]) => ({ day, value }));
}

function OverviewTab() {
  const agentState = useLoad<AdminAgent>(fetchAdminAgent);
  const usageLoad = useCallback((signal: AbortSignal) => fetchUsage(14, signal), []);
  const usage = useLoad<UsageRow[]>(usageLoad);
  const weekLoad = useCallback((signal: AbortSignal) => fetchUsage(7, signal), []);
  const week = useLoad<UsageRow[]>(weekLoad).data ?? [];
  const unansweredLoad = useCallback((signal: AbortSignal) => fetchUnanswered(30, signal), []);
  const unanswered = useLoad<UnansweredRow[]>(unansweredLoad);
  const [confirming, setConfirming] = useState<"status" | "governed" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const data = agentState.data;
  if (agentState.error) return <Failed message={agentState.error} />;
  if (!data) return <p className="pw-muted">Loading.</p>;
  const active = data.status === "ACTIVE";
  const calls = week.reduce((n, r) => n + r.calls, 0);
  const cost = week.reduce((n, r) => n + r.cost_estimated, 0);
  const share = data.daily_token_budget ? data.used_tokens_today / data.daily_token_budget : 0;
  const unansweredCount = (unanswered.data ?? []).reduce((n, r) => n + r.times, 0);

  const act = async (change: () => Promise<void>) => {
    setConfirming(null);
    try {
      await change();
      setProblem(null);
      agentState.reload();
    } catch (e: unknown) {
      setProblem(e instanceof ApiProblem ? e.message : "The change could not be made.");
    }
  };

  return (
    <div className="pw-admin-section">
      <ul className="pw-metrics">
        <Metric
          icon="gauge"
          label="Tokens used today"
          value={formatNumber(data.used_tokens_today)}
          note={`of ${formatNumber(data.daily_token_budget)}`}
        />
        <Metric icon="activity" label="Model calls, 7 days" value={formatNumber(calls)} />
        <Metric icon="coins" label="Estimated cost, 7 days" value={`${cost.toFixed(4)} USD`} />
        <Metric icon="help" label="Unanswered, 30 days" value={formatNumber(unansweredCount)} />
      </ul>

      <div className="pw-admin-cards">
        <section className="pw-admin-card" aria-labelledby="pw-budget-title">
          <h3 id="pw-budget-title">Daily token budget</h3>
          <Donut share={share} label="used today" value={`${String(Math.round(share * 100))}%`} />
        </section>
        <section className="pw-admin-card" aria-labelledby="pw-cost-title">
          <h3 id="pw-cost-title">Estimated cost per day</h3>
          <p className="pw-muted">
            Last 14 days, in USD; provider-reported tokens times the configured prices.
          </p>
          <BarStrip days={costPerDay(usage.data ?? [])} label="Estimated cost per day" />
        </section>
      </div>

      <section className="pw-admin-card" aria-labelledby="pw-controls-title">
        <h3 id="pw-controls-title">Copilot</h3>
        <dl className="pw-admin-facts">
          <dt>Status</dt>
          <dd>
            <Badge tone={active ? "success" : "critical"}>{active ? "Active" : "Suspended"}</Badge>
          </dd>
          <dt>Custom queries</dt>
          <dd>
            <Badge tone={data.governed_query_enabled ? "info" : "neutral"}>
              {data.governed_query_enabled ? "On" : "Off"}
            </Badge>
          </dd>
          <dt>Agent</dt>
          <dd>
            {data.id}, version {data.version}
          </dd>
          <dt>Model</dt>
          <dd>{data.primary_model}</dd>
          <dt>Prompt version</dt>
          <dd>{data.prompt_version}</dd>
        </dl>
        {confirming ? (
          <div className="pw-confirm" role="alertdialog" aria-label="Confirm">
            <p>
              {confirming === "status"
                ? active
                  ? "Suspend the copilot for everyone in your organisation?"
                  : "Resume the copilot?"
                : data.governed_query_enabled
                  ? "Switch custom queries off? The copilot then answers only with its tools."
                  : "Switch custom queries on? They run only for a configuration that passed its evaluation."}
            </p>
            <div className="pw-admin-actions">
              <button
                type="button"
                className={
                  confirming === "status" && active ? "pw-button pw-danger-fill" : "pw-button"
                }
                onClick={() =>
                  void act(() =>
                    confirming === "status"
                      ? setAgentStatus(active ? "SUSPENDED" : "ACTIVE")
                      : setGovernedQuery(!data.governed_query_enabled),
                  )
                }
              >
                Confirm
              </button>
              <button
                type="button"
                className="pw-button-quiet"
                onClick={() => {
                  setConfirming(null);
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
                setConfirming("status");
              }}
            >
              {active ? "Suspend the copilot" : "Resume the copilot"}
            </button>
            <button
              type="button"
              className="pw-button-quiet"
              onClick={() => {
                setConfirming("governed");
              }}
            >
              {data.governed_query_enabled
                ? "Switch custom queries off"
                : "Switch custom queries on"}
            </button>
          </div>
        )}
        {problem && <Failed message={problem} />}
      </section>
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
                <td className="pw-badges">
                  {r.reasons.map((x) => (
                    <Badge key={x} tone={outcomeTone(x)}>
                      {x.toLowerCase().replaceAll("_", " ")}
                    </Badge>
                  ))}
                </td>
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
                    <td>
                      {r.decision && (
                        <Badge tone={outcomeTone(r.decision)}>{r.decision.toLowerCase()}</Badge>
                      )}
                    </td>
                    <td>
                      <Badge tone={outcomeTone(r.outcome)}>
                        {r.outcome.toLowerCase().replaceAll("_", " ")}
                      </Badge>
                    </td>
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
  const [tab, setTab] = useState<Tab>("overview");
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
      {tab === "overview" && <OverviewTab />}
      {tab === "usage" && <UsageTab />}
      {tab === "unanswered" && <UnansweredTab />}
      {tab === "audit" && <AuditTab />}
    </main>
  );
}
