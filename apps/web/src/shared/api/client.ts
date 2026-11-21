import type { components } from "@palletwise/contracts";

type Schemas = components["schemas"];
export type SiteStatus = Schemas["SiteStatusOut"];
export type Me = Schemas["MeOut"];
export type Layout = Schemas["LayoutOut"];
export type LayoutBin = Schemas["BinOut"];
export type MapState = Schemas["MapStateOut"];
export type ResultEnvelope = Schemas["ResultEnvelope"];
export type Highlight = Schemas["Highlight"];
export type Conversation = Schemas["ConversationOut"];
export type ConversationDetail = Schemas["ConversationDetailOut"];
export type RunRecord = Schemas["RunOut"];
export type RunRequest = Schemas["RunIn"];
export type Starter = Schemas["SuggestionOut"];
export type PageContext = Schemas["PageContext"];
export type Inbox = Schemas["ExceptionsOut"];
export type InboxException = Schemas["ExceptionOut"];
export type Acknowledgement = Schemas["AcknowledgementOut"];
export type Brief = Schemas["BriefOut"];
export type SearchResult = Schemas["SearchResultOut"];
export type BriefItem = Schemas["BriefItemOut"];

/** Thrown when the session is missing or has ended; the shell sends the user to sign in. */
export class SignedOut extends Error {}

/** A refused request with the API's problem details (agent spec 12.1). */
export class ApiProblem extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly traceId: string | null,
  ) {
    super(message);
  }
}

interface ProblemBody {
  code?: string;
  detail?: string;
  trace_id?: string;
}

export async function problemFrom(response: Response): Promise<ApiProblem> {
  let body: ProblemBody = {};
  try {
    body = (await response.json()) as ProblemBody;
  } catch {
    /* not a problem document */
  }
  return new ApiProblem(
    response.status,
    body.code ?? "ERROR",
    body.detail ?? "The request could not be completed.",
    body.trace_id ?? null,
  );
}

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
  if (!response.ok) throw await problemFrom(response);
  return (await response.json()) as T;
}

async function send(path: string, body?: unknown, headers: Record<string, string> = {}) {
  const init: RequestInit = { method: "POST", headers: { ...headers } };
  if (body !== undefined) {
    init.headers = { ...headers, "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const response = await fetch(path, init);
  if (response.status === 401) throw new SignedOut();
  if (!response.ok) throw await problemFrom(response);
  return response;
}

async function post(path: string, body?: unknown): Promise<void> {
  await send(path, body);
}

const site = (siteId: string) => `/api/sites/${encodeURIComponent(siteId)}`;
const query = (siteId: string) => `site_id=${encodeURIComponent(siteId)}`;

export const fetchMe = (signal?: AbortSignal) => getJson<Me>("/api/me", signal);

export const fetchSiteStatus = (siteId: string, signal?: AbortSignal) =>
  getJson<SiteStatus>(`${site(siteId)}/status`, signal);

export const fetchLayout = (siteId: string, signal?: AbortSignal) =>
  getJson<Layout>(`${site(siteId)}/layout`, signal);

export const fetchMapState = (siteId: string, signal?: AbortSignal) =>
  getJson<MapState>(`${site(siteId)}/map-state`, signal);

export const fetchBinDetails = (siteId: string, code: string, signal?: AbortSignal) =>
  getJson<ResultEnvelope>(`${site(siteId)}/bins/${encodeURIComponent(code)}`, signal);

export const fetchConversations = (siteId: string, signal?: AbortSignal) =>
  getJson<Conversation[]>(`/api/copilot/conversations?${query(siteId)}`, signal);

export const fetchConversation = (siteId: string, id: string, signal?: AbortSignal) =>
  getJson<ConversationDetail>(
    `/api/copilot/conversations/${encodeURIComponent(id)}?${query(siteId)}`,
    signal,
  );

export const fetchStarters = (signal?: AbortSignal) =>
  getJson<Starter[]>("/api/copilot/starters", signal);

export const cancelRun = (siteId: string, runId: string) =>
  post(`/api/copilot/runs/${encodeURIComponent(runId)}/cancel?${query(siteId)}`);

export const sendFeedback = (
  siteId: string,
  runId: string,
  rating: "HELPFUL" | "NOT_HELPFUL",
  comment?: string,
) =>
  post(`/api/copilot/runs/${encodeURIComponent(runId)}/feedback?${query(siteId)}`, {
    rating,
    comment: comment ?? null,
  });

/** Site search (D8): SKUs, bin codes and order numbers, at most 8, best match first. */
export const searchSite = (siteId: string, q: string, signal?: AbortSignal) =>
  getJson<Schemas["SearchOut"]>(`${site(siteId)}/search?q=${encodeURIComponent(q)}`, signal);

/** Today's brief for the site: generated on the first request of the site day, then stored. */
export const fetchBrief = (siteId: string, signal?: AbortSignal) =>
  getJson<Brief>(`${site(siteId)}/brief`, signal);

export const fetchExceptions = (siteId: string, signal?: AbortSignal) =>
  getJson<Inbox>(`${site(siteId)}/exceptions`, signal);

/**
 * Acknowledges an exception (Managers only). The key correlates the request in the audit; a
 * repeat changes nothing (agent spec 16, 2026-11-21).
 */
export async function acknowledgeException(
  siteId: string,
  exceptionId: string,
): Promise<Acknowledgement> {
  const response = await send(
    `${site(siteId)}/exceptions/${encodeURIComponent(exceptionId)}/acknowledge`,
    undefined,
    { "Idempotency-Key": crypto.randomUUID() },
  );
  return (await response.json()) as Acknowledgement;
}
