import type { components } from "@palletwise/contracts";

export type SiteStatus = components["schemas"]["SiteStatusOut"];

/** Reads the current site's data status. Types come from the generated API contract. */
export async function fetchSiteStatus(signal?: AbortSignal): Promise<SiteStatus> {
  const response = await fetch("/api/sites/current/status", signal ? { signal } : {});
  if (!response.ok) {
    throw new Error(`Site status request failed: ${String(response.status)}`);
  }
  return (await response.json()) as SiteStatus;
}
