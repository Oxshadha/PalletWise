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
