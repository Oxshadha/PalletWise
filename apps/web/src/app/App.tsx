import { useEffect, useState } from "react";

import { fetchSiteStatus, type SiteStatus } from "../shared/api/client";

const STATUS_TEXT: Record<string, string> = {
  LOADING: "Data is loading. The workspace opens when the first load is complete.",
  REBUILDING: "Data is being rebuilt. The workspace opens when the rebuild is complete.",
  READY: "Data is ready.",
  FAILED: "The last data load failed. An administrator has been notified.",
};

/** Slice 0 shell: shows the site and its data readiness. Features arrive in Slice 1. */
export function App() {
  const [site, setSite] = useState<SiteStatus | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const load = () => {
      fetchSiteStatus(controller.signal)
        .then((s) => {
          setSite(s);
          setError(false);
        })
        .catch(() => {
          if (!controller.signal.aborted) setError(true);
        });
    };
    load();
    const timer = window.setInterval(load, 5000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: 32, maxWidth: 720 }}>
      <h1>PalletWise</h1>
      {error && <p role="alert">The service is not reachable yet.</p>}
      {site && (
        <section aria-live="polite">
          <h2>{site.name}</h2>
          <p>{STATUS_TEXT[site.data_status] ?? site.data_status}</p>
          {site.sim_time && <p>Site time: {new Date(site.sim_time).toLocaleString()}</p>}
        </section>
      )}
    </main>
  );
}
