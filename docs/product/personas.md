# PalletWise: Personas

| | |
|---|---|
| Status | Approved (2026-11-18) |
| Owner | Product owner |
| Last updated | 2026-11-18 |
| Related | [vision.md](vision.md), [requirements.md](requirements.md) |

## 1. Scope

v1 has two personas and two roles, mapped one to one. Personas describe people and their work. Roles describe permissions. Keeping them aligned in v1 keeps authorization simple and testable.

| Persona | Role key | Primary surface |
|---|---|---|
| Warehouse Manager | `warehouse_manager` | Manager workspace (map, copilot, brief, exceptions, proposals) |
| Admin | `admin` | `/admin` area, plus read-only use of the workspace and copilot |

Personas deferred to later releases: floor operator on a handheld device, shift supervisor as a separate role, executive with a multi-site view, external auditor.

## 2. Operating context

The v1 reference site is a single distribution warehouse that supplies retail outlets on fixed delivery windows (from `General Data/outlets.csv`). Many outlet windows open early in the morning, so problems must be visible before the first wave leaves. Demand rises around festivals and paydays and is affected by the monsoon (from `General Data/calendar.csv`).

Both personas work at a desk on a laptop or desktop screen (1080p or larger). Mobile and tablet layouts are not v1 targets.

## 3. Persona: Warehouse Manager

### Who they are

Responsible for the day-to-day performance of one warehouse: stock availability, on-time dispatch to outlets, space usage and inventory accuracy. Knows the warehouse layout and the product range well. Is not a data analyst and does not write queries. Is interrupted often and makes many small decisions under time pressure.

### Goals

1. Every outlet order leaves complete and on time.
2. Pick faces never run empty during a wave.
3. Space is used well: fast movers near dispatch, slow and dead stock out of prime locations.
4. Inventory records match what is physically in the bins.
5. Answer questions from supervisors, sales and head office quickly and with confidence.

### Frustrations today

- Has to open several WMS screens and spreadsheets to answer one question.
- Reports show bin codes, not where the problem physically is.
- Finds out about shortages when a picker reports an empty bin.
- Does not trust numbers that cannot be traced back to their source.
- Has no time to read long reports. Wants the answer first and the detail only when needed.

### A typical day

| When | What they do | What PalletWise gives them |
|---|---|---|
| Before the first wave | Check what could go wrong today | Daily brief: orders at risk in today's windows, top exceptions, pending proposals |
| Start of shift | Fix the urgent problems | Exceptions inbox ranked by impact, map highlights, replenishment proposals to approve |
| During the day | Answer ad hoc questions, handle surprises | Copilot answers with map highlights, click a bin to ask about it, follow-up suggestions |
| Afternoon | Prepare for tomorrow and upcoming peaks | Questions about days of cover, festival demand, capacity hotspots |
| End of week | Review space and accuracy | Dead stock, slow movers in prime locations, cycle-count proposals |

### Questions they ask (examples)

These examples seed the evaluation set and must each map to a supported tool or query.

| Job | Example questions |
|---|---|
| Find stock | Where is SKU-102 stored? What is in bin B2-03-02? |
| Protect today's orders | Which of today's orders are at risk? Which outlet orders are delayed? |
| Keep pick faces full | Which items are running low on stock? Which items need replenishment before today's first dispatch window? |
| Understand movement | Show me the top 5 fastest-moving items. Which items have not moved in the last 30 days? |
| Manage space | Which zones have the highest stock levels? Where are we close to capacity? Are slow movers sitting in the fast-pick zone? |
| Plan ahead | How many days of cover do we have for the beverages category? Which items could run short before the upcoming festival? |
| Follow up | Of those, which are in zone B? Show only the ones worth more than 50,000. |

### What they can do

- View the map in 2D and 3D, the daily brief, the exceptions inbox and answer cards.
- Ask the copilot any supported question.
- Ask the copilot to draft a replenishment or cycle-count proposal.
- Approve or reject proposals, with a reason required for rejection.

### What they cannot do

- Change thresholds, agents, users, layout or connector settings.
- Change the simulated clock, reset the dataset or trigger simulation scenarios.
- View the audit log of other users or the usage and cost dashboard.

### Design implications

- **Answer first.** Every answer starts with the headline result and the map highlight. Tables and evidence are one click away.
- **Minimal typing.** Suggestion chips and click-to-ask cover the most common needs.
- **Trust cues on every answer.** How it was computed, data freshness, and a clear statement when the copilot cannot answer.
- **One screen.** The manager should not need to leave the workspace for daily work.

## 4. Persona: Admin

### Who they are

Responsible for running PalletWise safely and correctly for the organisation. Typically from IT or operations systems. Comfortable with configuration, access control and logs. Not responsible for warehouse decisions.

### Goals

1. The right people have the right access, and nothing more.
2. The AI agent behaves within its approved limits and can be stopped immediately if needed.
3. Business thresholds reflect how the warehouse actually operates.
4. Any answer or action can be traced: who asked, what the agent did, which data it used.
5. Model cost stays within budget.
6. The copilot improves over time, based on what users could not get answered.

### Frustrations today

- AI tools that behave like black boxes, with no record of what they did.
- Cost that is only visible on the monthly invoice.
- No way to switch off a misbehaving feature without a deployment.

### What they can do

- Manage users and role assignments (through the identity provider).
- Manage the agent registry: allowed tools and scopes, model selection, step limits, daily token budget, and the kill switch.
- Set business thresholds: low-stock rules, dead-stock period, exception ranking weights.
- Manage the warehouse layout, simulated data settings and simulator configuration. Real WMS connector configuration is added when integrations become available in a later release.
- View the full audit log, usage and cost dashboard, and unanswered questions.
- Use the simulation controls: play, pause, speed, jump, reset, scenarios, guided tour.
- Use the workspace and copilot read-only.

### What they cannot do

- Approve or reject operational proposals. This separation of duties ensures that the person who configures the system is not the person who authorizes stock actions.

### Design implications

- **Dense, filterable views.** Audit and usage screens use tables with filters and export.
- **Safe changes.** Destructive or high-impact actions (kill switch, budget changes, simulation reset, jumping the simulated clock, running a scenario) require confirmation and are audited, because they change what the Manager sees.
- **Explain the effect.** Each setting states what it changes in the manager's experience.

## 5. Permission summary

| Capability | Warehouse Manager | Admin |
|---|---|---|
| View map, brief, exceptions | Yes | Yes (read-only) |
| Ask the copilot | Yes | Yes (read-only) |
| Request a proposal draft | Yes | No |
| Approve or reject proposals | Yes | No |
| Manage users and roles | No | Yes |
| Manage agent registry and kill switch | No | Yes |
| Set business thresholds and ranking weights | No | Yes |
| Layout, simulated data and simulator settings (real connectors in a later release) | No | Yes |
| View audit log | Own activity only | Full |
| View usage and cost | No | Yes |
| View unanswered questions | No | Yes |
| Simulation controls | No | Yes |

Every permission above is enforced on the server at the tool or endpoint boundary. Hiding a button in the interface is never the control.

## 6. Open questions

None at this time.
