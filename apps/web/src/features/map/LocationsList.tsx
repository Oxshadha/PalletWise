import type { Highlight, Layout, LayoutBin } from "../../shared/api/client";

const REASON_TEXT: Record<string, string> = {
  location: "Stored here",
  query: "Matches the question",
  fast_mover: "Fast mover",
  low_stock: "Low stock",
  stockout_risk: "Stock-out risk",
  delayed_order: "Delayed order",
  dead_stock: "Dead stock",
  slow_in_prime: "Slow mover in a prime location",
  capacity: "Capacity",
  festival_risk: "Festival risk",
  proposal: "Pending proposal",
};

/** The keyboard and screen-reader path to every highlighted location (UX spec 3). */
export function LocationsList({
  highlights,
  zoneHighlights,
  zones,
  bins,
  selectedBinId,
  onSelect,
}: {
  highlights: Highlight[];
  zoneHighlights: Highlight[];
  zones: Map<string, Layout["zones"][number]>;
  bins: Map<string, LayoutBin>;
  selectedBinId: string | null;
  onSelect: (binId: string) => void;
}) {
  const items = highlights
    .map((h) => ({
      highlight: h,
      bin: h.target_type === "bin" ? bins.get(h.target_id) : undefined,
    }))
    .filter((item): item is { highlight: Highlight; bin: LayoutBin } => item.bin !== undefined)
    .sort((a, b) => a.bin.code.localeCompare(b.bin.code));
  const zoneItems = zoneHighlights
    .map((h) => ({ highlight: h, zone: zones.get(h.target_id) }))
    .filter((item) => item.zone !== undefined)
    .sort((a, b) => (a.zone?.code ?? "").localeCompare(b.zone?.code ?? ""));
  const count = items.length + zoneItems.length;
  // Said once: when every highlight has the same reason, it heads the list instead of every row.
  const reasons = new Set([...zoneItems, ...items].map((item) => item.highlight.reason));
  const shared = reasons.size === 1 ? [...reasons][0] : undefined;
  const reasonText = (reason: string) => REASON_TEXT[reason] ?? reason;

  return (
    <details className="pw-locations" open>
      <summary>
        Highlighted locations <span className="pw-count">{count}</span>
      </summary>
      {shared && <p className="pw-locations-reason">{reasonText(shared)}</p>}
      {count === 0 ? (
        <p className="pw-muted">
          No locations are highlighted. Ask the copilot or select a location on the map.
        </p>
      ) : (
        <ul>
          {zoneItems.map(({ highlight, zone }) => (
            <li key={highlight.target_id}>
              <span className={`pw-location is-${highlight.severity}`}>
                <span className="pw-location-code">Zone {zone?.code}</span>
                {!shared && <span className="pw-muted">{reasonText(highlight.reason)}</span>}
              </span>
            </li>
          ))}
          {items.map(({ highlight, bin }) => (
            <li key={bin.id}>
              <button
                type="button"
                aria-pressed={bin.id === selectedBinId}
                className={`pw-location is-${highlight.severity}`}
                onClick={() => {
                  onSelect(bin.id);
                }}
              >
                <span className="pw-location-code">{bin.code}</span>
                {!shared && <span className="pw-muted">{reasonText(highlight.reason)}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}
