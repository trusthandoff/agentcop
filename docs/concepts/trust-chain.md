# TrustChain concepts

This page explains the core ideas behind agentcop's TrustChain layer in plain
English — what problem it solves, how each module fits the whole, and what
guarantees the layer provides and does not provide.

For working code, see the [TrustChain guide](../guides/trust-chain.md).

---

## The problem: multi-agent trust gaps

A single LLM call is easy to reason about: you control the input, you see the
output. A multi-agent system is different. Work crosses at least four kinds of
boundary:

1. **Orchestrator → sub-agent** — an orchestrator delegates a task. Did the
   sub-agent receive exactly the task that was sent, or was it altered in
   transit?

2. **Tool → agent** — a tool returns a result. Did the result come from the
   tool, or from an attacker who intercepted the response?

3. **RAG → context** — a retrieval step adds documents to the agent's context
   window. Are those documents trustworthy, or could a poisoned chunk instruct
   the agent to exfiltrate data?

4. **Memory → next turn** — an agent reads memory from a previous turn. Has
   that memory been modified since it was written?

At each boundary an attacker — or simply a misconfigured component — can inject
instructions, alter content, or impersonate a trusted source. The TrustChain
layer gives you the tools to detect all four failure modes.

---

## The chain metaphor

A trust chain is a sequence of cryptographic claims, one per execution step.
Each claim records:

- **What ran** — agent ID, tool calls, context hash, output hash
- **When** — a monotonically increasing timestamp
- **Who asserted it** — optionally, an Ed25519 signature over the claim payload
- **What came before** — the SHA-256 hash of the preceding claim

The last point is the key. Because each claim includes the hash of the previous
one, you cannot insert, delete, or modify a claim without breaking every
subsequent hash. Verification replays the chain from the beginning; the first
hash mismatch tells you exactly which step was tampered with.

This is the same design used by git commit history and append-only ledgers. It
is simple, proven, and does not require a third-party service.

---

## What "verified" means (and doesn't mean)

A verified chain means:

- Every claim's payload hash was correctly computed from the node's fields.
- Every claim's link hash correctly incorporates the preceding claim's ID.
- No claim has been inserted, deleted, or reordered.

A verified chain does **not** mean:

- The agent did what it claimed to do — agentcop can only verify the hash of
  the output, not whether that output is correct or safe.
- The input context was trustworthy — that requires `ContextGuard` or
  `RAGTrustLayer` in addition.
- Signatures are present — if the `cryptography` package is absent or no
  private key was provided, the chain operates in hash-only mode. Integrity is
  still verified; authenticity is not.

In other words: a verified chain proves *consistency*, not *correctness*.

---

## The 13 modules and their roles

The TrustChain layer is 13 discrete modules. Each solves a specific sub-problem
and can be used independently.

### Chain integrity

**`TrustChainBuilder`** — the core module. Maintains the linked sequence of
`TrustClaim` objects. Thread-safe; every `add_node()` call hashes the node and
links it to the previous claim. `verify_chain()` replays every hash.

**`TrustClaim` / `ExecutionNode`** (in `models.py`) — the data structures.
`ExecutionNode` captures what happened; `TrustClaim` is the signed, hashed,
linked record of that node.

### Attestation

**`NodeAttestor`** — adds Ed25519 signatures to claims when the `cryptography`
package is installed. When absent, silently operates in hash-only mode.
Attestation answers: *did this specific agent instance produce this claim, or
was it forged?*

### Boundary enforcement

**`ToolTrustBoundary`** — an O(1) allow/deny table for tool-to-tool data flow.
Declares which tools may pass their output to which other tools. Denied
crossings automatically fire a `SentinelEvent`. Example: "web_search results
must not flow directly to code_exec."

### Provenance

**`ProvenanceTracker`** — records the origin of every instruction and detects
when a `tool`, `rag`, or `memory` result claims to be a direct `user`
instruction. This is the canonical defence against prompt-injection attacks that
try to masquerade as the system operator.

### Context integrity

**`ContextGuard`** — snapshots context hashes at pipeline entry points and
flags mutation events that match known injection patterns (role override
attempts, exfiltration triggers, base64-encoded commands).

