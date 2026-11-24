import "./admin.css";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  type AdminAgent,
  ApiProblem,
  type AuditRow,
  auditQuery,
  type Feedback,
  fetchAdminAgent,
  fetchAudit,
  fetchFeedback,
  fetchSettings,
  fetchUnanswered,
  fetchUsage,
  saveSettings,
  setAgentStatus,
  setGovernedQuery,
  type SettingsValues,
  SignedOut,
  signIn,
  type SiteSettings,
  type UnansweredRow,
  type UsageRow,
} from "../../shared/api/client";
import { formatNumber } from "../../shared/format";
import { Badge, type BadgeTone } from "../../shared/ui/Badge";
import { ConfirmDialog } from "../../shared/ui/ConfirmDialog";
import { Icon, type IconName } from "../../shared/ui/Icon";
import { CostChart } from "./CostChart";
import { evalCaseDraft } from "./evalCase";

type Tab = "overview" | "usage" | "unanswered" | "feedback" | "settings" | "audit";

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "usage", label: "Usage and cost" },
  { id: "unanswered", label: "Unanswered questions" },
  { id: "feedback", label: "Answer feedback" },
  { id: "settings", label: "Settings" },
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
  // Palette colors only. Green: passed or answered. Blue: a change was made. Amber: declined or
  // limited. Red: refused or failed. Grey: a template or no outcome. The icon tells them apart too.
  if (["OK", "ALLOW", "ANSWER"].includes(v)) return "success";
  if (["CHANGED", "SHIFTED", "STAGED", "RESET", "RAN"].includes(v)) return "action";
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

/** A share as people read it: 0.2% rather than 0% for a small but real use. */
function percent(share: number): string {
  if (share <= 0) return "0%";
  if (share < 0.001) return "under 0.1%";
  if (share < 0.1) return `${(share * 100).toFixed(1)}%`;
  return `${String(Math.round(share * 100))}%`;
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
          <Donut share={share} label="used today" value={percent(share)} />
          <p className="pw-muted pw-donut-note">
            {formatNumber(data.used_tokens_today)} of {formatNumber(data.daily_token_budget)}{" "}
            tokens. When the budget is spent, answers use templates instead of failing.
          </p>
        </section>
        <section className="pw-admin-card" aria-labelledby="pw-cost-title">
          <h3 id="pw-cost-title">Estimated cost per day</h3>
          <CostChart rows={usage.data ?? []} />
          <p className="pw-muted pw-chart-note">
            Provider-reported tokens times the configured prices. The table is under Usage and cost.
          </p>
        </section>
      </div>

      <section className="pw-admin-card" aria-labelledby="pw-controls-title">
        <h3 id="pw-controls-title">Copilot</h3>
        <div className="pw-copilot-grid">
          <dl className="pw-admin-facts">
            <dt>Agent</dt>
            <dd>
              {data.id}, version {data.version}
            </dd>
            <dt>Model</dt>
            <dd>{data.primary_model}</dd>
            <dt>Prompt version</dt>
            <dd>{data.prompt_version}</dd>
          </dl>
          <ul className="pw-control-list">
            <li className="pw-control">
              <div>
                <p className="pw-control-title">
                  Copilot{" "}
                  <Badge tone={active ? "success" : "critical"}>
                    {active ? "Active" : "Suspended"}
                  </Badge>
                </p>
                <p className="pw-muted">
                  Suspending stops every copilot answer at once, for everyone in your organisation,
                  until you resume it.
                </p>
              </div>
              <button
                type="button"
                className={active ? "pw-button-quiet pw-danger" : "pw-button"}
                aria-label={active ? "Suspend the copilot" : "Resume the copilot"}
                disabled={confirming !== null}
                onClick={() => {
                  setConfirming("status");
                }}
              >
                {active ? "Suspend" : "Resume"}
              </button>
            </li>
            {/* Shown only once a configuration has passed its evaluation gate (ADR 0002). */}
            {data.governed_query_evaluated && (
              <li className="pw-control">
                <div>
                  <p className="pw-control-title">
                    Custom queries{" "}
                    <Badge tone={data.governed_query_enabled ? "info" : "neutral"}>
                      {data.governed_query_enabled ? "On" : "Off"}
                    </Badge>
                  </p>
                  <p className="pw-muted">
                    Read-only SQL over governed views, for questions no tool covers. The model is
                    offered it only after its evaluation gate passes.
                  </p>
                </div>
                <button
                  type="button"
                  className="pw-button-quiet"
                  aria-label={
                    data.governed_query_enabled
                      ? "Switch custom queries off"
                      : "Switch custom queries on"
                  }
                  disabled={confirming !== null}
                  onClick={() => {
                    setConfirming("governed");
                  }}
                >
                  {data.governed_query_enabled ? "Switch off" : "Switch on"}
                </button>
              </li>
            )}
          </ul>
        </div>
        {confirming ? (
          <ConfirmDialog
            question={
              confirming === "status"
                ? active
                  ? "Suspend the copilot for everyone in your organisation?"
                  : "Resume the copilot?"
                : data.governed_query_enabled
                  ? "Switch custom queries off? The copilot then answers only with its tools."
                  : "Switch custom queries on? They run only for a configuration that passed its evaluation."
            }
            onCancel={() => {
              setConfirming(null);
            }}
            actions={
              <>
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
              </>
            }
          />
        ) : null}
        {problem && <Failed message={problem} />}
      </section>
    </div>
  );
}

