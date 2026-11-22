import { useState } from "react";

import type { UsageRow } from "../../shared/api/client";
import { formatNumber } from "../../shared/format";

interface Day {
  day: string; // YYYY-MM-DD
  cost: number;
  calls: number;
}

const W = 640;
const H = 210;
const PAD = { left: 52, right: 8, top: 14, bottom: 30 };

/** The last `count` calendar days ending today, each with its cost and calls (0 when idle). */
export function lastDays(rows: UsageRow[], count: number, today = new Date()): Day[] {
  const byDay = new Map<string, Day>();
  for (const r of rows) {
    const d = byDay.get(r.day) ?? { day: r.day, cost: 0, calls: 0 };
    d.cost += r.cost_estimated;
    d.calls += r.calls;
    byDay.set(r.day, d);
  }
  const days: Day[] = [];
  for (let i = count - 1; i >= 0; i -= 1) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - i);
    const key = `${String(d.getFullYear())}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    days.push(byDay.get(key) ?? { day: key, cost: 0, calls: 0 });
  }
  return days;
}

/** A round axis top: 1, 2 or 5 times a power of ten, at least the largest value. */
function niceTop(value: number): number {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 5, 10]) if (step * power >= value) return step * power;
  return 10 * power;
}

function usd(value: number, top: number): string {
  return `$${value.toFixed(top < 0.1 ? 3 : 2)}`;
}

function label(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y ?? 0, (m ?? 1) - 1, d ?? 1).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
  });
}

/** A bar rounded at its top only, standing on the baseline. */
function bar(x: number, y: number, w: number, base: number): string {
  const r = Math.min(4, w / 2, (base - y) / 2);
  return `M${String(x)} ${String(base)}V${String(y + r)}Q${String(x)} ${String(y)} ${String(x + r)} ${String(y)}H${String(x + w - r)}Q${String(x + w)} ${String(y)} ${String(x + w)} ${String(y + r)}V${String(base)}Z`;
}

/**
 * Estimated model cost per day for the last 14 days: one series, a quiet USD axis, a tooltip per
 * day. The same figures are in the Usage and cost tab as a table.
 */
export function CostChart({ rows }: { rows: UsageRow[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const days = lastDays(rows, 14);
  const total = days.reduce((n, d) => n + d.cost, 0);
  const calls = days.reduce((n, d) => n + d.calls, 0);
  const top = niceTop(Math.max(...days.map((d) => d.cost)));
  const plotW = W - PAD.left - PAD.right;
  const base = H - PAD.bottom;
  const plotH = base - PAD.top;
  const slot = plotW / days.length;
  const barW = Math.max(6, slot * 0.58);
  const y = (v: number) => base - (v / top) * plotH;
  const shown = hover === null ? null : days[hover];

  return (
    <div className="pw-cost-chart">
      <p className="pw-cost-total">
        <strong>{total.toFixed(4)} USD</strong>
        <span className="pw-muted"> over 14 days, {formatNumber(calls)} model calls</span>
      </p>
      <div className="pw-chart-frame">
        <svg
          viewBox={`0 0 ${String(W)} ${String(H)}`}
          role="img"
          aria-label={`Estimated cost per day, last 14 days: ${days
            .map((d) => `${label(d.day)} ${d.cost.toFixed(4)} USD`)
            .join(", ")}`}
          onMouseLeave={() => {
            setHover(null);
          }}
        >
          {[0, 0.5, 1].map((t) => (
            <g key={t}>
              <line
                className={t === 0 ? "pw-chart-axis" : "pw-chart-grid"}
                x1={PAD.left}
                x2={W - PAD.right}
                y1={y(top * t)}
                y2={y(top * t)}
              />
              <text className="pw-chart-tick" x={PAD.left - 8} y={y(top * t) + 4} textAnchor="end">
                {usd(top * t, top)}
              </text>
            </g>
          ))}
          {days.map((d, i) => {
            const x = PAD.left + i * slot + (slot - barW) / 2;
            return (
              <g key={d.day}>
                {d.cost > 0 && (
                  <path
                    className="pw-chart-bar"
                    data-active={hover === i ? "" : undefined}
                    d={bar(x, Math.min(y(d.cost), base - 2), barW, base)}
                  />
                )}
                {(i % 2 === days.length % 2 || i === days.length - 1) && (
                  <text className="pw-chart-tick" x={x + barW / 2} y={H - 10} textAnchor="middle">
                    {label(d.day)}
                  </text>
                )}
                {/* The hover target is the whole column, wider than the bar. */}
                <rect
                  x={PAD.left + i * slot}
                  y={PAD.top}
                  width={slot}
                  height={plotH}
                  fill="transparent"
                  onMouseEnter={() => {
                    setHover(i);
                  }}
                />
              </g>
            );
          })}
        </svg>
        {shown && hover !== null && (
          <div
            className="pw-chart-tooltip"
            style={{ left: `${String(((PAD.left + (hover + 0.5) * slot) / W) * 100)}%` }}
            role="status"
          >
            <strong>{label(shown.day)}</strong>
            <span>{shown.cost.toFixed(4)} USD</span>
            <span className="pw-muted">{formatNumber(shown.calls)} calls</span>
          </div>
        )}
      </div>
    </div>
  );
}
