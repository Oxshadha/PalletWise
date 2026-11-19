import "./app.css";

import { useEffect, useState } from "react";

import {
  fetchMe,
  fetchSiteStatus,
  type Me,
  signIn,
  SignedOut,
  signOut,
  type SiteStatus,
} from "../shared/api/client";
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

/** Messages for the reasons the API gives when it refuses a sign-in (identity spec 4). */
const SIGN_IN_ERRORS: Record<string, string> = {
  role: "Your account has no PalletWise role, or more than one. Ask an administrator to assign exactly one.",
  tenant: "Your account is not linked to an organisation in PalletWise. Ask an administrator.",
  state: "The sign-in link expired or was already used. Please sign in again.",
  unavailable: "The sign-in service is not reachable right now. Please try again shortly.",
  failed: "Sign-in did not complete. Please try again.",
};

const ROLE_TEXT: Record<string, string> = {
  warehouse_manager: "Warehouse Manager",
  admin: "Admin",
};

const THEMES: { value: ThemeChoice; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "System" },
];

function signInError(): string | null {
  const reason = new URLSearchParams(window.location.search).get("auth_error");
  if (reason === null) return null;
  return SIGN_IN_ERRORS[reason] ?? SIGN_IN_ERRORS.failed ?? null;
}

/** App shell: brand, theme, the signed-in user and the site's data readiness. */
export function App() {
  const [authError] = useState(signInError);
  const [me, setMe] = useState<Me | null>(null);
  const [site, setSite] = useState<SiteStatus | null>(null);
  const [error, setError] = useState(false);
  const [theme, setTheme] = useState<ThemeChoice>(storedThemeChoice);

  useEffect(() => {
    if (authError) return;
    const controller = new AbortController();
    const load = async () => {
      try {
        const user = await fetchMe(controller.signal);
        setMe(user);
        const first = user.sites[0];
        setSite(first ? await fetchSiteStatus(first.id, controller.signal) : null);
        setError(false);
      } catch (e) {
        if (e instanceof SignedOut) signIn();
        else if (!controller.signal.aborted) setError(true);
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [authError]);

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
        <div className="pw-topbar-actions">
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
          {me && (
            <div className="pw-user">
              <span>
                {me.name}
                <span className="pw-user-role">{ROLE_TEXT[me.role] ?? me.role}</span>
              </span>
              <button type="button" className="pw-button-quiet" onClick={() => void signOut()}>
                Sign out
              </button>
            </div>
          )}
        </div>
      </header>
      <main className="pw-main">
        <section className="pw-card" aria-live="polite">
          {authError && (
            <>
              <h1>Sign-in was not completed</h1>
              <p role="alert">{authError}</p>
              <button type="button" className="pw-button" onClick={signIn}>
                Sign in
              </button>
            </>
          )}
          {error && <p role="alert">The service is not reachable yet.</p>}
          {me && me.sites.length === 0 && (
            <p>Your account has no warehouse sites yet. Ask an administrator for access.</p>
          )}
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
