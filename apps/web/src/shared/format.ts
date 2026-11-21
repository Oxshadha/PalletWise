/** Display formatting only. Values come from the API; nothing here calculates. */

/** Site-local time as the semantic layer writes it: "06:00, 1 Apr 2026 (Asia/Colombo)". */
export function formatSiteTime(iso: string, timeZone: string): string {
  const date = new Date(iso);
  const part = (options: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat("en-GB", { timeZone, ...options }).format(date);
  const time = part({ hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const day = part({ day: "numeric" });
  const monthYear = part({ month: "short", year: "numeric" });
  return `${time}, ${day} ${monthYear} (${timeZone})`;
}

const NUMBER = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 2 });

/** A number from the API (decimals arrive as text, for example "10000.00") with separators. */
export function formatNumber(value: unknown): string {
  const n = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
  return Number.isFinite(n) ? NUMBER.format(n) : String(value);
}

/** The site-local calendar date of an instant, as YYYY-MM-DD (the site's business day). */
export function siteDate(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(iso));
}
