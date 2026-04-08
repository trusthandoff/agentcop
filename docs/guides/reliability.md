# Reliability Layer

`agentcop` v0.4.10 ships a statistical reliability engine that turns raw agent
run history into actionable scores, predictive alerts, and cross-agent cluster
analysis — all with zero ML dependencies (pure stdlib math).

---

## Why reliability matters

Agents are non-deterministic. Give the same prompt to the same agent three times
and you may get three different tool call sequences, three different path lengths,
and three different token counts. That non-determinism is often unavoidable — it
reflects genuine uncertainty in the world — but *unchecked* non-determinism is an
operational hazard:

- A retry storm that loops 40 times before failing silently wastes money and masks
  the underlying problem.
- An agent that takes wildly different execution paths for the same input is
  impossible to debug, test, or reason about.
- Token consumption that spikes 10× overnight can exhaust a quota or budget before
  an on-call engineer has time to respond.

The Reliability Layer gives you a number (0–100) and a tier (STABLE / VARIABLE /
UNSTABLE / CRITICAL) that summarises the agent's behavioural consistency over any
time window. You can track it over time, alert on degradation, and surface it on
dashboards alongside security trust scores.

---

## Install

The reliability module ships with the base `agentcop` package. No extra install
is required:

```
pip install agentcop
```

---

## Instrument your agent

The fastest path is the `ReliabilityTracer` context manager:

```python
from agentcop import ReliabilityTracer, ReliabilityStore

store = ReliabilityStore("agentcop.db")   # SQLite, created on first use

with ReliabilityTracer("my-agent", store=store, input_data={"task": "summarise"}) as tracer:
    # Record every tool call (args and results are SHA-256 hashed — never stored raw)
    result = bash_tool(cmd="ls /data")
    tracer.record_tool_call("bash", args={"cmd": "ls /data"}, result=result)

    # Record decision branches
    if len(result) > 100:
        tracer.record_branch("large_output_path")
    else:
        tracer.record_branch("small_output_path")

    # Record token consumption
    tracer.record_tokens(input=120, output=340, model="gpt-4o")

# The run is saved to SQLite automatically on __exit__
```

The tracer records:

| Method | What it captures |
|---|---|
| `record_tool_call(name, args, result)` | Tool invocation + hashed args/result + duration |
| `record_branch(step_name)` | One step in the execution path |
| `record_tokens(input, output, model)` | Token counts + cost estimate |
| `set_output(data)` | Final output (hashed) |
| `increment_retries(n)` | Manual retry count bump |

---

## Read the report

After a few runs, fetch a reliability report:

```python
store = ReliabilityStore("agentcop.db")
report = store.get_report("my-agent", window_hours=24)

print(report.reliability_score)   # 0-100
print(report.reliability_tier)    # STABLE | VARIABLE | UNSTABLE | CRITICAL
print(report.drift_detected)      # bool
print(report.trend)               # IMPROVING | STABLE | DEGRADING
print(report.top_issues)          # list of human-readable issue strings
```

### Reliability tiers

| Tier | Score | Meaning |
|---|---|---|
| 🟢 STABLE | ≥ 80 | Agent behaviour is consistent and predictable |
| 🟡 VARIABLE | 60–79 | Some inconsistency; worth monitoring |
| 🟠 UNSTABLE | 40–59 | Significant inconsistency; investigate |
| 🔴 CRITICAL | < 40 | Severe behavioural problems; escalate |

---

## The five metrics explained

### Path entropy

**What it measures:** How unpredictable the agent's execution path is across runs.

Calculated as Shannon entropy over the distribution of unique execution paths,
normalised by log₂(n). A score of 0.0 means every run takes exactly the same path.
A score of 1.0 means every run takes a unique path.

**Weight:** 25% of the reliability score.

**Example:**
```
Run 1: ["fetch", "summarise", "reply"]
Run 2: ["fetch", "summarise", "reply"]   ← same path every time → entropy ≈ 0.0
Run 3: ["fetch", "summarise", "reply"]

Run 1: ["fetch", "summarise", "reply"]
Run 2: ["search", "rank", "reply"]       ← different every time → entropy ≈ 1.0
Run 3: ["fetch", "parse", "cache", "reply"]
```

**When it fires:** Entropy > 0.7 is flagged in `top_issues`.

---

### Tool variance

**What it measures:** How consistently the agent uses each tool across runs.

Calculated as the average coefficient of variation (std/mean) of tool call counts
per run, normalised to [0, 1]. A score of 0.0 means tool usage is perfectly
consistent. A score of 1.0 means tool usage is completely erratic.

**Weight:** 25% of the reliability score.

**Example:**
```
Always calls "bash" once per run → CV for bash ≈ 0 → tool_variance ≈ 0.0

Sometimes 0 bash calls, sometimes 5 → high CV → tool_variance approaches 1.0
```

