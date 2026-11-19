import type { Highlight, LayoutBin } from "../../shared/api/client";

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
  bins,
  selectedBinId,
  onSelect,
}: {
  highlights: Highlight[];
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

  return (
    <details className="pw-locations" open>
      <summary>
        Highlighted locations <span className="pw-count">{items.length}</span>
      </summary>
      {items.length === 0 ? (
        <p className="pw-muted">
          No locations are highlighted. Ask the copilot or select a location on the map.
        </p>
      ) : (
        <ul>
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
                <span className="pw-muted">
                  {REASON_TEXT[highlight.reason] ?? highlight.reason}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}
