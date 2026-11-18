import "./app.css";

import { useEffect, useState } from "react";

import { fetchSiteStatus, type SiteStatus } from "../shared/api/client";
import {
  followSystemTheme,
  setThemeChoice,
  storedThemeChoice,
  type ThemeChoice,
} from "../shared/theme/theme";

const STATUS_TEXT: Record<string, string> = {
  LOADING: "Data is loading. The workspace opens when the first load is complete.",
  REBUILDING: "Data is being rebuilt. The workspace opens when the rebuild is complete.",
  READY: "Data is ready.",
  FAILED: "The last data load failed. An administrator has been notified.",
};

const THEMES: { value: ThemeChoice; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "System" },
];

/** Slice 0 shell: brand, theme and the site's data readiness. Features arrive in Slice 1. */
export function App() {
  const [site, setSite] = useState<SiteStatus | null>(null);
  const [error, setError] = useState(false);
  const [theme, setTheme] = useState<ThemeChoice>(storedThemeChoice);

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

  useEffect(() => followSystemTheme(theme), [theme]);

  const chooseTheme = (choice: ThemeChoice) => {
    setThemeChoice(choice);
    setTheme(choice);
  };

  return (
    <>
      <header className="pw-topbar">
        <div className="pw-brand">
          <span className="pw-brand-logo" role="img" aria-label="PalletWise logo" />
          PalletWise
        </div>
        <div className="pw-theme-toggle" role="group" aria-label="Theme">
          {THEMES.map((t) => (
            <button
              key={t.value}
              type="button"
              aria-pressed={theme === t.value}
              onClick={() => {
                chooseTheme(t.value);
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
      </header>
      <main className="pw-main">
        <section className="pw-card" aria-live="polite">
          {error && <p role="alert">The service is not reachable yet.</p>}
          {site && (
            <>
              <h1>{site.name}</h1>
              <p>Warehouse code {site.code}</p>
              <span className="pw-status">{STATUS_TEXT[site.data_status] ?? site.data_status}</span>
              {site.sim_time && <p>Site time: {new Date(site.sim_time).toLocaleString()}</p>}
            </>
          )}
        </section>
      </main>
    </>
  );
}
