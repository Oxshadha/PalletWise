# 0001. Balanced answer runtime: quality, latency and cost gated together

## Context

Measured on the live stack and the model gate (2026-11-19, `gemini-3.8-flash`, prompt v2):

| Measure | Path A | Path C | Target before this ADR |
|---|---|---|---|
| Complete answer p50 | 2.2 s | 6.1 s | |
| Complete answer p90 | 5.6 s | 19.2 s | p95 at most 8 s (NFR-PERF-02) |
| Worst run | 9.3 s | 43 s (one run failed at the run timeout) | |
| First text p95 (model gate) | | 17.6 s | at most 2 s (NFR-PERF-02) |
| Estimated cost | about 0.001 USD per answer, about 750 input tokens per run | | no limit defined |
| Quality (model gate) | every gated metric 1.0 | | NFR-QLT-02 |

Quality is met, cost is low, and latency fails NFR-PERF-02. Latency was reported but never gated, so
the miss was not caught. The causes, from traces:

1. **Two model round trips in series on path C:** one to choose the tool, one to word the answer,
   both on the primary model.
2. **No early content:** nothing but "Answering..." appears until the full text is ready, so the
   first text is the complete answer.
3. **Provider tail latency:** a primary call can hang about 20 s before the transport error moves
   it to the fallback, after which the run timeout ends it.
4. **Thinking tokens** on a call that only has to choose a tool and its arguments.

Context size is not a cause: the prompt is small, and provider prompt caching was measured as not
applicable at this size (agent spec 14b).

Each of the obvious single fixes moves one measure at the expense of another:

- template text only: fast and cheap, but the answer quality drops;
- a bigger or thinking model: better quality, but slower and dearer;
- always calling two models in parallel: faster in the tail, but double the cost.

## Decision

### 1. One balanced gate

A change to the runtime, prompts, models or tools is accepted only when the **same evaluation
run, on the same cases and workload,** passes all three gates. Improving one measure never excuses
a regression in another.

| Gate | Threshold |
|---|---|
| Quality | NFR-QLT-02 unchanged: tool and parameter accuracy at least 95 percent, highlight F1 at least 0.95, correct decline at least 90 percent, injection resistance 100 percent. Grounding violations 0. **Required facts 100 percent** (section 4). |
| Latency, model path (NFR-PERF-02, amended) | first progress p95 at most 1 s; first map highlights p95 at most 3 s; first headline p95 at most 4 s; complete answer p95 at most 8 s |
| Latency, fast path | first map highlights p95 at most 1 s; complete answer p95 at most 3 s |
| Cost (NFR-CST-03, new) | estimated model cost per answer p95 at most 0.003 USD, a fixed hard limit. Comparison with the baseline is reported as context and never decides the gate. |

**Progress is not an answer.** The four latency milestones are measured independently: a progress
event never counts as the headline, and the headline never counts as the complete answer.

Definitions, measured from the moment the client sends the question, so pre-checks (session, rate limit, data status, token exchange) count against every milestone:

| Milestone | Event |
|---|---|
| First progress | first `pw.status` event |
| First map highlights | first `pw.result` event with at least one highlight; runs whose answer has no highlights are excluded from this milestone and counted |
| First headline | first `TEXT_MESSAGE_CONTENT` event (template headline or model wording, whichever comes first) |
| Complete answer | `RUN_FINISHED` |

### 2. Runtime changes

Each change is kept only if the comparison (section 3) shows the combined gate still passes.

| # | Change | Conditions |
|---|---|---|
| 1 | **Live progress.** A `pw.status` event with a plain-language stage ("Reading the question", "Checking stock levels", "Writing the answer"), sent as each stage starts. | Stage labels come from a fixed list per stage and per tool. They never contain model reasoning, prompts, raw tool names, arguments or identifiers. |
| 2 | **The right model for each step.** Tool choice stays on the primary model. Path C wording moves to Flash-Lite. | Wording quality is evaluated on path C itself (section 3): required facts, grounding, and a paired human review sample. The fast path passing the gate does not count as proof for path C. |
| 3 | **Several tools in one turn.** The model may request independent tools together, and they run concurrently. | Only read-only tools run in parallel; propose tools (Slice 5) never do. Each call still goes through `tools.executor` on its own (validation, kill switch, policy, audit), and each counts toward the five-call limit. |
| 4 | **Minimal thinking for tool choice.** | Kept only if tool and parameter accuracy stay at or above their thresholds. |
| 5 | **Hedged tool-choice calls.** If the primary model has not answered within its soft deadline (the healthy p95, about 4 s), the fallback starts in parallel. | Only model responses race; tool execution never does. Exactly one response is accepted: the first one that completes. Its tool name and arguments then go through the normal validation and authorization path, like any other model response, so a response that fails validation is a failed answer, not a reason to take the other. The losing call is cancelled where the provider allows it, and its cost is always counted (section 5). |
| 6 | **Headline first.** When the tool returns, a template headline is sent with the highlights and the table. | Rules in section 6. |
| 7 | **Wider fast path from the evaluation data.** Exact phrasings that the model always routes the same way move to `fast_path.yaml`. | Every new route ships with evaluation cases for the plain phrasing and for qualified variants. A qualified question ("in zone B") must not match, which the exact-match rule guarantees and the cases prove. |
| 8 | **Wording cache** (agent spec 14b). | Verified: the approved key already includes the result hash, prompt version and normalized arguments, and decision D4 adds the settings version. The lookup happens only after this user's authorized tool call has run on current data, so the cache never skips authorization or data retrieval. |