### Document trust

**`RAGTrustLayer`** — maintains a per-source trust registry. Documents from
`verified` sources pass through; documents from `unverified` or `untrusted`
sources are flagged. Also scans document batches for poisoning patterns.

### Memory protection

**`MemoryGuard`** — snapshots agent memory between turns and detects when
poisoning patterns appear in an update that were absent before. Catches persona
override attempts, instruction injection into memory, and "jailbreak" patterns
that agents store for later retrieval.

### Hierarchy enforcement

**`AgentHierarchy`** — defines supervisor/worker relationships and enforces
delegation rules. Calls between unrelated hierarchies are denied and fire a
`SentinelEvent`. Supports veto rights (one agent can block another's actions),
quorum requirements (minimum votes to proceed), and depth tracking (maximum
delegation depth).

### Portability

**`TrustInterop`** — serialises `TrustClaim` objects to a portable token
(`agentcop.trust.v1.*`) that can cross process boundaries, HTTP headers, or
tool call arguments. Any agentcop instance can verify the token's embedded
checksum. Exports to OpenAI function format and Anthropic tool format for
native framework integration.

### Observability

**`TrustObserver`** — converts trust data to the telemetry format your stack
already uses: OTel spans, LangSmith run dicts, Datadog trace dicts. Exposes
Prometheus counters for verified chains, delegation violations, and boundary
violations. Supports webhook delivery for real-time alerting.

---

## Threat model

The TrustChain layer addresses the following specific threats:

| Threat | Module |
|--------|--------|
| Tampered agent output | `TrustChainBuilder` — hash mismatch detected |
| Forged claim source | `NodeAttestor` — signature verification |
| Prompt injection via tool result | `ProvenanceTracker` — source type spoofing |
| Prompt injection via RAG | `RAGTrustLayer` — poisoning scan + source trust |
| Prompt injection via context mutation | `ContextGuard` — pattern matching on delta |
| Memory poisoning between turns | `MemoryGuard` — snapshot diff |
| Unauthorised tool data flow | `ToolTrustBoundary` — allow/deny table |
| Rogue agent calling outside its scope | `AgentHierarchy` — hierarchy validation |
| Tampering with claims in transit | `TrustInterop` — checksum verification |

The layer does not address:

- **LLM hallucination** — the content of an agent's output is not verified,
  only its hash integrity.
- **Network-level interception** — agentcop runs in-process. For transport
  security, use TLS between services.
- **Compromised orchestrator** — if the process that runs `TrustChainBuilder`
  is itself compromised, the attacker controls the chain. Signatures (via
  `NodeAttestor`) mitigate this by tying claims to a private key stored outside
  the process.

---

## Relationship to the Reliability Layer

The Reliability Layer and TrustChain Layer are complementary:

- **Reliability** asks: *is this agent behaving consistently over time?* It
  measures path entropy, tool variance, retry rates, and token consumption
  across many runs.
- **TrustChain** asks: *was this specific run tampered with?* It verifies
  integrity, provenance, and delegation authority within a single execution.

The two layers can be used together: attach a `TrustObserver` to the same
telemetry pipeline that collects reliability metrics, and you get both
consistency monitoring and per-run integrity verification in one dashboard.

---

## Deployment model

**No new mandatory dependencies.** The full layer works with `pip install
agentcop`. Ed25519 signing requires `pip install cryptography`, which is already
a transitive dependency of most LLM frameworks.

**In-process only.** All trust classes run in the same Python process as your
agent. For cross-service trust, use `TrustInterop` to export portable tokens
and verify them in the receiving service.

**Thread-safe by default.** Every stateful class uses `threading.Lock()`
internally. You can share one instance across threads without external
synchronisation.

**Storage.** `TrustChainBuilder` defaults to in-memory storage. For persistence
across restarts, use `export_chain("json")` and store the JSON yourself; SQLite
support is planned for a future release.

---

## Further reading

- [TrustChain guide](../guides/trust-chain.md) — all 13 modules with working
  code examples, key management, and Docker deployment notes
- [Adapter docs](../adapters/) — how to attach trust params to each adapter
- [Reliability concepts](reliability.md) — the complementary consistency layer
