# Reliability concepts

This page explains the five reliability metrics in plain English — what each one
measures, how it is calculated, and what a high or low score means for your agent.

---

## Overview

Every time an agent runs, agentcop records:

- **What path it took** — the sequence of steps (branches) it executed
- **Which tools it called** — and how many times each
- **How many retries it needed** — across all tool calls in the run
- **How many tokens it consumed** — input + output

Those four dimensions are combined into a single **reliability score (0–100)**.
The score is calculated fresh whenever you call `store.get_report()`, using all
runs that fall inside the requested time window.

---

## Path entropy

**One-line summary:** *How predictable is the agent's execution path?*

### What it measures

The agent's execution path is the ordered list of steps it took during a run — for
example `["fetch", "summarise", "reply"]`. Path entropy is the Shannon entropy of
the distribution of unique paths across all runs in the window, normalised to [0, 1]
by dividing by log₂(n).

### Examples

```
All runs take path ["fetch", "summarise", "reply"]
→ One path, probability 1.0
→ Entropy = 0.0  (perfectly predictable)

4 runs, 4 completely different paths
→ Each path has probability 0.25
→ Entropy = log₂(4) / log₂(4) = 1.0  (maximally unpredictable)

10 runs: 7 take ["a","b"], 3 take ["a","c"]
→ Entropy ≈ 0.88  (mostly one path, some variation)
```

### What a high score means

High path entropy means the agent is making different decisions each time it runs,
even for similar inputs. This makes the agent hard to test (you can't reproduce a
specific run reliably) and hard to debug (the path that failed yesterday may not
occur again today).

### Weight in the reliability score

**25%** of the composite score.

---

## Tool variance

**One-line summary:** *Does the agent use each tool a consistent number of times?*

### What it measures

For each tool the agent uses, the coefficient of variation (CV = std/mean) of its
call count across runs is calculated. The average CV across all tools is normalised
to [0, 1] (raw CV is capped at 2.0, so the maximum normalised value is 1.0).

### Examples

```
"bash" called exactly once in every run
→ std = 0, CV = 0
→ tool_variance contribution from bash = 0.0

"web_search" called 0 times in some runs, 5 times in others
→ high CV
→ tool_variance contribution from web_search is high
```

```python
# Run 1: bash × 1, read × 2
# Run 2: bash × 1, read × 2
# Run 3: bash × 1, read × 2
→ tool_variance ≈ 0.0  (very consistent)

# Run 1: bash × 3, read × 0
# Run 2: bash × 0, read × 5
# Run 3: bash × 1, read × 1
→ tool_variance ≈ 0.7  (erratic)
```

### What a high score means

High tool variance means the agent does not have a stable tool-use pattern. This
is often a sign that the LLM is choosing tools based on subtle prompt variation or
that the agent has too many tools available and is sampling from them inconsistently.

### Weight in the reliability score

**25%** of the composite score.

---

## Retry explosion

**One-line summary:** *How often — and how badly — does the agent get stuck retrying?*

### What it measures

Retry explosion is a composite score built from three components:

1. **Retry rate** — proportion of runs that had at least one retry
2. **Severity** — average retry count normalised by the warning threshold (3)
3. **Velocity** — extra weight for bursts (runs where retries exceeded the critical
   threshold of 10)

The three components are weighted and capped at 1.0.

### Examples

```
All runs have retry_count = 0
→ retry_explosion_score = 0.0

10% of runs have retry_count = 2 (below warning threshold)
→ retry_explosion_score ≈ 0.05

50% of runs have retry_count = 5
→ retry_explosion_score ≈ 0.4  (VARIABLE tier territory)

Any run with retry_count ≥ 10
→ retry_explosion_score spikes sharply  (CRITICAL tier territory)
```

### Thresholds

| Threshold | Retries |
|---|---|
| Warning | 3 per run |
| Critical | 10 per run |

### What a high score means

High retry explosion scores typically mean one of three things: a downstream
service is flaky, the LLM is generating malformed tool arguments, or the agent
is stuck in a loop and retrying the same failing action without backoff.

### Weight in the reliability score

**30%** of the composite score — the highest weight, because retries compound
cost and can run indefinitely without a circuit breaker.

