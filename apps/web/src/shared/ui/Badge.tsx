import { Icon, type IconName } from "./Icon";

export type BadgeTone = "success" | "warning" | "critical" | "info" | "action" | "neutral";

const ICON: Record<BadgeTone, IconName> = {
  success: "status-success",
  warning: "warning",
  critical: "status-failed",
  info: "status-working",
  action: "activity",
  neutral: "status-idle",
};

/** A status chip: a soft tinted pill with an icon and a bold word. The word and the icon carry the
 * meaning, never the color alone. */
export function Badge({ tone, children }: { tone: BadgeTone; children: string }) {
  return (
    <span className={`pw-badge is-${tone}`}>
      <Icon name={ICON[tone]} size={14} />
      {children}
    </span>
  );
}