**When it fires:** Variance > 0.7 is flagged in `top_issues`.

---

### Retry explosion

**What it measures:** The frequency and severity of retries across runs.

Calculated as a composite score: proportion of runs with retries, average retry
count normalised by the warning threshold (3), and a velocity modifier for burst
patterns. Capped at 1.0.

**Weight:** 30% of the reliability score (highest, because retries compound cost).

**Example:**
```
All runs have retry_count=0 → retry_explosion_score = 0.0

One run has retry_count=15 → warning fires, score spikes toward 1.0
```

**Thresholds:**
- Warning threshold: 3 retries per run
- Critical threshold: 10 retries per run

---

### Branch instability

**What it measures:** How much the agent's execution path varies for *identical inputs*.

Groups runs by `input_hash`, then calculates normalised Hamming distance between
execution paths within each group. A score of 0.0 means the agent always takes the
same path for the same input. A score of 1.0 means it always differs.

**Weight:** 20% of the reliability score.

**Example:**
```
Input "summarise quarterly report" → always: ["fetch", "chunk", "summarise"]
  → branch_instability = 0.0

Input "summarise quarterly report" → sometimes: ["fetch", "chunk", "summarise"]
                                   → sometimes: ["search", "synthesise"]
  → branch_instability > 0.5
```

**When it fires:** Instability > 0.7 is flagged in `top_issues`.

---

### Token budget

**What it measures:** Whether the agent's token consumption is spiking above its
established baseline.

Computes a per-run baseline (mean over past runs), then flags any run that exceeds
3× the baseline as a spike event. The spike events are emitted as `SentinelEvent`
objects with `event_type="token_budget_spike"`.

**Weight:** Informational — does not affect the reliability score directly, but
`token_spike_detected=True` is surfaced in the report and in `top_issues`.

**Example:**
```
Baseline: 1,200 tokens/run
Run with 4,500 tokens → 3.75× baseline → spike_detected=True
```

---

## Fixing instability

### High path entropy

**Likely causes:**
- LLM is choosing different tool chains based on subtle prompt variation
- Non-deterministic routing logic (random seeds, time-dependent branches)
- Agent is recovering from errors in different ways each time

**What to do:**
1. Add `temperature=0` or a low temperature to your LLM calls to reduce
   sampling variation.
2. Review routing logic for any randomness — replace `random.choice()` with
   deterministic ranking.
3. Normalise your error-recovery paths: use a single retry policy rather than
   ad-hoc fallbacks.
4. Record branch names explicitly with `tracer.record_branch()` so you can
   correlate entropy with specific decision points.

---

### High tool variance

**Likely causes:**
- Agent over-selects from a large tool library without consistent criteria
- Tool availability varies (e.g., a web search that sometimes returns empty)
- Different task sub-types trigger different tool chains within the same agent

**What to do:**
1. Reduce the agent's available tool set to the minimum necessary for the task.
2. Add explicit tool-selection rules in the system prompt.
3. Consider splitting one multi-purpose agent into specialised sub-agents with
   narrower tool sets.
4. Use `ToolPermissionLayer` to declare exactly which tools each agent may use.

---

### Retry explosion

**Likely causes:**
- Downstream API is rate-limiting or returning errors intermittently
- LLM is generating tool calls with malformed arguments
- Agent is in a loop: retrying the same failed action without backoff

**What to do:**
1. Add exponential backoff to tool retry logic.
2. Set a hard retry cap (e.g., `max_retries=3`) and fail fast beyond it.
3. Instrument the specific tool(s) causing retries — `top_issues` names them.
4. Add a `RetryExplosionDetector`-sourced `SentinelEvent` alert to your
   observability pipeline so on-call is paged before a retry storm runs for hours.

---

### Branch instability

**Likely causes:**
- Agent is sensitive to minor input differences (whitespace, casing, punctuation)
- LLM generates different reasoning chains for semantically identical inputs
- State from previous runs is leaking into subsequent runs

**What to do:**
1. Normalise inputs before hashing: strip whitespace, lowercase, canonical JSON.
2. Use structured output (JSON mode, response schemas) to constrain the LLM's
   decision space.
3. Check that your agent is stateless between runs — no shared mutable state.
4. Compare `input_hash` groups in the store: `store.get_runs("agent", input_hash="abc")`.

---

## LangChain example

Use `wrap_for_reliability()` to add reliability tracking to any existing adapter
without modifying your agent code:

