/** Stroke icons drawn in currentColor, so tokens decide their color. Decorative: always hidden. */
const PATHS = {
  map: "M3 6.5 9 4l6 2.5L21 4v13.5L15 20l-6-2.5L3 20zM9 4v13.5M15 6.5V20",
  sparkle:
    "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z",
  history: "M3 12a9 9 0 1 0 3-6.7M3 4v4h4M12 7v5l3 2",
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  recenter: "M12 3v3M12 18v3M3 12h3M18 12h3M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  close: "M6 6l12 12M18 6 6 18",
  layers: "M12 3 2 8l10 5 10-5zM2 13l10 5 10-5",
  send: "M4 12 20 4l-6 16-3-7z",
  stop: "M7 7h10v10H7z",
  pin: "M12 21s-6-5.6-6-11a6 6 0 1 1 12 0c0 5.4-6 11-6 11zM12 8a2 2 0 1 0 0 4 2 2 0 0 0 0-4z",
  arrow: "M5 12h14M13 6l6 6-6 6",
  "rotate-left": "M4 12a8 8 0 1 0 2.3-5.7M4 4v4h4",
  "rotate-right": "M20 12a8 8 0 1 1-2.3-5.7M20 4v4h-4",
  "tilt-up": "M12 19V5M6 11l6-6 6 6",
  "tilt-down": "M12 5v14M6 13l6 6 6-6",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
