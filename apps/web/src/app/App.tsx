import "./app.css";

import { useEffect, useState } from "react";

import { CopilotPanel } from "../features/copilot/CopilotPanel";
import { MapPanel } from "../features/map/MapPanel";
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
import { formatSiteTime } from "../shared/format";
import { Icon } from "../shared/ui/Icon";
import { WorkspaceProvider } from "../shared/workspace/WorkspaceContext";

/** Data status text (UX spec 2); the copilot input shows the same text while not READY. */
const NOT_READY_TEXT: Record<string, string> = {
  LOADING: "Data is loading. The copilot opens when the first load is complete.",
  REBUILDING: "Data is being rebuilt. The copilot opens when the rebuild is complete.",
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

type Panel = "warehouse" | "copilot";

function signInError(): string | null {
  const reason = new URLSearchParams(window.location.search).get("auth_error");
  if (reason === null) return null;
  return SIGN_IN_ERRORS[reason] ?? SIGN_IN_ERRORS["failed"] ?? null;
}

function ProfileMenu({ me, siteName }: { me: Me; siteName: string | null }) {
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<ThemeChoice>(storedThemeChoice);
  useEffect(() => followSystemTheme(theme), [theme]);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const initials = me.name
    .split(" ")
    .map((part) => part[0] ?? "")
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className="pw-profile">
      <button
        type="button"
        className="pw-avatar"
        aria-expanded={open}
        aria-label={`Profile menu for ${me.name}`}
        onClick={() => {
          setOpen((v) => !v);
        }}
      >
        <span aria-hidden="true">{initials}</span>
      </button>
      {open && (
        <div className="pw-profile-menu">
          <p className="pw-profile-name">{me.name}</p>
          <p className="pw-muted">{ROLE_TEXT[me.role] ?? me.role}</p>
          {siteName && <p className="pw-muted">{siteName}</p>}
          <div className="pw-segmented" role="group" aria-label="Theme">
            {THEMES.map((t) => (
              <button
                key={t.value}
                type="button"
                aria-pressed={theme === t.value}
                onClick={() => {
                  setThemeChoice(t.value);
                  setTheme(t.value);
                }}
              >
                {t.label}
              </button>
            ))}
          </div>
          <button type="button" className="pw-button-quiet" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

/** Workspace shell (UX spec 2, 9): the warehouse and the copilot, nothing added to fill space. */
export function App() {
  const [authError] = useState(signInError);
  const [me, setMe] = useState<Me | null>(null);
  const [site, setSite] = useState<SiteStatus | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [panel, setPanel] = useState<Panel>("warehouse");

  useEffect(() => {
    if (authError) return;
    const controller = new AbortController();
    const load = async () => {
      try {
        const user = await fetchMe(controller.signal);
        setMe(user);
        const first = user.sites[0];
        setSite(first ? await fetchSiteStatus(first.id, controller.signal) : null);
        setUnreachable(false);
      } catch (error) {
        if (error instanceof SignedOut) signIn();
        else if (!controller.signal.aborted) setUnreachable(true);
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 15_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [authError]);

  const choosePanel = (next: Panel) => {
    setPanel(next);
    // Reveal the panel and move focus to its heading (UX spec 2).
    window.requestAnimationFrame(() => {
      const heading = document.getElementById(
        next === "warehouse" ? "pw-warehouse-title" : "pw-copilot-title",
      );
      heading?.scrollIntoView({ block: "nearest" });
      heading?.focus();
    });
  };

  const siteInfo = me?.sites[0] ?? null;
  const notReady =
    site && site.data_status !== "READY" ? (NOT_READY_TEXT[site.data_status] ?? null) : null;

  return (
    <div className="pw-app">
      <header className="pw-topbar">
        <div className="pw-brand">
          <span className="pw-brand-logo" role="img" aria-label="PalletWise logo" />
          <span>
            PalletWise
            <span className="pw-brand-sub">Warehouse copilot</span>
          </span>
        </div>
        {me && siteInfo && (
          <nav className="pw-nav" aria-label="Show panel">
            <button
              type="button"
              aria-pressed={panel === "warehouse"}
              onClick={() => {
                choosePanel("warehouse");
              }}
            >
              <Icon name="map" size={16} /> Warehouse
            </button>
            <button
              type="button"
              aria-pressed={panel === "copilot"}
              onClick={() => {
                choosePanel("copilot");
              }}
            >
              <Icon name="sparkle" size={16} /> Copilot
            </button>
          </nav>
        )}
        {me && (
          <ProfileMenu me={me} siteName={siteInfo ? `${siteInfo.name} (${siteInfo.code})` : null} />
        )}
      </header>

      {me && siteInfo && (
        <div className="pw-page-head">
          <h1>{siteInfo.name}</h1>
          <p className="pw-muted">
            Live digital twin of {siteInfo.code}
            {site?.sim_time ? ` · site time ${formatSiteTime(site.sim_time, site.time_zone)}` : ""}
          </p>
        </div>
      )}

      {authError && (
        <main className="pw-center">
          <section className="pw-panel pw-message-card">
            <h1>Sign-in was not completed</h1>
            <p role="alert">{authError}</p>
            <button type="button" className="pw-button" onClick={signIn}>
              Sign in
            </button>
          </section>
        </main>
      )}

      {unreachable && (
        <p className="pw-banner" role="alert">
          The service is not reachable right now. Retrying.
        </p>
      )}
      {notReady && (
        <p className="pw-banner" role="status">
          {notReady}
        </p>
      )}

      {me && !siteInfo && (
        <main className="pw-center">
          <section className="pw-panel pw-message-card">
            <p>Your account has no warehouse sites yet. Ask an administrator for access.</p>
          </section>
        </main>
      )}

      {me && siteInfo && (
        <WorkspaceProvider me={me} siteId={siteInfo.id} dataReady={!notReady}>
          <main className="pw-workspace" data-panel={panel}>
            <MapPanel />
            <CopilotPanel notReadyText={notReady} />
          </main>
        </WorkspaceProvider>
      )}
    </div>
  );
}