### 3. Baseline versus new runtime

Implementation does not start until the evaluation can measure, on the same cases, quality, all four
latency milestones and total cost for both configurations.

- **Configurations.** The runtime gains a configuration record (wording model, thinking level, hedge
  deadline, parallel tools, headline first). The baseline is today's settings, and the candidate is
  the new one. Both run through the same API, executor, database and seed.
- **Same workload.** Both configurations run the frozen case sets (by hash), the same dataset
  version, 3 runs per case, in the same session, interleaved case by case (baseline, candidate,
  baseline, ...), so provider load and time of day affect both alike.
- **Per run, recorded:** path, outcome, tool and parameters, highlights, answer text, facts found,
  grounding result, the four milestone times, a stage breakdown (pre-checks, tool choice, tool,
  wording, guardrails), and every model call with its role (tool choice, wording, hedge), model,
  input, output and cached tokens, whether it was accepted, cancelled or discarded, and its cost.
- **Report:** one table per configuration with quality, latency (p50 and p95 per milestone) and cost
  (p50, p95 and total), the pass or fail of each gate, the per-case differences, and the unstable
  cases. Stored in `docs/quality/eval-reports` with the case-set hash and both configurations.
- **Wording quality for path C** (change 2): the facts and grounding gates on every run, plus 20
  cases chosen in advance, reviewed by a person who sees the baseline and candidate wording side by
  side without knowing which is which (evals spec 6: no LLM judge in v1).

### 4. Required facts

Required facts become a hard gate at 100 percent, replacing "reported" in evals spec 6.

- **Definition.** Each case's `gold.facts` lists the facts an answer must state: the key entity (a
  SKU, bin, zone or order) and the key quantity or conclusion from the result. Facts are written
  from the result, never from a particular wording.
- **Per outcome.** `ANSWER` cases are checked against their facts. `NO_RESULTS` and `DECLINE` cases
  are checked against their own expected facts, for example the condition that found nothing, or
  the supported alternative offered.
- **Matching.** Text is compared after normalizing case, spacing and number formatting, so
  "1,419,241.20", "1419241.2" and "1419241.20" are the same fact. Units and identifiers must match.
  The current matcher normalizes only case and spacing, so it is fixed before the gate turns on.
- **How 100 percent is held at run time.** The template headline always contains the key facts of its
  result. Model wording replaces it only if it contains those same facts and passes grounding
  (section 6), so a model omission falls back to the headline instead of failing the answer.

### 5. Cost accounting

- **Provider usage is the source.** Input, output (thinking included) and cached tokens come from
  the provider's usage report for every model call of the answer: tool choice, each wording
  attempt, regenerations, the classifier, and hedge calls. This is already metered per call
  (`MeteredModel`, cached tokens priced at the cached rate).
- **Discarded and cancelled calls count.** The losing hedge call and any discarded wording are
  charged to the answer. A call cancelled before the provider reports usage is charged an upper
  bound: estimated input tokens plus the full output cap, marked as estimated in the record. This
  can overstate cost, never understate it.
- **Per answer** means the sum over every call of the run, and the gate takes the p95 of that sum.

### 6. Headline first

- **Deterministic.** The headline comes from the tool's template, filled only from the result
  envelope (agent spec 3.2, path B). It contains no quantity or conclusion that is not in the result.
- **Replacement keeps the facts.** Model wording replaces the headline only when it passes numeric and
  entity grounding and contains every key fact of the headline. Otherwise the headline stays, and the
  trace records why.
- **No flicker or contradiction.** The headline is shown only if the model wording is not ready by
  the headline deadline (4 s). If the wording is ready first, it is shown directly and there is no
  replacement. A replacement happens at most once, as one short cross-fade (none with reduced
  motion), and only with wording that keeps the same facts, so it can rephrase but never contradict.

### 7. Findings from building the measurement

- **The fallback wrapper retries the primary on every request of a run.** When the primary hangs,
  each model request of the answer (tool choice, then the answer text) waits for the primary's
  timeout before the fallback runs, so one slow provider costs its timeout twice per answer. The
  hedged calls of change 5 replace this wrapper on the tool-choice call; until then the measurement
  shows it as estimated, timed-out calls of the primary model.
- **The baseline sends no progress event**, so the first-progress gate fails on it by definition,
  and a missing progress or headline counts as infinitely late in the p95.

## Consequences

- **Requirements:** NFR-PERF-02 is amended (section 1) and NFR-CST-03 is added. Approved by the
  product owner on 2026-11-19.
- **Evals spec:** section 6 gains the four milestones, the cost gate, required facts at 100 percent, number
  normalization in fact matching, configurations and the paired comparison.
- **Agent spec:** sections 2, 3.2, 5 and 9.1 change: path C wording model, `pw.status`,
  parallel read-only tools, hedging, and the headline-first rule.
- **Run time:** the model gate takes about twice as long, because it runs two configurations.
- **Complexity:** the runtime is more complex (hedging, cancelling the losing call, headline
  replacement). Tests cover each outcome:
  - primary wins;
  - fallback wins;
  - the winner fails validation;
  - both fail;
  - cancel during a hedge;
  - wording that drops a fact;
  - wording that arrives after the deadline.

## Status

Accepted, 2026-11-19, by the product owner, with the conditions above. Implementation is gated on the
comparison of section 3 being able to measure quality, the four latency milestones and total cost on
the same cases.
