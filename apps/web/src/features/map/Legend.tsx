/** Map legend (FR-MAP-03): every state has a pattern or outline, never color alone. */
export function Legend() {
  return (
    <details className="pw-legend">
      <summary>Legend</summary>
      <ul>
        <li>
          <span className="pw-swatch is-normal" aria-hidden="true" /> Location with stock
        </li>
        <li>
          <span className="pw-swatch is-empty" aria-hidden="true" /> Empty location (thin hatch,
          dashed edge)
        </li>
        <li>
          <span className="pw-swatch is-low-stock" aria-hidden="true" /> Low stock (bold hatch,
          solid edge)
        </li>
        <li>
          <span className="pw-swatch is-over-capacity" aria-hidden="true" /> Over capacity (cross
          hatch)
        </li>
        <li>
          <span className="pw-swatch is-info" aria-hidden="true" /> Highlighted: information
          (outline; zones outlined on the floor)
        </li>
        <li>
          <span className="pw-swatch is-warning" aria-hidden="true" /> Highlighted: warning
          (outline)
        </li>
        <li>
          <span className="pw-swatch is-critical" aria-hidden="true" /> Highlighted: critical (thick
          outline)
        </li>
        <li>
          <span className="pw-swatch is-selected" aria-hidden="true" /> Selected location (dashed)
        </li>
      </ul>
    </details>
  );
}
