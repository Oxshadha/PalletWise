# UX Screens

| | |
|---|---|
| Status | Approved (2026-11-19, revision 2) |
| Owner | Product owner |
| Last updated | 2026-11-19 |
| Implements | FR-MAP-01 to FR-MAP-08, FR-COP-04, FR-COP-07 to FR-COP-14, NFR-UX-01 to NFR-UX-04 in [requirements.md](../product/requirements.md) |
| Design sources | [docs/design/mockup-light.png](../design/mockup-light.png), [docs/design/mockup-dark.png](../design/mockup-dark.png), `apps/web/src/shared/theme/tokens.css` |

## 1. Scope and principles

The Manager workspace as built in Slices 1 and 2: shell, warehouse map, copilot panel and answer cards. Inbox, brief, proposals and Admin screens get their own short sections when their slices start.

- **Two dominant elements.** The warehouse and the copilot. No KPI cards or widgets added to fill space.
- **Tokens only.** Colors, fonts, sizes, spacing and shadows come from `tokens.css` (including the existing `--pw-font-*` typography tokens); this spec names tokens, never values.
- **The UI never calculates.** Utilization, availability, demand and freshness come from the semantic layer through the result envelope (semantic spec 7.5). Statuses use the names of CWM 5.2 and 7.4 (`LOADING`, `READY`, `REBUILDING`, `FAILED`).
- **Later features are hidden, not disabled.**

## 2. Shell

| Element | Content and behaviour |
|---|---|
| Brand | Logo and "PalletWise", top left |
| View switch | Pill toggle **Warehouse** / **Copilot** / **Inbox** (Inbox from Slice 3, section 9a), top centre (behaviour per screen width in section 9). Activating it scrolls to or reveals the chosen panel and moves focus to that panel's heading. |
| Search | Icon button; opens a command box that finds SKUs, bins and orders and runs the matching fast-path question. Slice 2. |
| Notifications | Bell with a dot for new exceptions. Slice 4; hidden before. |
| Profile | Avatar menu: name, role, site, theme choice (Light, Dark, System), sign out |
| Data status | When the site is not `READY`, a banner explains it (`LOADING`, `REBUILDING`, `FAILED`) and the copilot input is disabled with the same text |

## 3. Warehouse panel

