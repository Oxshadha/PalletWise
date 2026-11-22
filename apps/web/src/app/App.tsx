import "./app.css";

import { useEffect, useRef, useState } from "react";

import { AdminPanel } from "../features/admin/AdminPanel";
import { CopilotPanel } from "../features/copilot/CopilotPanel";
import { InboxPanel } from "../features/inbox/InboxPanel";
import { Search } from "../features/search/SearchBox";
import { MapPanel } from "../features/map/MapPanel";
import { Bell } from "../features/simulation/Bell";
import { SimulationControls } from "../features/simulation/SimulationControls";
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
import { useSiteChanges } from "../shared/api/siteEvents";
import { formatSiteTime, siteDate } from "../shared/format";
import { Icon } from "../shared/ui/Icon";
import { WorkspaceProvider } from "../shared/workspace/WorkspaceContext";
import { Splitter } from "./Splitter";
import { SignIn } from "./SignIn";

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

const THEMES: { value: ThemeChoice; label: string; icon: "sun" | "moon" | "monitor" }[] = [
  { value: "light", label: "Light", icon: "sun" },
  { value: "dark", label: "Dark", icon: "moon" },
  { value: "system", label: "System", icon: "monitor" },
];

type Panel = "warehouse" | "copilot" | "inbox" | "admin";
type Side = "copilot" | "inbox";

const PANEL_HEADING: Record<Panel, string> = {
  warehouse: "pw-warehouse-title",
  copilot: "pw-copilot-title",
  inbox: "pw-inbox-title",
  admin: "pw-admin-title",
};

/** Below this width one panel shows at a time (UX spec 9, app.css). */
const ONE_PANEL = "(max-width: 1199px)";

/** True below the two-panel width; false where media queries are not available. */
function narrowScreen(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(ONE_PANEL).matches;
}

const RETRY_KEY = "pw.sign-in-retry";
/** A fresh sign-in is on its way: render nothing and load nothing. */
const REDIRECTING = "";
const RETRY_WINDOW_MS = 60_000;

/**
 * An expired or already used sign-in link (a sign-in page left open past its 10 minutes, or the
 * back button) is harmless: start a fresh sign-in once instead of showing an error. A second
 * failure within a minute shows the message, so this can never loop.
 */
function retryExpiredSignIn(reason: string | null): boolean {
  if (reason !== "state") return false;
  try {
    const last = Number(window.sessionStorage.getItem(RETRY_KEY));
    if (last && Date.now() - last < RETRY_WINDOW_MS) return false;
    window.sessionStorage.setItem(RETRY_KEY, String(Date.now()));
  } catch {
    return false; // storage blocked: show the message rather than risk a loop
  }
  return true;
}

function signInError(): string | null {
  const reason = new URLSearchParams(window.location.search).get("auth_error");
  if (reason === null) return null;
  if (retryExpiredSignIn(reason)) {
    signIn();
    return REDIRECTING;
  }
  return SIGN_IN_ERRORS[reason] ?? SIGN_IN_ERRORS["failed"] ?? null;
}

function ProfileMenu({ me, siteName }: { me: Me; siteName: string | null }) {
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<ThemeChoice>(storedThemeChoice);
  useEffect(() => followSystemTheme(theme), [theme]);
  const menu = useRef<HTMLDivElement>(null);
  // Closes on Escape and on a click or tap anywhere outside the menu.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const onPointer = (event: PointerEvent) => {
      if (!menu.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);
  const initials = me.name
    .split(" ")
    .map((part) => part[0] ?? "")
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className="pw-profile" ref={menu}>
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
                <Icon name={t.icon} size={15} /> {t.label}
              </button>
            ))}
          </div>
          <button type="button" className="pw-button-quiet pw-signout" onClick={() => void signOut()}>
            <Icon name="sign-out" size={16} />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * Workspace shell (UX spec 2, 9): the warehouse beside the copilot or the inbox (D4), nothing
 * added to fill space.
 */
