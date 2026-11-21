export type BadgeTone = "success" | "warning" | "critical" | "info" | "neutral";

/** A status chip: a tinted pill with a dot; the word carries the meaning, never the color alone. */
export function Badge({ tone, children }: { tone: BadgeTone; children: string }) {
  return (
    <span className={`pw-badge is-${tone}`}>
      <span className="pw-badge-dot" aria-hidden="true" />
      {children}
    </span>
  );
}
