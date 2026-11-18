# PalletWise: Product Vision

| | |
|---|---|
| Status | Approved (2026-11-18) |
| Owner | Product owner |
| Last updated | 2026-11-18 (amended: simulator execution of approved proposals, see requirements Q1) |
| Related | [personas.md](personas.md), [requirements.md](requirements.md), [canonical-warehouse-model.md](../specs/canonical-warehouse-model.md) |

## 1. Vision statement

PalletWise is a WMS-agnostic warehouse intelligence platform with a governed conversational agent, an interactive 2D/3D warehouse digital twin, and human-controlled operational workflows.

It tells a warehouse manager what needs attention, shows exactly where it is on the floor, explains why, and prepares the next action for a human to approve.

## 2. The problem

Warehouse managers already have a WMS, but a WMS records transactions. It does not tell them what matters today.

- **Answers are scattered.** Stock levels, movements, orders and locations live on different screens and reports. A simple question such as "which of today's orders are at risk?" means opening several screens and combining the results by hand.
- **Spatial context is lost.** Reports list bin codes. The manager still has to work out where those bins are and whether they are near each other.
- **Problems are found late.** Low pick-face stock, dead stock in prime locations and delayed orders are discovered when they already hurt, not when they could still be prevented.
- **Generic AI chat is not trusted.** A chatbot that writes its own SQL, invents numbers or acts without approval is not acceptable in an operational system.

## 3. Who it is for

| Persona | Role in v1 |
|---|---|
| Warehouse Manager | Primary user. Monitors the warehouse, asks questions, works the exceptions, approves or rejects proposals. |
| Admin | Configures and governs the platform: users, agents, thresholds, layout and data, audit, usage and cost, simulation controls. Cannot approve operational proposals. |

Details are in [personas.md](personas.md).

## 4. Product principles

These principles guide every specification and design decision. If a proposal conflicts with one of them, the principle wins unless the product owner explicitly changes it.

1. **The LLM is not the authority.** Business logic, permissions, calculations and data access belong to deterministic application services. The model interprets questions, selects approved capabilities and explains results. It never writes SQL, grants itself permissions or invents numbers.
2. **The warehouse model is the foundation.** Every data source, including the simulator, normalizes into one canonical warehouse model. Features are built on that model, never on a specific WMS.
3. **The map and the copilot are one experience.** Answers, locations, exceptions and proposals stay connected. An answer highlights the map, and selecting something on the map gives the copilot its context.
4. **Tell, do not make them ask.** The most valuable insight is the one the manager did not have to request. The daily brief and the exceptions inbox come first, chat is there for everything else.
5. **Agents propose, humans approve.** The agent can draft operational actions. Only an authorized human can approve them, and every step is audited.
6. **Every answer shows its evidence.** Each answer states how it was computed and how fresh the data is. Unanswerable questions are declined honestly, never guessed.
7. **Governed by design.** Identity, authorization, tenant isolation, audit, evaluation and observability are part of the first slice, not added later.

## 5. What PalletWise does (v1)

- **Warehouse digital twin.** An interactive map of the site with a 2D and a 3D view of the same scene, generated from the location data. Bins are colored by state and highlighted by answers.
- **Governed copilot.** Natural-language questions answered through approved tools and a controlled query language, with results shown as answer cards and map highlights.
- **Context-aware suggestions.** After each answer the copilot offers next questions and actions that the system is guaranteed to support.
- **Exceptions inbox.** Problems ranked by business impact, each with a visible reason for its ranking: low stock against open orders, delayed orders, dead stock, capacity hotspots.
- **Daily brief.** A short summary when the manager signs in: top exceptions, orders at risk today, yesterday's throughput, pending proposals, data freshness.
- **Proposals.** Replenishment and cycle-count proposals drafted by the agent with an evidence snapshot, approved or rejected by the manager. Approved proposals are executed by the simulated warehouse, and the map and exceptions update from the resulting state.
- **Admin governance.** Users and roles, agent registry with a kill switch, business thresholds, audit log, usage and cost, unanswered questions.
- **Simulation controls.** In v1 the workspace runs on one simulated warehouse. Admin can play, pause, change speed, jump in time, reset to the seed and run scripted scenario events, with an optional guided tour for presentations. The simulator is a data source, not a separate application mode: the agent, tools, authorization, audit, visualization and evaluations follow exactly the same path as with any other data source.

## 6. What PalletWise is not (non-goals)

- **Not a WMS replacement.** It does not run receiving, putaway, picking, packing or shipping. It reads from the WMS and proposes actions to it.
- **No execution against a real WMS in v1.** v1 supports human-approved execution against the simulated warehouse only. It does not execute transactions against a real WMS. The agent cannot independently approve or execute operational proposals.
- **No free-form SQL.** The model cannot write or run arbitrary queries.
- **Not a general chatbot.** Questions outside warehouse operations are declined.
- **Out of v1 scope:** handheld floor-operator app, executive multi-site view, lots and expiry, serial numbers, handling units, labour management, transport and route planning.

## 7. How it connects to other systems

- **Data in.** Connectors bring WMS data into the canonical warehouse model. In v1 the only connector is the simulator, which behaves like an external WMS and uses the same connector interface a real one will use.
- **Simulation versus real data.** Simulation (scenario events, speed, jump, reset) only ever operates on simulated data. Real WMS data cannot be fast-forwarded, reset or injected with simulated events. Historical replay of real data is read-only. When a customer with a real connector wants demonstrations alongside their real warehouse, a separate demo tenant is provided (v2).
- **Insight out (later phases).** An embeddable copilot widget, a Copilot API, webhooks for proactive alerts, and an MCP server that lets a customer's own AI agent use PalletWise insights. All of them are built on the same governed tool registry.

## 8. How we measure success

| Area | Measure |
|---|---|
| Speed | Time from question to highlighted answer. Time from exception raised to proposal approved. |
| Accuracy | Evaluation pass rate: correct tool and parameters, correct highlights (precision and recall), correct abstentions. |
| Trust and safety | Zero cross-tenant data access. Zero unauthorized tool executions. Prompt-injection test pass rate. |
| Usefulness | Proposal acceptance rate. Share of exceptions resolved before they cause a late order (measured in simulation). |
| Cost | Model cost per answered question and per active user per day. |
| Coverage | Share of questions answered versus declined, with declined questions reviewed and turned into new capabilities. |

## 9. Release outline

| Release | Scope |
|---|---|
| v1 | Manager workspace (map, copilot, brief, exceptions, proposals), Admin area, one simulated warehouse with simulation controls and scenarios, simulator connector, human-approved proposals executed by the simulator only. |
| v1.1 | Saved questions as alerts, heatmap views, read-only movement time scrubber. |
| v2 | First real WMS connector, execution of approved proposals against a real WMS, separate demo tenant for customers with real data, embed widget, Copilot API, webhooks, MCP server, SOP knowledge search. |
| Later | Handheld operator experience, multi-site executive view, slotting and forecasting optimization. |

## 10. Constraints

- **Data.** Development and demo data are synthetic. The only external inputs are `General Data/calendar.csv` (demand seasonality: festivals, paydays, monsoon, operating days) and `General Data/outlets.csv` (outbound customers and delivery windows). All generated records carry a dataset version and a synthetic label.
- **Models.** Gemini Flash models on Vertex AI, behind a provider interface so the model can change without code changes.
- **Secrets.** No credentials in the repository. Local development reads them from the environment, deployed environments use workload identity.

## 11. Open questions

None at this time. New questions are added here and resolved before the dependent specification is approved.