export function App() {
  const [authError] = useState(signInError);
  const [me, setMe] = useState<Me | null>(null);
  const [site, setSite] = useState<SiteStatus | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [panel, setPanel] = useState<Panel>("warehouse");
  // Wide screens: the map and one side panel (copilot or inbox), each switched on or off from the
  // view switch; at least one stays visible. Narrow screens show `panel` alone.
  const [showMap, setShowMap] = useState(true);
  const [side, setSide] = useState<Side | null>("copilot");
  const [wide, setWide] = useState(() => !narrowScreen());
  const workspace = useRef<HTMLElement>(null);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(ONE_PANEL);
    const update = () => {
      setWide(!query.matches);
    };
    query.addEventListener("change", update);
    return () => {
      query.removeEventListener("change", update);
    };
  }, []);

  useEffect(() => {
    if (authError !== null) return;
    const controller = new AbortController();
    const load = async () => {
      try {
        const user = await fetchMe(controller.signal);
        setMe(user);
        const first = user.sites[0];
        setSite(first ? await fetchSiteStatus(first.id, controller.signal) : null);
        setUnreachable(false);
      } catch (error) {
        // One sign-in page: the identity provider's PalletWise page with the form (no extra step).
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

  // The header's site time and data status follow the site stream (agent spec 9.2).
  const siteId = me?.sites[0]?.id ?? null;
  const statusChanged = useSiteChanges(siteId, ["clock", "data_status"]);
  useEffect(() => {
    if (!siteId || statusChanged === 0) return;
    const controller = new AbortController();
    fetchSiteStatus(siteId, controller.signal)
      .then(setSite)
      .catch(() => {
        /* the 15-second reload above retries */
      });
    return () => {
      controller.abort();
    };
  }, [siteId, statusChanged]);

  const choosePanel = (next: Panel) => {
    setPanel(next);
    if (next === "warehouse") setShowMap(true);
    if (next === "copilot" || next === "inbox") setSide(next);
    // Reveal the panel and move focus to its heading (UX spec 2).
    window.requestAnimationFrame(() => {
      const heading = document.getElementById(PANEL_HEADING[next]);
      heading?.scrollIntoView({ block: "nearest" });
      heading?.focus();
    });
  };

  /** A view switch click: on wide screens it shows or hides that panel (never the last one). */
  const toggle = (target: Exclude<Panel, "admin">) => {
    if (!wide || panel === "admin") {
      choosePanel(target);
      return;
    }
    if (target === "warehouse") {
      if (showMap && side) setShowMap(false);
      else choosePanel("warehouse");
    } else if (side === target && showMap) {
      setSide(null);
    } else {
      choosePanel(target);
    }
  };
  const shown = (target: Exclude<Panel, "admin">): boolean => {
    if (panel === "admin") return false;
    if (!wide) return panel === target;
    return target === "warehouse" ? showMap : side === target;
  };

  const siteInfo = me?.sites[0] ?? null;
  const notReady =
    site && site.data_status !== "READY" ? (NOT_READY_TEXT[site.data_status] ?? null) : null;

  // A sign-in that failed: why, and a way to try again.
  if (authError === REDIRECTING) return null;
  if (authError) return <SignIn error={authError} />;

  const shell = (
    <div className="pw-app">
      <header className="pw-topbar">
        <div className="pw-brand">
          <span className="pw-brand-logo" role="img" aria-label="PalletWise logo" />
          <span className="pw-brand-name">PalletWise</span>
        </div>
        {me && siteInfo && (
          <nav className="pw-nav" aria-label="Show panel">
            <button
              type="button"
              aria-pressed={shown("warehouse")}
              onClick={() => {
                toggle("warehouse");
              }}
            >
              <Icon name="map" size={16} /> Warehouse
            </button>
            <button
              type="button"
              aria-pressed={shown("copilot")}
              onClick={() => {
                toggle("copilot");
              }}
            >
              <Icon name="chat" size={16} /> Copilot
            </button>
            <button
              type="button"
              aria-pressed={shown("inbox")}
              onClick={() => {
                toggle("inbox");
              }}
            >
              <Icon name="inbox" size={16} /> Inbox
            </button>
            {me.role === "admin" && (
              <button
                type="button"
                aria-pressed={panel === "admin"}
                onClick={() => {
                  choosePanel("admin");
                }}
              >
                <Icon name="layers" size={16} /> Admin
              </button>
            )}
          </nav>
        )}
        <div className="pw-topbar-actions">
          {me && siteInfo && (
            <Search
              onAsked={() => {
                choosePanel("copilot");
              }}
            />
          )}
          {me && siteInfo && (
            <Bell
              siteId={siteInfo.id}
              inboxOpen={panel === "inbox"}
              onOpenInbox={() => {
                choosePanel("inbox");
              }}
            />
          )}
          {me && (
            <ProfileMenu
              me={me}
              siteName={siteInfo ? `${siteInfo.name} (${siteInfo.code})` : null}
            />
          )}
        </div>
      </header>

      {me && siteInfo && (
        <div className="pw-page-head">
          <div className="pw-page-title">
            <h1>{siteInfo.name}</h1>
            <p className="pw-muted">
              Live digital twin of {siteInfo.code}
              {site?.sim_time
                ? ` · site time ${formatSiteTime(site.sim_time, site.time_zone)}`
                : ""}
            </p>
          </div>
          <SimulationControls
            onAsked={() => {
              choosePanel("copilot");
            }}
          />
        </div>
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

      {me && siteInfo && panel === "admin" && <AdminPanel />}

      {me && siteInfo && panel !== "admin" && (
        <main
          ref={workspace}
          className="pw-workspace"
          data-panel={panel}
          data-map={showMap ? "on" : "off"}
          data-side={side ?? "none"}
        >
          <MapPanel />
          <Splitter workspace={workspace} />
          <CopilotPanel
            notReadyText={notReady}
            onOpenInbox={() => {
              choosePanel("inbox");
            }}
          />
          <InboxPanel
            onAskCopilot={() => {
              choosePanel("copilot");
            }}
            onShowOnMap={() => {
              // Beside the map on wide screens; on narrow ones, show the map.
              if (narrowScreen()) choosePanel("warehouse");
            }}
          />
        </main>
      )}
    </div>
  );

  // The workspace spans the header too: search asks the copilot like any other question.
  return me && siteInfo ? (
    <WorkspaceProvider
      me={me}
      siteId={siteInfo.id}
      dataReady={!notReady}
      siteDate={site?.sim_time ? siteDate(site.sim_time, site.time_zone) : null}
    >
      {shell}
    </WorkspaceProvider>
  ) : (
    shell
  );
}
