import { describe, expect, it } from "vitest";

import { parseSse } from "../src/shared/api/runStream";

describe("parseSse", () => {
  it("parses complete events and keeps the unfinished remainder", () => {
    const { events, rest } = parseSse(
      'event: RUN_STARTED\nid: 1\ndata: {"seq":1}\n\nevent: x\ndata: {"se',
    );
    expect(events).toEqual([{ seq: 1 }]);
    expect(rest).toBe('event: x\ndata: {"se');
    expect(parseSse(rest + 'q":2}\n\n').events).toEqual([{ seq: 2 }]);
  });

  it("handles CRLF line endings and comment lines such as pings", () => {
    expect(parseSse(': ping\r\n\r\ndata: {"seq":3}\r\n\r\n').events).toEqual([{ seq: 3 }]);
  });

  it("skips a malformed event instead of failing the stream", () => {
    expect(parseSse('data: {not json}\n\ndata: {"seq":4}\n\n').events).toEqual([{ seq: 4 }]);
  });
});
