import {
  ArrowRight,
  Check,
  ChevronDown,
  ChevronUp,
  History,
  Inbox,
  Info,
  Layers,
  LocateFixed,
  type LucideIcon,
  Map,
  MapPin,
  MessageSquareText,
  Minus,
  OctagonAlert,
  Plus,
  RotateCcw,
  RotateCw,
  Search,
  SendHorizontal,
  Square,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
  X,
} from "lucide-react";

/** One icon set (Lucide) at one stroke weight, drawn in currentColor so tokens decide the color.
 * Decorative: always hidden from assistive technology; the control carries the accessible name. */
const ICONS = {
  map: Map,
  chat: MessageSquareText,
  history: History,
  plus: Plus,
  minus: Minus,
  recenter: LocateFixed,
  close: X,
  layers: Layers,
  send: SendHorizontal,
  stop: Square,
  pin: MapPin,
  arrow: ArrowRight,
  "rotate-left": RotateCcw,
  "rotate-right": RotateCw,
  "tilt-up": ChevronUp,
  "tilt-down": ChevronDown,
  "thumbs-up": ThumbsUp,
  "thumbs-down": ThumbsDown,
  inbox: Inbox,
  check: Check,
  info: Info,
  warning: TriangleAlert,
  critical: OctagonAlert,
  search: Search,
} as const satisfies Record<string, LucideIcon>;

export type IconName = keyof typeof ICONS;

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  const Glyph = ICONS[name];
  return <Glyph size={size} strokeWidth={1.75} aria-hidden="true" focusable="false" />;
}
