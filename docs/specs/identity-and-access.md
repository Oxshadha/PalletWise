# Identity and Access

| | |
|---|---|
| Status | Approved (2026-11-19, revision 2) |
| Owner | Product owner |
| Last updated | 2026-11-19 |
| Implements | FR-AUTH-01 to FR-AUTH-04, NFR-SEC-01, NFR-SEC-02, NFR-SEC-03 in [requirements.md](../product/requirements.md) |
| Consumes | [agent-tools-and-api.md](agent-tools-and-api.md) sections 6 and 7, [canonical-warehouse-model.md](canonical-warehouse-model.md) section 11 |

## 1. Scope and layers

How people sign in, how sessions work, how tenant and site are bound to the signed-in user, how the copilot acts on a user's behalf, and how OPA decides. Needed by Slice 1.

Three layers, kept separate in implementation:

| Layer | Answers | Enforced by |
|---|---|---|
| Keycloak | Who is this person? | authentication, application roles, identity attributes |
| OPA | What may this person, or the agent acting for them, do? | scopes, agent status, site |
| PostgreSQL RLS | Which rows can this request touch? | tenant isolation at the database (CWM 11) |

A mistake in one layer is caught by the next: even if OPA wrongly allowed a cross-tenant read, RLS still returns nothing.

## 2. Sign-in (OIDC, run by the API as BFF)

Authorization code flow against Keycloak. The browser never sees an access, refresh or ID token (FR-AUTH-02).

| Step | Rule |
|---|---|
| Start (`GET /api/auth/login`) | The API creates a login attempt in `pw.web_login`: random `state` (256 bits), random `nonce`, PKCE `code_verifier` (S256 only, never `plain`), return path, expiry 10 minutes. Only the hash of `state` is stored as the key; a short-lived `__Host-pw_login` cookie binds the attempt to the browser. |
| Callback (`GET /api/auth/callback`) | Rejected unless: `state` matches an unexpired, unused attempt bound to this browser (then marked used); the redirect URI equals the configured callback exactly; the code exchange succeeds with the stored `code_verifier`. A reused code or state is rejected. |
| ID token validation | Signature against Keycloak's published keys, `iss` equals the realm issuer, `aud` contains `palletwise-bff`, `azp` equals `palletwise-bff` when present, `exp` and `iat` valid, `nonce` equals the attempt's nonce. Any failure rejects the sign-in. |
| Session creation | Only after a successful callback is a session created and its id issued. No session exists before authentication, so session fixation is not possible. |
| Other endpoints | `POST /api/auth/logout`, `GET /api/me` (user, role, sites; never tokens) |

## 3. Session

| Topic | Rule |
|---|---|
| Store | `pw.web_session`: session id hash (SHA-256 of a 256-bit value from a cryptographically secure generator), subject, role, tenant, sites, refresh token encrypted with AES-256-GCM (with key id, for rotation), created, last seen, expires. Written only through `pw.create_session()`, `pw.touch_session()`, `pw.refresh_session()`, `pw.end_session()` (owner functions, CWM 11.4). |
| Cookie | `__Host-pw_session`: HttpOnly, Secure, SameSite=Lax, Path=/. The `__Host-` prefix guarantees no `Domain` attribute. Holds only the session id. |
| Expiry | Idle 30 minutes, absolute 12 hours (settings), **checked on every authenticated request**. The cleanup sweep is housekeeping only. |
| Token refresh | Keycloak access tokens live 5 minutes. When a server-side operation needs a fresh user access token, the API refreshes with the stored refresh token. **Refreshes are serialized per session** (row lock on the session during refresh), so concurrent requests never race on a rotating refresh token. |
| Revalidation | Every refresh re-reads roles, tenant and sites from the new tokens. A lost role, a changed tenant or a disabled account ends the session; changed sites are updated in place. Revocation therefore takes effect within 5 minutes. |
| Secrets in telemetry | Tokens, refresh tokens, session ids and encryption keys never appear in logs, traces or audit entries. |
| Sign-out | Ends the local session, clears the cookie, revokes the refresh token at Keycloak and starts RP-initiated logout. |