| Element | Content and behaviour |
|---|---|
| Header | "Warehouse", site name and code, for example "Peliyagoda Distribution Centre (PLG)" |
| 2D / 3D toggle | 3D by default. Switching preserves site, selected location, active highlight set, active answer and any filters. Both views render the same layout and location state; neither computes data of its own. |
| Controls | Recenter, zoom in, zoom out, each with an accessible name. Mouse: drag to pan (2D) or orbit (3D), wheel to zoom. Keyboard: arrows pan, `+` / `-` zoom, `0` recenters. |
| Locations list | A collapsible list of the highlighted (or, with no highlights, the selected zone's) locations next to the map: the keyboard and screen-reader path to every location without using the scene. `Tab` and arrow keys move through it; selecting an item selects the location on the map. |
| Scene | Generated from the layout endpoint (FR-MAP-01). Performance acceptance: 60 frames per second for the v1 site on the reference machine (NFR-PERF-03). |

**Interaction by input type**

| Input | Brief information | Details |
|---|---|---|
| Mouse | Hover shows a tooltip: code and main number | Click opens the location popover |
| Keyboard | Focus (via the locations list) shows the tooltip | `Enter` opens the popover |
| Touch | (none) | Tap opens the popover |

No information is available only through hover. The popover closes with `Escape`, its close button, or a click outside.

**Map states**

| State | Shown |
|---|---|
| Loading | Lightweight skeleton of the floor outline |
| Ready | Interactive scene |
| No layout | Message: no layout is available for this site |
| Failed | Readable message, retry button and trace id |
| 3D unavailable (no WebGL) | Message and automatic switch to the 2D view |

## 4. Location popover

Follows the "Rack B7" card in the mockups. All numbers come from the semantic layer (utilization: semantic spec 4.4, the larger of the volume and weight ratios; available space: canonical capacity rules).

| Element | Rack | Bin |
|---|---|---|
| Title | Rack code with icon | Bin code with icon |
| Main measure | Utilization percent with a bar | Utilization percent with a bar |
| Lines | Bins; bins with available space | Products with on hand, allocated, available; last movement |
| Actions | "View details" (the rack's bins), "Ask about this rack" | "Ask about this bin" (FR-MAP-07) |

"Ask about this ..." sends a question with the location as page context.

## 5. Map states and colors

One table, shared with the inbox and answer cards (agent spec 10). Every state has a pattern or icon, so color is never the only signal (FR-MAP-03).

| Channel | Meaning | Values |
|---|---|---|
| Base fill and pattern | Warehouse state | normal (`--pw-color-surface`, no pattern), empty (`--pw-color-background`, dashed outline), low stock (`--pw-color-status-warning`, diagonal hatch), over capacity (`--pw-color-status-critical`, cross hatch) |
| Badge | Proposal status | pending proposal icon (Slice 5) |
| Outline | Current copilot highlight | `info`, `warning`, `critical` in the matching status token; `critical` uses a thicker outline |

Layering: fill and pattern, then badge, then outline, so all three remain readable together. A highlight animates once when it appears (a short pulse), then stays as a steady outline. With `prefers-reduced-motion`, there is no animation in the map or the copilot.

## 6. Copilot panel

| Element | Content and behaviour |
|---|---|
| Header | Chat icon, "PalletWise Copilot", subtitle "Ask about your warehouse, inventory, orders or risks." |
| Starter suggestions | When the conversation is empty. Each runs on the fast path. A suggestion appears only when its capability is built: "Where is SKU-102 stored?" (Slice 1); "Which products could run short?", "Show capacity hotspots" (Slice 2); "Propose replenishment for low stock" (Slice 5, where it drafts a proposal for the Manager to review, never approves or executes). |
| Conversation | Questions right-aligned, answers as answer cards, auto-scroll unless the user has scrolled up |
| Input | "Ask a question..." and send; `Enter` sends, `Shift+Enter` adds a line; 1,000 characters with a counter near the limit |
| One run at a time | Per conversation. While a run is active the send button becomes **Stop**; sending a new question cancels the active run first (FR-COP-10). |
| Cancellation | Stops generation and any remaining tool calls on the server. A proposal already created before the cancel stays `PENDING` and is shown in the conversation; no new one is created after the cancel. |

**Conversation history (FR-COP-11)**

| Topic | Rule |
|---|---|
| Scope | Per signed-in user, tenant and site |
| Naming | The first question, shortened to 60 characters |
| New conversation | "New conversation" button in the panel header |
| Reopen | History menu in the panel header; restores messages and evidence |
| Old highlights | Can be shown again from a reopened answer, marked **"Historical: data as of ..."**, never presented as current state |
| Site change | Shows that site's conversations |

## 6a. Today's brief (Slice 3)

Per slice-3-acceptance D6 and step 5: a card at the top of the copilot panel, above the conversation.

| Element | Content and behaviour |
|---|---|
| Header | "Today's brief" and **Hide**. Hidden, it becomes a **Show today's brief** button; hidden is remembered for that site day only, so the next day's brief shows by itself. |
| Items | In order: Top exceptions, Orders at risk today, Yesterday's throughput, Data freshness. Each has its title, one sentence, and why it is in the brief (muted). |
| Actions | **Details** on every item: its stored rows and how they were computed. **Show on map** for an item with locations: exactly its highlights become the map's layer (FR-BRF-02). **Open the inbox** for the exceptions item: shows its locations and opens the inbox. The orders item's suggestion runs the copilot's fast-path answer for the same exact window (before midnight, site time). |
| Footer | "As of (site time)." and either "Worded by the copilot from these numbers." or "Standard wording." |
| Live | Refetched when the site clock or data status changes: the stored brief comes back unchanged during the day, and a new site day shows its new brief |

## 7. Answer card

The unit of every answer (FR-COP-04).

| Part | Content |
|---|---|
| Headline | One or two sentences, written by the model or the template (agent spec 3.2) |
| Map link | "Show on map" with the number of locations |
| Table | Up to 10 rows with "Show all"; numbers right-aligned in the KPI weight; the locations also listed here (accessibility) |
| How computed | Expandable, in plain language from the semantic layer's explanations and parameters, for example "Available stock is on hand minus active allocations", "Demand averaged over the last 28 operating days", "Festival uplift for Beverages: 60 percent". Internal tool names, raw arguments and identifiers are never shown to the Manager; they stay in the trace and the Admin audit view. |
| Freshness | From the evidence `data_as_of` (the oldest stream used), in the site's time zone: "Data as of 06:00, 1 Apr 2026 (Asia/Colombo)" |
| Suggestions | Two or three chips (agent spec 11) |
| Feedback | Helpful / not helpful with an optional comment (FR-COP-14) |

**Card types**, each visually distinct:

| Type | Meaning |
|---|---|
| Answer | A supported question answered |
| No results | Valid question, no matching data ("No items have been idle for more than 30 days") |
| Decline | Unsupported or unsafe to answer: the reason and supported alternatives as chips (FR-COP-08) |
| Stale or unavailable data | A stream is behind or never synced: the card says so and does not present the data as current (CWM 10.2) |
| Error | Unexpected failure: plain message and trace id (NFR-OBS-03) |

**Highlight lifecycle**

1. Each answer keeps its own highlight set.
2. The newest answer's set becomes active when its tool result arrives.
3. Selecting an older answer's "Show on map" makes its set active again.
4. Selecting a location on the map changes the selection only; the active highlight set and the answer history are unchanged.
5. "Clear highlights" removes the active highlight layer from the map; the answers and their evidence remain.

All interface text is free of em dashes and en dashes (FR-COP-13).

## 8. Accessibility

| Rule | Detail |
|---|---|
| Standard | WCAG 2.2 AA for every screen (NFR-UX-01) |
| Keyboard | Every action reachable; visible focus with `--pw-color-focus-ring` (NFR-UX-02) |
| Map | The locations list (section 3) and the answer table make every map result available without the scene; selected location details are announced |
| Live regions | Streaming answers polite; errors assertive |
| Contrast | **Decided: option (a).** A new token `--pw-color-accent-text`, darker than the accent, for normal-size text and links in both themes. `--pw-color-accent` stays for fills, icons, outlines and large text. Contrast is verified by an automated check of every token pair actually used (text on surface, text on background, labels on accent, links, focus ring, disabled text, status colors), not by relying on font weight. |

## 9. Screen sizes

Designed for 1920 by 1080; usable down to 1366 by 768 (NFR-UX-04), and down to 1024 with the single-panel layout.

| Width | Layout |
|---|---|
| 1600 px and wider | Both panels visible, balanced |
| 1200 to 1599 px | Both visible; the selected panel gets 55 percent, never below its minimum (map 560 px, copilot 380 px) |
| Below 1200 px | One panel at a time; the view switch toggles between them |

Breakpoints are checked in the Slice 1 layout tests and may be tuned once, with the change recorded here.

## 9a. Exceptions inbox (Slice 3)

Per slice-3-acceptance D4: a third item **Inbox** in the view switch. The inbox takes the column beside the map in place of the copilot; the copilot stays mounted, so an answer in progress keeps streaming. Below 1200 px one panel shows at a time, as for the other two.

| Element | Content and behaviour |
|---|---|
| Header | "Inbox" and the count, for example "3 open exceptions, ranked by impact" (a polite live region) |
| Row | Severity as icon and word (never color alone), the type in words, "Acknowledged" when it is, the subject in words, the score. Order and score exactly as the API sent them. |
| Choosing a row | The row expands and the map shows exactly that exception's highlights, with its reason and severity (FR-MAP-04); they replace the answer's highlights until another answer arrives, an answer is shown, the row is chosen again, or the exception resolves. On a narrow screen choosing shows the map. |
| Why it is ranked here | A table of the three factors: the raw value, the cap, the value scaled from 0 to 1 and the weight, with the score of 100 as caption (FR-EXC-02). Values as stored; the UI computes nothing. |
| Details | Orders affected, first detected and acknowledged times in site time |
| Actions | **Ask the copilot**: opens the copilot and asks "What should I do about the (type) for (subject)?" with the exception's id and type as page context. **Acknowledge**: Managers only (hidden for the Admin), shown while the exception is open; the list updates from the site stream. |
| Keyboard | `Tab` reaches the list; arrow keys, `Home` and `End` move between rows; `Enter` or `Space` chooses; `Tab` continues into the chosen row's actions |
| Live | The list refetches when the site stream reports `exceptions` or `data_status`: a new exception appears and a cleared one leaves without a reload |
| States | Loading; data not ready ("Exceptions appear when the warehouse data has loaded."); empty ("Nothing needs attention right now."); failed, with trace id and Try again |

## 10. Decisions

| # | Decision | Status |
|---|---|---|
| 1 | Both panels visible at full width; the switch reveals and focuses the chosen panel | Approved |
| 2 | 3D is the default map view, with automatic 2D fallback | Approved |
| 3 | Theme choice in the profile menu | Approved |
| 4 | Later features hidden, not disabled | Approved |
| 5 | Highlighted locations always also listed (answer table and locations list) | Approved |
| 6 | Accent contrast option (a): `--pw-color-accent-text` | Approved |
| 7 | Single-panel layout below 1200 px | Approved (revision 2) |
| 8 | Each answer keeps its own highlight set; older answers can restore theirs, marked historical when reopened | Approved (revision 2) |
| 9 | "How computed" in plain language; tool internals only in traces and Admin audit | Approved (revision 2) |

## 11. Change log

| Date | Change |
|---|---|
| 2026-11-19 | First draft. |
| 2026-11-19 | Revision 2 after one review round: three-tier responsive layout; map loading, empty, failure and no-WebGL states; input-specific interaction and popover closing; locations list for keyboard and screen readers; plain-language "how computed"; highlight lifecycle; single-pulse highlights; card types (answer, no results, decline, stale, error); one run per conversation and cancellation semantics; conversation history scope; accent text token decided. Already covered, no change: typography tokens (exist in tokens.css), cancel on new question (FR-COP-10). |
| 2026-11-19 | Slice 1 step 6: (1) Visual language after the product owner's reference video: frosted glass panels over a soft canvas, a centred pill navigation with icons, a large light page title, and an isometric warehouse model as the map's hero (floor slab, light racks, cartons for stocked locations, flat slots for empty ones, blue zone tags, pins with the location code on highlighted locations). (2) Typography: Inter Variable with optical sizing; page title, greeting and large numbers in Light (300) with tight tracking; section headings Medium (500); body Regular (400). This replaces the earlier Medium headings and numbers, at the product owner's request. (3) The 2D view is an SVG floor plan, so it also works when WebGL is unavailable (section 3); both views read the same layout, map state, selection and highlights. (4) When an answer is shown, other locations step back and highlighted ones take their severity color, with a marker and code. (5) Map states in Slice 1: stocked and empty; low stock and over capacity arrive with their rules in Slice 2. (6) New tokens: `--pw-color-accent-text` and `--pw-color-accent-strong` (filled buttons with text; the contrast check found white on the accent at 4.40:1), glass, deep panel and map material tokens; an automated test checks every text pair at 4.5:1 and marks at 3:1 in both themes. |
| 2026-11-19 | Slice 1 step 6, second pass after product owner feedback: (1) The 3D map is a realistic model generated from layout data only: concrete floor with painted zone lines, low walls, dock doors and the office from site features, pallet racking (uprights, beams, wire decking) from racks, a pallet with cartons for every stocked location; nothing is invented. (2) Game-style camera: eased movement, zoom to the pointer, orbit and tilt, buttons and keys (arrows pan, Q and E rotate, Page Up and Page Down tilt, plus and minus zoom, 0 recenters); framing keeps the isometric angle and the user's rotation. (3) With WebGL, 2D is the same scene seen from straight above with panning only (one scene, two views, FR-MAP-02); the SVG plan is the fallback without WebGL. (4) Hover shows a tooltip with the code and units; click opens the location details. (5) Interaction principles taken from the taste-skill library: every control answers hover and press with a 1 px step, one ease-out curve without bounce, text people read at 16 px, nothing under 13 px. |
| 2026-11-19 | Slice 2 design pass at the product owner's request, with the taste and stageshow skill rules that apply to an application: (1) No sparkle as AI decoration: the copilot header and the navigation use a chat icon. (2) One icon set (Lucide) at one stroke weight replaces hand-drawn glyphs. (3) Hierarchy: the answer leads; "Show on map" is a compact control with a map icon; evidence (freshness, how computed) sits below a hairline; suggestions are neutral chips; feedback is two icon buttons with accessible names "Helpful" and "Not helpful". The active answer is lifted by its shadow, not ringed. (4) Map side column: sections divided by space and a hairline instead of cards inside the panel; highlighted locations are plain rows with a severity bar, and a reason shared by every row is said once above the list. (5) 3D zone names are quiet tags at each zone's centre; a highlighted zone shows one tag with its code and name. (6) Camera controls are one glass rail at the bottom right. (7) The send button is neutral while there is nothing to send. |
| 2026-11-21 | Slice 3 step 4: section 9a, the exceptions inbox, as approved in slice-3-acceptance D4; the view switch gains Inbox. |
| 2026-11-21 | Slice 3 step 5: section 6a, today's brief in the copilot panel (product owner choice Q1 a). |
