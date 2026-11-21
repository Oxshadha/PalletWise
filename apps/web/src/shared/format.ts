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

/** The site's UTC offset at an instant, in minutes (for the date picker; display only). */
function offsetMinutes(at: number, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).formatToParts(new Date(at));
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0);
  const asUtc = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"));
  return Math.round((asUtc - Math.floor(at / 60_000) * 60_000) / 60_000);
}

/** A site-local "YYYY-MM-DDTHH:mm" (a datetime-local input) as an ISO instant. */
export function siteLocalToIso(local: string, timeZone: string): string {
  const [date = "", time = "00:00"] = local.split("T");
  const [y = 0, m = 1, d = 1] = date.split("-").map(Number);
  const [hh = 0, mm = 0] = time.split(":").map(Number);
  const guess = Date.UTC(y, m - 1, d, hh, mm);
  return new Date(guess - offsetMinutes(guess, timeZone) * 60_000).toISOString();
}

/** An instant as a site-local "YYYY-MM-DDTHH:mm" for a datetime-local input. */
export function isoToSiteLocal(iso: string, timeZone: string): string {
  const at = new Date(iso).getTime();
  return new Date(at + offsetMinutes(at, timeZone) * 60_000).toISOString().slice(0, 16);
}