## 4. Roles, tenant and sites

- **Application roles** are Keycloak **client roles of `palletwise-bff`**: `warehouse_manager` and `admin`. Other realm or client roles are ignored. A user must hold exactly one of the two application roles; none or both is refused with a clear message (FR-AUTH-03).
- **Tenant and sites** come from the user attributes `pw_tenant` and `pw_sites`, mapped into the tokens. Keycloak's user profile makes both **editable by realm administrators only**; users cannot change their own. An empty site list means no access.
- **Tenant binding (closes the Slice 0 security review note).** Every request takes tenant and sites from the session, never from the request. The API sets `app.tenant_id` from the session only; no endpoint accepts a tenant id. A site id in a path must be in the session's sites, otherwise 404 (agent spec 12.1). Supplying a new site id never grants access.

## 5. The copilot acting on a user's behalf

| Step | Rule |
|---|---|
| Agent identity | Keycloak client `palletwise-copilot` (confidential). PalletWise record: **agent registry** row in `pw.agent` with id, version, status (`ACTIVE`, `SUSPENDED`), allowed scopes, primary and fallback model, prompt version, max steps, daily token budget. Slice 1 seeds one row; the Admin UI arrives in Slice 7. |
| Token exchange | At the start of a copilot run the API obtains a fresh user access token by server-side refresh (section 3), then exchanges it for a token issued to `palletwise-copilot`. The exchanged token is validated for issuer, audience and client. The API never accepts a subject or agent identity from the request. |
| Failure | If refresh or exchange fails, the run is denied. There is no fallback to a service account. |
| Delegation claim | **Verified in Slice 1 (Keycloak 26.4.7): standard token exchange issues no RFC 8693 `act` claim.** The API therefore records the acting agent itself: subject from the exchanged token, `azp` must be `palletwise-bff`, `aud` must be `palletwise-copilot`, and the agent identity and version come from the registry. The policy input is unchanged. |
| Copilot audience | Keycloak issues the `palletwise-copilot` audience only to a client mapped to it. The realm gives `palletwise-bff` the **optional** client scope `copilot-audience`, requested only in the exchange call, so ordinary user tokens never carry the copilot audience. |
| Effective rights | The user's scopes **intersected with** the agent's allowed scopes, only while the agent is `ACTIVE` (NFR-SEC-02). Agent scopes can only remove rights, never add them. |

## 6. Authorization (OPA)

**Role to scope map**, kept in the policy's data (policy as code, covered by Rego tests). The API sends only the verified role; it never computes or accepts scopes.

| Scope | Warehouse Manager | Admin |
|---|---|---|
| `inventory.read`, `orders.read` | yes | yes (read-only workspace) |
| `proposal.create` | yes | no |
| `proposal.approve`, `proposal.reject` | yes | no |
| `admin.users.read`, `admin.agents.manage`, `admin.settings.manage`, `admin.audit.read`, `admin.usage.read`, `admin.simulation.control` | no | yes |

**Policy input**, built by the server from the session, the registry and the resolved resource:

```json
{
  "user": {"sub": "...", "role": "warehouse_manager", "tenant": "...", "sites": ["..."]},
  "agent": {"id": "copilot", "version": "1", "status": "ACTIVE", "scopes": ["inventory.read"]},
  "action": {"name": "find_product_locations", "scope": "inventory.read"},
  "resource": {"tenant": "...", "site": "..."}
}
```

`agent` is absent for actions a person performs directly, such as approving a proposal.

**Rules.** Allow only if all hold:

1. the user's role grants the scope;
2. the resource's tenant equals the user's tenant and its site is in the user's sites;
3. when an agent acts: the agent is `ACTIVE` and its scopes include the scope;
4. when an agent acts, the scope is not `proposal.approve`, `proposal.reject` or any `admin.*` scope, **whatever the registry says**. The copilot can never approve, reject or administer (separation of duties, FR-PRP-08).