const PAGE_SIZE = 25;

/** The rows of one page, and the page the controls show (kept in range when the rows change). */
function usePaging<T>(items: readonly T[]) {
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const current = Math.min(page, pages - 1);
  const rows = items.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE);
  return { rows, current, pages, setPage };
}

/** 1 2 3 … 9 10: the first and last page, the current one and its neighbours. */
function pageItems(current: number, pages: number): (number | null)[] {
  const wanted = new Set([0, pages - 1, current - 1, current, current + 1]);
  const items: (number | null)[] = [];
  let previous = -1;
  for (const n of [...wanted].filter((n) => n >= 0 && n < pages).sort((a, b) => a - b)) {
    if (n - previous > 1) items.push(null);
    items.push(n);
    previous = n;
  }
  return items;
}

/** Page controls under a table, always shown: arrows at the ends, numbered pages between. */
function Pager({
  current,
  pages,
  label,
  onPage,
}: {
  current: number;
  pages: number;
  label: string;
  onPage: (page: number) => void;
}) {
  return (
    <nav className="pw-pager" aria-label={label}>
      <button
        type="button"
        className="pw-page"
        aria-label="Previous page"
        disabled={current === 0}
        onClick={() => {
          onPage(current - 1);
        }}
      >
        <Icon name="chevron-left" size={16} />
      </button>
      {pageItems(current, pages).map((n, i) =>
        n === null ? (
          <span key={`gap-${String(i)}`} className="pw-pager-gap" aria-hidden="true">
            …
          </span>
        ) : (
          <button
            key={n}
            type="button"
            className="pw-page"
            aria-label={`Page ${String(n + 1)}`}
            aria-current={n === current ? "page" : undefined}
            onClick={() => {
              onPage(n);
            }}
          >
            {n + 1}
          </button>
        ),
      )}
      <button
        type="button"
        className="pw-page"
        aria-label="Next page"
        disabled={current >= pages - 1}
        onClick={() => {
          onPage(current + 1);
        }}
      >
        <Icon name="chevron-right" size={16} />
      </button>
    </nav>
  );
}