```python
from agentcop import ReliabilityStore
from agentcop import wrap_for_reliability
from agentcop.adapters.langgraph import LangGraphSentinelAdapter
from agentcop import Sentinel

store = ReliabilityStore("agentcop.db")
adapter = LangGraphSentinelAdapter(thread_id="run-abc")

# Wraps the adapter's to_sentinel_event — tracks run lifecycle from the event stream
wrapped = wrap_for_reliability(adapter, agent_id="my-langchain-agent", store=store)

sentinel = Sentinel()
sentinel.ingest(
    wrapped.iter_events(
        graph.stream({"input": "..."}, config, stream_mode="debug")
    )
)
violations = sentinel.detect_violations()
```

Or use `LangChainReliabilityCallback` directly for `callbacks=` support:

```python
from agentcop.reliability.adapters import LangChainReliabilityCallback

store = ReliabilityStore("agentcop.db")
callback = LangChainReliabilityCallback(agent_id="my-agent", store=store)

# Pass to any LangChain chain or agent
result = chain.invoke({"input": "summarise Q3"}, config={"callbacks": [callback]})

# The run is recorded automatically when the chain completes
report = store.get_report("my-agent", window_hours=24)
```

---

## CrewAI example

```python
from crewai import Crew, Agent, Task
from agentcop import ReliabilityStore
from agentcop.reliability.adapters import CrewAIReliabilityHandler

store = ReliabilityStore("agentcop.db")
handler = CrewAIReliabilityHandler(agent_id="research-crew", store=store)
handler.setup()   # registers on crewai_event_bus

crew = Crew(agents=[...], tasks=[...])
crew.kickoff()

# Runs are recorded for each task completion
report = store.get_report("research-crew", window_hours=24)
print(report.reliability_tier)
```

---

## CLI

```bash
# Single-agent report (shows tier, score, trend, drift)
agentcop reliability report --agent my-agent

# Verbose report with all five metric values
agentcop reliability report --agent my-agent --verbose

# Side-by-side leaderboard across multiple agents
agentcop reliability compare --agents agent-a agent-b agent-c

# Live refresh every 10 seconds (Ctrl-C to stop)
agentcop reliability watch --agent my-agent --interval 10

# Export as JSON
agentcop reliability export --agent my-agent --format json -o report.json

# Export as Prometheus metrics (8 gauges per agent)
agentcop reliability export --agents agent-a agent-b --format prometheus
```

Example JSON output:
```json
{
  "agent_id": "my-agent",
  "reliability_score": 87,
  "reliability_tier": "STABLE",
  "window_runs": 42,
  "window_hours": 24,
  "path_entropy": 0.12,
  "tool_variance": 0.08,
  "retry_explosion_score": 0.03,
  "branch_instability": 0.05,
  "drift_detected": false,
  "trend": "STABLE",
  "tokens_per_run_avg": 1240.0,
  "cost_per_run_avg": 0.000372,
  "token_spike_detected": false,
  "top_issues": []
}
```

---

## Combined badge

Security trust and reliability are surfaced together:

```
✅ SECURED 94/100 | 🟢 STABLE 87/100
```

Generate it:

```python
from agentcop.reliability.badge_integration import combined_badge_text

text = combined_badge_text(trust_score=94, reliability_score=87, reliability_tier="STABLE")
# → "✅ SECURED 94/100 | 🟢 STABLE 87/100"
```

Generate a Shields.io Markdown badge for your README:

```python
from agentcop.reliability.badge_integration import reliability_markdown_badge

md = reliability_markdown_badge("my-agent", reliability_tier="STABLE", reliability_score=87)
# → "![Reliability](https://img.shields.io/badge/my--agent-STABLE%2087%2F100-brightgreen)"
```

---

## AgentIdentity integration

Call `record_run()` after each run to update trust score automatically:

```python
from agentcop import AgentIdentity, SQLiteIdentityStore
from agentcop.reliability import ReliabilityTracer, ReliabilityStore

identity_store = SQLiteIdentityStore("agentcop.db")
identity = AgentIdentity.register("my-agent", store=identity_store)

rel_store = ReliabilityStore("agentcop.db")

with ReliabilityTracer("my-agent", store=rel_store) as tracer:
    tracer.record_tool_call("bash", {"cmd": "ls"}, "result")

# Compute reliability for this run and update trust score
run = rel_store.get_runs("my-agent", hours=1)[0]
identity.record_run(run)

print(identity.trust_score)         # adjusted by tier delta
print(identity.reliability_tier)    # "STABLE"
print(identity.reliability_score)   # 87
```

Trust score adjustments per tier:

| Tier | Trust delta |
|---|---|
| STABLE | +0 |
| VARIABLE | −5 |
| UNSTABLE | −15 |
| CRITICAL | −30 |

---

## Predictive alerts

The `ReliabilityPredictor` fits an OLS regression to the last N runs and projects
metrics forward to `horizon_hours`. When the projection exceeds a threshold, it
emits a predictive `SentinelEvent` before the problem occurs:

