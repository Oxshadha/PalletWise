import type { components } from "@palletwise/contracts";

export type SiteStatus = components["schemas"]["SiteStatusOut"];
export type Me = components["schemas"]["MeOut"];

/** Thrown when the session is missing or has ended; the shell sends the user to sign in. */
export class SignedOut extends Error {}

/** Starts sign-in at the API (BFF). The browser never handles tokens. */
export function signIn(): void {
  const here = window.location.pathname + window.location.search;
  window.location.assign(`/api/auth/login?return_to=${encodeURIComponent(here)}`);
}

export async function signOut(): Promise<void> {
  // Same-origin fetch: the browser sends the Origin header the API requires.
  await fetch("/api/auth/logout", { method: "POST" });
  window.location.assign("/");
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, signal ? { signal } : {});
  if (response.status === 401) throw new SignedOut();
  if (!response.ok) throw new Error(`Request failed: ${String(response.status)}`);
  return (await response.json()) as T;
}

export function fetchMe(signal?: AbortSignal): Promise<Me> {
  return getJson<Me>("/api/me", signal);
}

/** Reads a site's data status. Types come from the generated API contract. */
export function fetchSiteStatus(siteId: string, signal?: AbortSignal): Promise<SiteStatus> {
  return getJson<SiteStatus>(`/api/sites/${encodeURIComponent(siteId)}/status`, signal);
}