Default deny. **Fail closed:** if OPA or the agent registry cannot be reached, the request is denied. The agent's status and the decision are evaluated **on every tool call**, not once per run (agent spec 6), so suspending the agent stops a run at its next step. Every decision is audited.

## 7. Browser request protection

| Check | Rule |
|---|---|
| CSRF | SameSite=Lax cookies, plus an `Origin` check on every state-changing request: a missing or foreign `Origin` is refused. `Sec-Fetch-Site`, where the browser sends it, is checked as a supplement, never as a replacement. |
| Session in URLs | Never. |

## 8. Tests

| Test | Expected |
|---|---|
| No session | 401 on every API endpoint except login, callback and health |
| Tokens never reach the browser | No token in any response body, URL or header other than the session cookie |
| Invalid or reused state | Callback rejected |
| Invalid nonce, wrong audience, wrong issuer, bad signature | Sign-in rejected |
| Replayed authorization code, missing PKCE verifier | Sign-in rejected |
| Role mapping | Manager and Admin get exactly their scopes; no or both application roles refused; unrelated roles ignored |
| Role revoked during a session | Access lost at the next refresh |
| Site access revoked | That site returns 404 after the next refresh |
| Tenant attribute changed | Session ended |
| Tenant binding | A request cannot change its tenant; another tenant's site id returns 404 |
| Agent intersection | A scope the user has but the agent lacks is denied, and the reverse |
| Copilot approves or administers | Always denied, even if the registry lists the scope |
| Agent suspended during a run | Next tool call denied |
| OPA or registry unavailable | Denied |
| Concurrent refresh | One refresh per session; no failed requests from token rotation |
| CSRF | Missing or foreign `Origin` on a state-changing request refused |
| Session expiry | Idle and absolute timeouts enforced on the request, not by the sweep |
| Sign-out | Session row gone, refresh token revoked, Keycloak logout started |
| RLS backstop | A query with a mismatched tenant context returns no rows |
| OPA | Rego unit tests for every row of the scope table and every deny reason |

## 9. Decisions

| # | Decision | Status |
|---|---|---|
| 1 | Server-side sessions in PostgreSQL behind owner functions; only a hashed session id in a `__Host-` cookie | Approved |
| 2 | Tenant and sites from admin-only Keycloak attributes, revalidated at every refresh; never from a request | Approved |
| 3 | CSRF protection by SameSite=Lax plus a required Origin check, with Sec-Fetch-Site as a supplement | Approved |
| 4 | Agent registry table introduced in Slice 1 with one seeded row | Approved |
| 5 | Fresh user token by server-side refresh, then token exchange; `act` claim verified first with a fallback that keeps the policy input identical | Approved |
| 6 | Application roles are client roles of `palletwise-bff` | Approved (revision 2) |
| 7 | The role to scope map lives in OPA policy data, not in the API | Approved (revision 2) |
| 8 | Revocation through 5-minute access tokens and revalidation on refresh, without a periodic job | Approved (revision 2) |

## 10. Change log

| Date | Change |
|---|---|
| 2026-11-18 | First draft. |
| 2026-11-19 | Slice 1 step 1 findings (no design change): no `act` claim from Keycloak, documented fallback in use; optional `copilot-audience` scope added to the realm. Also fixed a Slice 0 bug: the realm file used `${env.X}` placeholders, which Keycloak does not substitute, leaving literal client secrets and dev passwords; the syntax is `${X}`. |
| 2026-11-19 | Revision 2 after review: OIDC state, nonce, PKCE and token validation with a login attempt table; fresh user token by refresh for token exchange; failure means deny; client roles; attribute lifecycle and revalidation on refresh; AES-GCM refresh tokens with key ids; refresh serialization; expiry checked per request; explicit copilot prohibition on approval and admin scopes; fail closed when OPA or the registry is unavailable; resource tenant in the policy input; three-layer model; extended tests. Not adopted: computing scopes in the API (the map lives in OPA policy data instead); a separate `Domain` prohibition (the `__Host-` prefix already guarantees it). |
