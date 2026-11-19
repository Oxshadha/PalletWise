import type { ResultEnvelope } from "../src/shared/api/client";
import type { RunEvent } from "../src/shared/api/runStream";

export const BIN_A = "00000000-0000-7000-8000-00000000000a";
export const BIN_B = "00000000-0000-7000-8000-00000000000b";

export function envelope(highlighted: string[], rows = 2): ResultEnvelope {
  return {
    status: "OK",
    message: null,
    columns: [
      { key: "bin_code", label: "Location", type: "text", unit: null },
      { key: "on_hand", label: "On hand", type: "integer", unit: "units" },
    ],
    rows: Array.from({ length: rows }, (_, i) => ({
      bin_code: `A1-01-${String(i).padStart(2, "0")}`,
      on_hand: i,
    })),
    truncated: false,
    highlights: highlighted.map((id) => ({
      target_type: "bin",
      target_id: id,
      reason: "location",
      severity: "info",
    })),
    highlights_truncated: false,
    evidence: {
      as_of: "2026-04-01T00:30:00Z",
      time_zone: "Asia/Colombo",
      settings_version_id: null,
      data_as_of: "2026-04-01T00:30:00Z",
      freshness: [],
      how_computed: ["Available is on hand minus allocated."],
    },
    entities: [],
    normalized_request: { tool: "find_product_locations", sku: "SKU-102" },
  };
}

let seq = 0;
export function event<T extends RunEvent["type"]>(
  type: T,
  data: Omit<Extract<RunEvent, { type: T }>, "seq" | "type">,
  at?: number,
): RunEvent {
  seq += 1;
  return { type, seq: at ?? seq, ...data } as unknown as RunEvent;
}