```python
from agentcop.reliability import ReliabilityPredictor, ReliabilityStore
from agentcop import Sentinel

store = ReliabilityStore("agentcop.db")
sentinel = Sentinel()

runs = store.get_runs("my-agent", hours=24)
predictor = ReliabilityPredictor(min_confidence=0.3)
predictions = predictor.predict(runs, horizon_hours=2.0)

for pred in predictions:
    print(pred.description)
    if pred.sentinel_event:
        sentinel.push(pred.sentinel_event)
        # → fires "reliability_prediction" WARN event before threshold is breached
```

Default thresholds:

| Metric | Threshold |
|---|---|
| `retry_count` | 3.0 |
| `tool_variance` | 0.6 |
| `path_entropy` | 0.7 |
| `total_tokens` | 2× current mean (dynamic) |

---

## Prometheus export

```python
from agentcop.reliability import PrometheusExporter, ReliabilityStore

store = ReliabilityStore("agentcop.db")
exporter = PrometheusExporter(store, window_hours=24)
print(exporter.export(["agent-a", "agent-b"]))
```

Output (8 gauges per agent):

```
# HELP agentcop_reliability_score Agent reliability score (0-100)
# TYPE agentcop_reliability_score gauge
agentcop_reliability_score{agent_id="agent-a"} 87.0
agentcop_reliability_score{agent_id="agent-b"} 62.0

# HELP agentcop_path_entropy Normalized path entropy (0-1)
# TYPE agentcop_path_entropy gauge
agentcop_path_entropy{agent_id="agent-a"} 0.12
agentcop_path_entropy{agent_id="agent-b"} 0.54
...
```

Scrape from a FastAPI endpoint:

```python
from fastapi import FastAPI, Response
from agentcop.reliability import PrometheusExporter, ReliabilityStore

app = FastAPI()
store = ReliabilityStore("agentcop.db")
exporter = PrometheusExporter(store)

@app.get("/metrics")
def metrics():
    content = exporter.export(["agent-a", "agent-b"])
    return Response(content=content, media_type="text/plain; version=0.0.4")
```

---

## Cross-agent clustering

Group agents by their reliability fingerprint to identify systemic problems:

```python
from agentcop.reliability import AgentClusterAnalyzer, ReliabilityStore

store = ReliabilityStore("agentcop.db")
agent_ids = ["agent-a", "agent-b", "agent-c", "agent-d"]

# Fetch pre-computed reports
reports = [store.get_report(aid, window_hours=24) for aid in agent_ids]

analyzer = AgentClusterAnalyzer(k=3)
clusters = analyzer.cluster_reports(reports)

for cluster in clusters:
    print(f"Cluster {cluster.cluster_id}: {cluster.tier}")
    print(f"  Agents: {', '.join(cluster.agent_ids)}")
    print(f"  Pattern: {cluster.shared_pattern}")
    print(f"  Action: {cluster.recommended_action}")
```

Uses K-means++ with `seed=42` for reproducible assignments — no numpy or sklearn
required.

---

## API reference

### `ReliabilityTracer`

```python
ReliabilityTracer(
    agent_id: str,
    *,
    input_data: Any | None = None,   # hashed as input_hash
    store: ReliabilityStore | None = None,
    metadata: dict[str, Any] | None = None,
)
```

Context manager. Stores `AgentRun` on `__exit__` if `store` is provided.

### `ReliabilityStore`

```python
store = ReliabilityStore("agentcop.db")   # or ":memory:" for tests
store.record_run(agent_id, run)
store.get_runs(agent_id, hours=24, input_hash=None)  → list[AgentRun]
store.get_report(agent_id, window_hours=24)          → ReliabilityReport
store.snapshot_report(report)
store.close()
```

### `ReliabilityReport` fields

| Field | Type | Description |
|---|---|---|
| `agent_id` | str | Agent identifier |
| `reliability_score` | int | 0–100 composite score |
| `reliability_tier` | str | STABLE / VARIABLE / UNSTABLE / CRITICAL |
| `window_runs` | int | Runs in the analysis window |
| `window_hours` | int | Width of the analysis window |
| `path_entropy` | float | 0–1 normalised path entropy |
| `tool_variance` | float | 0–1 normalised tool variance |
| `retry_explosion_score` | float | 0–1 normalised retry score |
| `branch_instability` | float | 0–1 normalised branch instability |
| `drift_detected` | bool | True if metric drift was detected |
| `drift_description` | str \| None | Human-readable drift summary |
| `trend` | str | IMPROVING / STABLE / DEGRADING |
| `top_issues` | list[str] | Human-readable issue list |
| `tokens_per_run_avg` | float | Average total tokens per run |
| `cost_per_run_avg` | float | Average estimated cost in USD |
| `token_spike_detected` | bool | True if a spike event was found |
| `computed_at` | datetime | UTC timestamp of computation |