function UsageTab() {
  const [days, setDays] = useState(7);
  const load = useCallback((signal: AbortSignal) => fetchUsage(days, signal), [days]);
  const { data, error } = useLoad<UsageRow[]>(load);
  const total = (data ?? []).reduce((sum, r) => sum + r.cost_estimated, 0);
  const paging = usePaging(data ?? []);
  return (
    <div className="pw-admin-section pw-admin-fill">
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
                  {paging.rows.map((r) => (
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
          <Pager
            current={paging.current}
            pages={paging.pages}
            label="Usage pages"
            onPage={paging.setPage}
          />
        </>
      )}
    </div>
  );
}

function UnansweredTab() {
  const load = useCallback((signal: AbortSignal) => fetchUnanswered(30, signal), []);
  const { data, error } = useLoad<UnansweredRow[]>(load);
  const paging = usePaging(data ?? []);
  if (error) return <Failed message={error} />;
  if (!data) return <p className="pw-muted">Loading.</p>;
  if (data.length === 0)
    return <p className="pw-muted">Every question in the last 30 days was answered.</p>;
  return (
    <div className="pw-admin-section pw-admin-fill">
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
            {paging.rows.map((r) => (
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
      <Pager
        current={paging.current}
        pages={paging.pages}
        label="Unanswered pages"
        onPage={paging.setPage}
      />
    </div>
  );
}

type RatingFilter = "NOT_HELPFUL" | "HELPFUL" | "";

/** Answer ratings (FR-COP-14): "not helpful" answers are candidates for the evaluation set. */
function FeedbackTab() {
  const [rating, setRating] = useState<RatingFilter>("NOT_HELPFUL");
  const load = useCallback(
    (signal: AbortSignal) => fetchFeedback(30, rating || null, signal),
    [rating],
  );
  const { data, error } = useLoad<Feedback>(load);
  const paging = usePaging(data?.rows ?? []);
  const [copied, setCopied] = useState<string | null>(null);
  const copy = (text: string, trace: string) => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(trace);
    });
  };
  return (
    <div className="pw-admin-section pw-admin-fill">
      <ul className="pw-metrics">
        <Metric
          icon="thumbs-up"
          label="Helpful, 30 days"
          value={formatNumber(data?.helpful ?? 0)}
        />
        <Metric
          icon="thumbs-down"
          label="Not helpful, 30 days"
          value={formatNumber(data?.not_helpful ?? 0)}
        />
      </ul>
      <div className="pw-admin-toolbar">
        <label>
          <span>Rating</span>
          <select
            value={rating}
            onChange={(event) => {
              setRating(event.target.value as RatingFilter);
              paging.setPage(0);
            }}
          >
            <option value="NOT_HELPFUL">Not helpful</option>
            <option value="HELPFUL">Helpful</option>
            <option value="">Any</option>
          </select>
        </label>
      </div>
      <p className="pw-muted">
        A user who rates an answer not helpful sends its question and comment for review; a helpful
        rating shares nothing else. The tools come from the audit log. Copy a case to add it to the
        evaluation set after completing its gold answer.
      </p>
      {error && <Failed message={error} />}
      {data && data.rows.length === 0 && (
        <p className="pw-muted" role="status">
          No ratings like this in the last 30 days.
        </p>
      )}
      {data && data.rows.length > 0 && (
        <>
          <div className="pw-admin-scroll">
            <table className="pw-table">
              <thead>
                <tr>
                  <th scope="col">Time</th>
                  <th scope="col">Rating</th>
                  <th scope="col">Question</th>
                  <th scope="col">Comment</th>
                  <th scope="col">Tools</th>
                  <th scope="col">Outcome</th>
                  <th scope="col">Trace</th>
                  <th scope="col">
                    <span className="pw-visually-hidden">Evaluation case</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {paging.rows.map((r) => (
                  <tr key={r.trace_id}>
                    <td>{when(r.created_at)}</td>
                    <td>
                      <Badge tone={r.rating === "HELPFUL" ? "success" : "warning"}>
                        {r.rating === "HELPFUL" ? "helpful" : "not helpful"}
                      </Badge>
                    </td>
                    <td>{r.question ?? ""}</td>
                    <td>{r.comment ?? ""}</td>
                    <td>{r.tools.join(", ")}</td>
                    <td>
                      {r.outcome && (
                        <Badge tone={outcomeTone(r.outcome)}>
                          {r.outcome.toLowerCase().replaceAll("_", " ")}
                        </Badge>
                      )}
                    </td>
                    <td className="pw-admin-trace">{r.trace_id.slice(0, 12)}</td>
                    <td>
                      {r.rating === "NOT_HELPFUL" && r.question && (
                        <button
                          type="button"
                          className="pw-button-quiet"
                          onClick={() => {
                            copy(evalCaseDraft(r), r.trace_id);
                          }}
                        >
                          {copied === r.trace_id ? "Copied" : "Copy as evaluation case"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager
            current={paging.current}
            pages={paging.pages}
            label="Feedback pages"
            onPage={paging.setPage}
          />
        </>
      )}
    </div>
  );
}

type Draft = Record<keyof SettingsValues, string>;

function draftOf(values: SettingsValues): Draft {
  return Object.fromEntries(Object.entries(values).map(([k, v]) => [k, String(v)])) as Draft;
}

/** The draft as numbers, or null while any field is not a number. */
function parsed(draft: Draft): SettingsValues | null {
  const out: Record<string, number> = {};
  for (const [key, text] of Object.entries(draft)) {
    const value = Number(text);
    if (text.trim() === "" || !Number.isFinite(value)) return null;
    out[key] = value;
  }
  return out as unknown as SettingsValues;
}

/** Site settings (FR-ADM-04): versioned, audited; each shows what it affects. */
function SettingsTab() {
  const { data, error, reload } = useLoad<SiteSettings>(fetchSettings);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  if (error) return <Failed message={error} />;
  if (!data) return <p className="pw-muted">Loading.</p>;
  // Keyed by version: a saved or reloaded version starts the form again from its values.
  return (
    <SettingsForm
      key={data.version_no}
      data={data}
      message={message}
      onMessage={setMessage}
      onSaved={reload}
    />
  );
}

function SettingsForm({
  data,
  message,
  onMessage: setMessage,
  onSaved: reload,
}: {
  data: SiteSettings;
  message: { ok: boolean; text: string } | null;
  onMessage: (message: { ok: boolean; text: string } | null) => void;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(data.values));
  const [note, setNote] = useState("");
  const [confirming, setConfirming] = useState(false);

  const values = parsed(draft);
  const changed = values !== null && JSON.stringify(values) !== JSON.stringify(data.values);
  const save = async () => {
    setConfirming(false);
    if (!values) return;
    try {
      const saved = await saveSettings(data.version_no, values, note.trim() || null);
      setMessage({
        ok: true,
        text:
          saved.outcome === "changed"
            ? `Saved as version ${String(saved.version_no)}. Open exceptions are re-ranked on the next evaluation cycle.`
            : "Nothing changed.",
      });
      setNote("");
      reload();
    } catch (e: unknown) {
      setMessage({
        ok: false,
        text: e instanceof ApiProblem ? e.message : "The settings could not be saved.",
      });
    }
  };
  const groups: { id: "rules" | "impact"; title: string }[] = [
    { id: "rules", title: "Exception rules" },
    { id: "impact", title: "Inbox ranking (impact score)" },
  ];

  return (
    <div className="pw-admin-section pw-admin-fill">
      <p className="pw-muted">
        Version {data.version_no} is in effect. Saving creates a new version; earlier versions and
        scores stay in the history, and every change is in the audit log.
      </p>
      <form
        className="pw-settings"
        onSubmit={(event) => {
          event.preventDefault();
          setConfirming(true);
        }}
      >
        <div className="pw-admin-cards">
          {groups.map((group) => (
            <fieldset key={group.id} className="pw-admin-card pw-settings-group">
              <legend>{group.title}</legend>
              {data.fields
                .filter((f) => f.group === group.id)
                .map((f) => {
                  const key = f.key as keyof SettingsValues;
                  return (
                    <div key={f.key} className="pw-setting">
                      <label htmlFor={`setting-${f.key}`}>{f.label}</label>
                      <span className="pw-setting-input">
                        <input
                          id={`setting-${f.key}`}
                          type="number"
                          inputMode="decimal"
                          step={f.step}
                          value={draft[key]}
                          aria-describedby={`setting-${f.key}-affects`}
                          onChange={(event) => {
                            setDraft({ ...draft, [key]: event.target.value });
                            setMessage(null);
                          }}
                        />
                        {f.unit && <span className="pw-muted">{f.unit}</span>}
                      </span>
                      <p id={`setting-${f.key}-affects`} className="pw-muted">
                        {f.affects}
                      </p>
                    </div>
                  );
                })}
            </fieldset>
          ))}
        </div>
        <div className="pw-admin-toolbar">
          <label>
            <span>Note</span>
            <input
              type="text"
              maxLength={200}
              value={note}
              placeholder="Why this change"
              onChange={(event) => {
                setNote(event.target.value);
              }}
            />
          </label>
          <button type="submit" className="pw-button" disabled={!changed || confirming}>
            Save settings
          </button>
          <button
            type="button"
            className="pw-button-quiet"
            disabled={!changed}
            onClick={() => {
              setDraft(draftOf(data.values));
              setMessage(null);
            }}
          >
            Discard changes
          </button>
        </div>
        {values === null && <Failed message="Every setting needs a number." />}
      </form>
      {confirming && (
        <ConfirmDialog
          question={`Save these settings as version ${String(data.version_no + 1)}? Answers use them from the next question, and open exceptions are re-ranked on the next evaluation cycle.`}
          onCancel={() => {
            setConfirming(false);
          }}
          actions={
            <>
              <button type="button" className="pw-button" onClick={() => void save()}>
                Confirm
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
            </>
          }
        />
      )}
      {message &&
        (message.ok ? (
          <p className="pw-muted" role="status">
            {message.text}
          </p>
        ) : (
          <Failed message={message.text} />
        ))}
      <section className="pw-admin-card" aria-labelledby="pw-settings-history">
        <h3 id="pw-settings-history">Recent versions</h3>
        <table className="pw-table">
          <thead>
            <tr>
              <th scope="col" className="pw-num">
                Version
              </th>
              <th scope="col">Saved</th>
              <th scope="col">By</th>
              <th scope="col">Note</th>
            </tr>
          </thead>
          <tbody>
            {data.history.map((v) => (
              <tr key={v.version_no}>
                <td className="pw-num">{v.version_no}</td>
                <td>{when(v.saved_at)}</td>
                <td>{v.saved_by}</td>
                <td>{v.note ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

type AuditGroup = AuditRow & { count: number };

/** The audit stores the sign-in id; people read the role ("warehouse_manager" becomes "Warehouse manager"). */
function roleLabel(role: string | null | undefined): string | null {
  if (!role) return null;
  const words = role.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Consecutive identical events (the same who, what and result) show once with a count. */
function groupAudit(rows: AuditRow[]): AuditGroup[] {
  const groups: AuditGroup[] = [];
  const key = (r: AuditRow) =>
    [
      r.actor_type,
      r.user_subject,
      r.agent_id,
      r.agent_version,
      r.action,
      r.tool,
      r.decision,
      r.outcome,
    ].join("|");
  for (const r of rows) {
    const last = groups.at(-1);
    if (last && key(last) === key(r)) last.count += 1;
    else groups.push({ ...r, count: 1 });
  }
  return groups;
}

function AuditTab() {
  const [applied, setApplied] = useState<Record<string, string>>({});
  const load = useCallback((signal: AbortSignal) => fetchAudit(applied, signal), [applied]);
  const { data, error } = useLoad<AuditRow[]>(load);
  const query = auditQuery(applied);
  const grouped = useMemo(() => groupAudit(data ?? []), [data]);
  const paging = usePaging(grouped);
  const { rows, current, pages, setPage } = paging;
  return (
    <div className="pw-admin-section pw-admin-fill">
      <div className="pw-admin-toolbar">
        <label>
          <span>Decision</span>
          <select
            value={applied["decision"] ?? ""}
            onChange={(event) => {
              setApplied(event.target.value ? { decision: event.target.value } : {});
              setPage(0);
            }}
          >
            <option value="">Any</option>
            <option value="ALLOW">Allowed</option>
            <option value="DENY">Denied</option>
          </select>
        </label>
        <a className="pw-button-quiet" href={`/api/admin/audit.csv${query ? `?${query}` : ""}`}>
          Export CSV
        </a>
      </div>
      {error && <Failed message={error} />}
      {data && (
        <>
          {data.length === 0 && (
            <p className="pw-muted" role="status">
              No entries match these filters.
            </p>
          )}
          <div className="pw-admin-scroll">
            <table className="pw-table">
              <thead>
                <tr>
                  <th scope="col">Time</th>
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
                {rows.map((r) => (
                  <tr key={`${r.trace_id}-${r.occurred_at}-${r.action}-${r.tool ?? ""}`}>
                    <td>{when(r.occurred_at)}</td>
                    <td title={r.user_subject ?? undefined}>
                      {r.user_name ?? roleLabel(r.user_role) ?? r.user_subject?.slice(0, 8) ?? ""}
                      {r.user_name && r.user_role && (
                        <span className="pw-muted"> ({roleLabel(r.user_role)})</span>
                      )}
                    </td>
                    <td>
                      {r.agent_id
                        ? `${r.agent_id}${r.agent_version ? ` ${r.agent_version}` : ""}`
                        : ""}
                    </td>
                    <td>
                      {r.action}
                      {r.count > 1 && <span className="pw-muted"> ×{r.count}</span>}
                    </td>
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
          <Pager current={current} pages={pages} label="Audit log pages" onPage={setPage} />
        </>
      )}
    </div>
  );
}

/** The admin panel (FR-ADM-03 to 07, FR-COP-14), for the Admin only. */
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
      <div className="pw-admin-body">
        {tab === "overview" && <OverviewTab />}
        {tab === "usage" && <UsageTab />}
        {tab === "unanswered" && <UnansweredTab />}
        {tab === "feedback" && <FeedbackTab />}
        {tab === "settings" && <SettingsTab />}
        {tab === "audit" && <AuditTab />}
      </div>
    </main>
  );
}