---

## Branch instability

**One-line summary:** *Does the agent take the same path when given the same input?*

### What it measures

Runs are grouped by `input_hash` (a SHA-256 hash of the raw input). Within each
group, the normalised Hamming distance between execution paths is calculated —
i.e., how many steps differ when you align two paths of the same input side by
side. The average across all groups is the branch instability score.

A score of 0.0 means the agent always executes identically for the same input.
A score of 1.0 means it always takes a completely different path.

### Examples

```
Input hash "abc123":
  Run 1: ["fetch", "chunk", "summarise"]
  Run 2: ["fetch", "chunk", "summarise"]
  → Hamming distance = 0/3 → instability = 0.0

Input hash "abc123":
  Run 1: ["fetch", "chunk", "summarise"]
  Run 2: ["search", "rank", "reply"]
  → Hamming distance = 3/3 → instability = 1.0

Input hash "abc123":
  Run 1: ["fetch", "chunk", "summarise", "reply"]
  Run 2: ["fetch", "chunk", "cache", "reply"]
  → One step differs out of 4 → instability = 0.25
```

### What a high score means

High branch instability means the agent is non-deterministic even when its input
is identical. This is often caused by LLM temperature being too high, shared
mutable state between runs, or routing logic that depends on time or random seeds.

### Weight in the reliability score

**20%** of the composite score.

---

## Token budget

**One-line summary:** *Is the agent's token consumption spiking above its normal range?*

### What it measures

For each run, total token consumption (`input_tokens + output_tokens`) is compared
to a rolling baseline (the mean over all runs in the window). Any run that consumes
more than **3× the baseline** is flagged as a spike event. Spike events are emitted
as `SentinelEvent` objects with `event_type="token_budget_spike"`.

### Examples

```
Baseline: 1,200 tokens/run (average over past 20 runs)

Run with 1,100 tokens → 0.92× baseline → no spike
Run with 3,700 tokens → 3.08× baseline → SPIKE
Run with 8,000 tokens → 6.7× baseline  → SPIKE (very large)
```

### What a high score means

Token spikes mean the agent generated a much longer chain of thought, called more
tools, or received unexpectedly large responses. Common causes: a web search
returning a huge page, a recursive loop, or a model being switched to a more
verbose variant.

### Weight in the reliability score

**Informational only** — token budget does not affect the 0–100 reliability score
directly. Instead, `token_spike_detected=True` is surfaced in the report and in
`top_issues`, and spike events are emitted as `SentinelEvent` objects you can push
into a `Sentinel` for alerting.

---

## How the score is calculated

The four weighted metrics are combined into a penalty, which is subtracted from 100:

```
penalty = path_entropy × 0.25
        + tool_variance × 0.25
        + retry_explosion_score × 0.30
        + branch_instability × 0.20

reliability_score = clamp(round(100 - penalty × 100), 0, 100)
```

Because each metric is already normalised to [0, 1] and the weights sum to 1.0,
the maximum possible penalty is 1.0, which maps to a score of 0. A perfect agent
(all metrics at 0.0) scores 100.

### Tier thresholds

| Tier | Score | Badge |
|---|---|---|
| 🟢 STABLE | ≥ 80 | |
| 🟡 VARIABLE | 60–79 | |
| 🟠 UNSTABLE | 40–59 | |
| 🔴 CRITICAL | < 40 | |

---

## Drift detection

On top of the snapshot score, the `DriftDetector` splits the run window at its
midpoint and compares early vs recent metric values. If any metric increased by
more than 2× between the two halves, drift is flagged:

```python
report.drift_detected     # True / False
report.drift_description  # "tool_variance increased 3.1x in last 24h"
report.trend              # "IMPROVING" | "STABLE" | "DEGRADING"
```

Drift is surfaced as a `reliability_drift` `SentinelEvent` with `severity="WARN"`.

---

## Further reading

- [Reliability guide](../guides/reliability.md) — instrumentation, CLI, integrations
- [ReliabilityTracer API](../guides/reliability.md#reliabilitytracer)
- [Prometheus export](../guides/reliability.md#prometheus-export)
- [Predictive alerts](../guides/reliability.md#predictive-alerts)
