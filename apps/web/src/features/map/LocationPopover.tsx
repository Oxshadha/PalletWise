import { useEffect, useRef, useState } from "react";

import {
  ApiProblem,
  fetchBinDetails,
  type LayoutBin,
  type ResultEnvelope,
} from "../../shared/api/client";
import { formatSiteTime } from "../../shared/format";

/** Bin details (UX spec 4): products with on hand, allocated, available, last movement. */
export function LocationPopover({
  siteId,
  bin,
  onClose,
  onAsk,
}: {
  siteId: string;
  bin: LayoutBin;
  onClose: () => void;
  onAsk: (bin: LayoutBin) => void;
}) {
  const [details, setDetails] = useState<ResultEnvelope | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchBinDetails(siteId, bin.code, controller.signal)
      .then(setDetails)
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setFailed(error instanceof ApiProblem ? error.message : "Details could not be loaded.");
        }
      });
    heading.current?.focus();
    return () => {
      controller.abort();
    };
  }, [siteId, bin.code]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const rows = details?.rows ?? [];
  return (
    <section className="pw-popover" aria-labelledby="pw-popover-title" aria-live="polite">
      <header>
        <h3 id="pw-popover-title" ref={heading} tabIndex={-1}>
          Location {bin.code}
        </h3>
        <button
          type="button"
          className="pw-icon-button"
          aria-label="Close location details"
          onClick={onClose}
        >
          <span aria-hidden="true">x</span>
        </button>
      </header>
      <p className="pw-muted">{bin.bin_role.toLowerCase()} location</p>
      {failed && <p role="alert">{failed}</p>}
      {!details && !failed && <p className="pw-muted">Loading details...</p>}
      {details && rows.length === 0 && <p>{details.message ?? "This location is empty."}</p>}
      {details && rows.length > 0 && (
        <table className="pw-table">
          <caption className="pw-visually-hidden">Products in {bin.code}</caption>
          <thead>
            <tr>
              <th scope="col">Product</th>
              <th scope="col" className="pw-num">
                On hand
              </th>
              <th scope="col" className="pw-num">
                Allocated
              </th>
              <th scope="col" className="pw-num">
                Available
              </th>
              <th scope="col">Last movement</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={String(row["sku"])}>
                <th scope="row">
                  {String(row["sku"])}
                  <span className="pw-muted pw-block">{String(row["name"])}</span>
                </th>
                <td className="pw-num">{String(row["on_hand"])}</td>
                <td className="pw-num">{String(row["allocated"])}</td>
                <td className="pw-num">{String(row["available"])}</td>
                <td>
                  {formatSiteTime(String(row["last_movement_at"]), details.evidence.time_zone)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <button
        type="button"
        className="pw-button"
        onClick={() => {
          onAsk(bin);
        }}
      >
        Ask about this bin
      </button>
    </section>
  );
}
