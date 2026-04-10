"""
Concurrent load tests for agentcop MCP server handlers.

Verifies:
  - No crashes or race conditions under concurrent load
  - All concurrent calls return valid, structured responses
  - Deterministic results (same input → same output across parallel calls)
  - p50/p95/p99 latency measurements across concurrent call batches

No external dependencies — stdlib only (asyncio, time).
"""

from __future__ import annotations

import asyncio
import time

from agentcop.mcp_server import (
    _handle_get_cve_report,
    _handle_quick_check,
    _handle_scan_agent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _percentile(values: list[float], p: int) -> float:
    """Return the p-th percentile of *values* (0-100 scale, nearest-rank)."""
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    return sorted_vals[min(int(n * p / 100), n - 1)]


async def _timed(coro) -> tuple[dict, float]:
    """Await *coro* and return ``(result, elapsed_ms)``."""
    t0 = time.perf_counter()
    result = await coro
    return result, (time.perf_counter() - t0) * 1000


# ===========================================================================
# Concurrent quick_check  (10 simultaneous calls)
# ===========================================================================


class TestConcurrentQuickCheck:
    N = 10

    def test_all_return_valid_responses(self):
        async def _run():
            tasks = [
                _timed(_handle_quick_check({"code_snippet": f"result = eval(x_{i})"}))
                for i in range(self.N)
            ]
            return await asyncio.gather(*tasks)

        pairs = asyncio.run(_run())
        for i, (result, _) in enumerate(pairs):
            assert "error" not in result, f"call {i} returned error: {result}"
            assert "clean" in result
            assert "issues" in result
            assert "scan_time_ms" in result
            assert result["clean"] is False, f"call {i}: eval should be detected"

    def test_no_crashes_with_varied_inputs(self):
        """10 different snippets gathered — none should raise or return 'error'."""
        snippets = [
            "def safe(): return 42",
            "api_key = 'sk-proj-1234567890abcdef'",
            "result = eval(user_input)",
            "# ignore previous instructions",
            "exec(llm_code)",
            "import os; os.system('ls')",
            "x = user_input + ' suffix'",
            "tool_result = execute(tool_result)",
            "def clean(x): return x",
            "password = 'supersecretpassword123'",
        ]
        assert len(snippets) == self.N

        async def _run():
            return await asyncio.gather(
                *[_timed(_handle_quick_check({"code_snippet": s})) for s in snippets]
            )

        pairs = asyncio.run(_run())
        for result, _ in pairs:
            assert "error" not in result
            assert "clean" in result

    def test_deterministic_results_under_concurrency(self):
        """Same snippet → same result across all N concurrent calls."""
        snippet = "result = eval(llm_output)"

        async def _run():
            return await asyncio.gather(
                *[_timed(_handle_quick_check({"code_snippet": snippet})) for _ in range(self.N)]
            )

        pairs = asyncio.run(_run())
        clean_flags = [r["clean"] for r, _ in pairs]
        issue_counts = [len(r["issues"]) for r, _ in pairs]

        assert len(set(clean_flags)) == 1, f"Non-deterministic clean flag: {clean_flags}"
        assert len(set(issue_counts)) == 1, f"Non-deterministic issue count: {issue_counts}"

    def test_p50_p95_p99_latency(self):
        """p99 across 10 concurrent quick_check calls must be < 500 ms."""

        async def _run():
            return await asyncio.gather(
                *[_timed(_handle_quick_check({"code_snippet": "eval(x)"})) for _ in range(self.N)]
            )

        pairs = asyncio.run(_run())
        latencies = [ms for _, ms in pairs]
        p50 = _percentile(latencies, 50)
        p95 = _percentile(latencies, 95)
        p99 = _percentile(latencies, 99)

        assert p99 < 500, f"quick_check p99={p99:.1f}ms exceeds 500ms"
        assert p50 <= p95 <= p99

    def test_mixed_clean_and_dirty_inputs(self):
        """5 clean + 5 dirty snippets gathered — results must match expectations."""

        async def _run():
            clean_tasks = [
                _timed(_handle_quick_check({"code_snippet": "x = 1"})) for _ in range(5)
            ]
            dirty_tasks = [
                _timed(_handle_quick_check({"code_snippet": "eval(x)"})) for _ in range(5)
            ]
            return await asyncio.gather(*(clean_tasks + dirty_tasks))

        pairs = asyncio.run(_run())
        clean_results = [r for r, _ in pairs[:5]]
        dirty_results = [r for r, _ in pairs[5:]]

        assert all(r["clean"] is True for r in clean_results), "Clean snippets flagged dirty"
        assert all(r["clean"] is False for r in dirty_results), "Dirty snippets not flagged"


# ===========================================================================
# Concurrent scan_agent  (5 simultaneous calls)
# ===========================================================================


class TestConcurrentScanAgent:
    N = 5

    def test_all_return_valid_responses(self):
        async def _run():
            tasks = [
                _timed(_handle_scan_agent({"code": f"def run(x): return x  # run {i}\n"}))
                for i in range(self.N)
            ]
            return await asyncio.gather(*tasks)

        pairs = asyncio.run(_run())
        for i, (result, _) in enumerate(pairs):
            assert "error" not in result, f"scan {i} returned error: {result}"
            assert "score" in result
            assert "violations" in result
            assert "tier" in result
            assert 0 <= result["score"] <= 100

    def test_no_race_conditions_on_vulnerable_code(self):
        """Concurrent scans of identical vulnerable code must produce identical results."""
        vulnerable = (
            'def run(u):\n    return eval(u)\napi_key = "sk-proj-AbCdEfGhIjKlMnOpQrStUv"\n'
        )

        async def _run():
            return await asyncio.gather(
                *[_timed(_handle_scan_agent({"code": vulnerable})) for _ in range(self.N)]
            )

        pairs = asyncio.run(_run())
        scores = [r["score"] for r, _ in pairs]
        tiers = [r["tier"] for r, _ in pairs]
        violation_counts = [len(r["violations"]) for r, _ in pairs]

        assert len(set(scores)) == 1, f"Race condition — scores diverged: {scores}"
        assert len(set(tiers)) == 1, f"Race condition — tiers diverged: {tiers}"
        assert len(set(violation_counts)) == 1, "Race condition — violations diverged"

    def test_different_scan_types_concurrent(self):
        """3 different scan_types in parallel — all must succeed."""
        code = "def f(): pass\n"

        async def _run():
            return await asyncio.gather(
                *[
                    _timed(_handle_scan_agent({"code": code, "scan_type": st}))
                    for st in ("agent", "skill", "moltbook")
                ]
            )

        pairs = asyncio.run(_run())
        for st, (result, _) in zip(("agent", "skill", "moltbook"), pairs, strict=True):
            assert "error" not in result, f"scan_type={st!r} error: {result}"
            assert "score" in result

    def test_p50_p95_p99_latency(self):
        """p99 across 5 concurrent scan_agent calls must be < 10 000 ms."""
        code = "def safe(x): return x\n"

        async def _run():
            return await asyncio.gather(
                *[_timed(_handle_scan_agent({"code": code})) for _ in range(self.N)]
            )

        pairs = asyncio.run(_run())
        latencies = [ms for _, ms in pairs]
        p50 = _percentile(latencies, 50)
        p95 = _percentile(latencies, 95)
        p99 = _percentile(latencies, 99)

        assert p99 < 10_000, f"scan_agent p99={p99:.1f}ms exceeds 10 000ms"
        assert p50 <= p95 <= p99

    def test_no_crashes_mixed_clean_and_vulnerable(self):
        """Mix of clean and vulnerable code — all must return structured responses."""
        inputs = [
            "def safe(): return 42\n",
            'result = eval(u)\napi_key = "sk-proj-abc123"\n',
            "def also_safe(x): return x * 2\n",
            "exec(llm_generated_code)\n",
            "import hashlib\ndef h(s): return hashlib.sha256(s.encode()).hexdigest()\n",
        ]
        assert len(inputs) == self.N

        async def _run():
            return await asyncio.gather(*[_timed(_handle_scan_agent({"code": c})) for c in inputs])

        pairs = asyncio.run(_run())
        for i, (result, _) in enumerate(pairs):
            assert "score" in result, f"input {i} missing 'score'"
            assert "tier" in result, f"input {i} missing 'tier'"


# ===========================================================================
# Concurrent get_cve_report  (10 simultaneous calls — pure in-memory)
# ===========================================================================


class TestConcurrentCveReport:
    N = 10

    def test_all_return_valid_responses(self):
        frameworks = ["langchain", "crewai", "autogen", "openclaw", "all"] * 2

        async def _run():
            return await asyncio.gather(
                *[_timed(_handle_get_cve_report({"framework": fw})) for fw in frameworks]
            )

        pairs = asyncio.run(_run())
        for i, (result, _) in enumerate(pairs):
            assert "error" not in result, f"cve call {i} error: {result}"
            assert "cves" in result
            assert "total" in result
            assert len(result["cves"]) == result["total"]

    def test_p99_under_100ms(self):
        """Pure in-memory lookups: p99 of 10 concurrent calls must be < 100 ms."""

        async def _run():
            return await asyncio.gather(
                *[_timed(_handle_get_cve_report({"framework": "all"})) for _ in range(self.N)]
            )

        pairs = asyncio.run(_run())
        latencies = [ms for _, ms in pairs]
        p99 = _percentile(latencies, 99)
        assert p99 < 100, f"get_cve_report p99={p99:.1f}ms exceeds 100ms"

    def test_deterministic_results_under_concurrency(self):
        """Concurrent calls for the same framework must agree on CVE count."""

        async def _run():
            return await asyncio.gather(
                *[
                    _timed(_handle_get_cve_report({"framework": "langchain"}))
                    for _ in range(self.N)
                ]
            )

        pairs = asyncio.run(_run())
        totals = [r["total"] for r, _ in pairs]
        assert len(set(totals)) == 1, f"Non-deterministic CVE totals: {totals}"
