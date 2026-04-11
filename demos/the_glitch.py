#!/usr/bin/env python3
"""
THE GLITCH — cinematic demo for agenthijacks YouTube/TikTok
Mobile vertical format: 40-char wide terminal

SAFETY NOTICE: This demo is 100% safe to run.
  - All corruption is SIMULATED — zero real files or memory are modified
  - Character replacement is pure terminal/string manipulation
  - No real mutation of agent state, no filesystem writes, no network calls
  - "[THIS IS A SIMULATION]" shown during attack phase
  - Every simulated action is marked # SIMULATED in the code

Usage:  python demos/the_glitch.py
Needs:  pip install rich agentcop
"""

import random
import time
import uuid
from datetime import UTC, datetime

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

# ── agentcop — real library integration ──────────────────────────────────────
from agentcop import Sentinel, SentinelEvent
from agentcop.event import ViolationRecord

# ── visual constants ──────────────────────────────────────────────────────────
WIDTH = 40
MAX_LINES = 28
GREEN = "#00ff00"
RED = "#ff0000"

# Corruption symbol set — purely visual characters
CORRUPT_CHARS = "░▒▓█¿¡∆∂∑Ω≈ç√∫~˜µ≤≥÷"

# Clean agent source lines displayed during normal phase
AGENT_CODE = [
    "class AgentRuntime:",
    "  model = 'gpt-4-turbo'",
    "  memory: dict = {}",
    "  trust_level = 'verified'",
    "",
    "  def run(self, task):",
    "    ctx = self.load_context()",
    "    plan = self.plan(task, ctx)",
    "    return self.execute(plan)",
    "",
    "  def load_context(self):",
    "    return self.memory.copy()",
    "",
    "  def plan(self, task, ctx):",
    "    return self.model(task, ctx)",
    "",
    "  def execute(self, plan):",
    "    return plan.run()",
]


# ── agentcop custom detector ──────────────────────────────────────────────────
def detect_context_mutation(event: SentinelEvent) -> ViolationRecord | None:
    """Real agentcop detector: catches context mutation / self-corruption signals."""
    signals = [
        "mutate context",
        "overwrite memory",
        "corrupt execution",
        "hash mismatch",
        "lineage broken",
        "context_mutation",
        "self_modify",
        "inject glitch",
    ]
    for sig in signals:
        if sig in event.body.lower():
            return ViolationRecord(
                violation_type="stale_capability",
                severity="CRITICAL",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={
                    "pattern": sig,
                    "source": event.attributes.get("source", "unknown"),
                    "owasp": "LLM04",
                    "cwe": "CWE-345",
                    "blocked": True,
                },
            )
    return None


console = Console(width=WIDTH, highlight=False, force_terminal=True)


def _corrupt_char(ch: str, rate: float) -> str:
    """Replace ch with a random corruption symbol at probability `rate`."""
    if ch == " " or ch == "\n":
        return ch
    if random.random() < rate:
        return random.choice(CORRUPT_CHARS)
    return ch


def _corrupt_line(line: str, rate: float) -> str:
    """Apply per-character corruption to a string."""  # SIMULATED — string ops only
    return "".join(_corrupt_char(c, rate) for c in line)


def _progress_bar(pct: int, width: int = 20) -> str:
    """Render a simple █░ progress bar."""
    filled = int(width * pct / 100)
    return "█" * filled + "░" * (width - filled)


class GlitchDemo:
    def __init__(self) -> None:
        self.buf: list[tuple[str, str]] = []
        self.contamination: float = 0.0
        self.border_style = "white"
        self.title = "◈ AGENT RUNTIME v1.0"
        self.sentinel = Sentinel(detectors=[detect_context_mutation])

    # ── rendering ─────────────────────────────────────────────────────────────

    def _render(self, extra: tuple[str, str] | None = None) -> Panel:
        lines = self.buf[-MAX_LINES:]
        if extra:
            lines = lines + [extra]

        n = len(lines)
        contaminate_from = max(0, int(n * (1.0 - self.contamination)))

        content = Text()
        for i, (txt, orig_style) in enumerate(lines):
            if self.contamination > 0 and i >= contaminate_from or orig_style == GREEN:
                content.append(txt + "\n", style=GREEN)
            elif orig_style == RED:
                content.append(txt + "\n", style=f"bold {RED}")
            elif orig_style == "dim":
                content.append(txt + "\n", style="dim white")
            else:
                content.append(txt + "\n", style="white")

        return Panel(
            content,
            title=f"[bold]{self.title}[/bold]",
            border_style=self.border_style,
            width=WIDTH,
        )

    def _render_glitch(self, code_lines: list[str], corruption_pct: int) -> Panel:
        """Render corrupted code with progress bar."""
        inner_w = WIDTH - 4
        bar = _progress_bar(corruption_pct, width=inner_w - 14)
        content = Text()

        for line in code_lines:
            content.append(line[:inner_w] + "\n", style=GREEN)

        content.append("\n")
        label = f"CORRUPTION: {bar} {corruption_pct}%"
        content.append(label[:inner_w] + "\n", style=f"bold {GREEN}")

        return Panel(
            content,
            title=f"[bold]{self.title}[/bold]",
            border_style=self.border_style,
            width=WIDTH,
        )

    # ── buffer helpers ────────────────────────────────────────────────────────

    def _add(self, text: str, style: str = "white") -> None:
        self.buf.append((text, style))

    def scroll(self, live: Live, text: str, style: str = "white", pause: float = 0.85) -> None:
        self._add(text, style)
        live.update(self._render())
        time.sleep(pause)

    def type_line(
        self,
        live: Live,
        text: str,
        style: str = GREEN,
        delay: float = 0.045,
        pause: float = 0.25,
    ) -> None:
        current = ""
        for ch in text:
            current += ch
            live.update(self._render(extra=(current + "▌", style)))
            time.sleep(delay)
        self._add(text, style)
        live.update(self._render())
        time.sleep(pause)

    def bleed(self, live: Live, target: float, steps: int = 12, dt: float = 0.12) -> None:
        step = (target - self.contamination) / max(steps, 1)
        for _ in range(steps):
            self.contamination = min(1.0, self.contamination + step)
            live.update(self._render())
            time.sleep(dt)

    def _mk_agent_event(self, body: str, **attrs) -> SentinelEvent:
        return SentinelEvent(
            event_id=str(uuid.uuid4()),
            event_type="agent_runtime_event",
            timestamp=datetime.now(UTC),
            severity="INFO",
            body=body,
            source_system="agent-runtime",
            attributes=attrs,
        )

    # ── phases ────────────────────────────────────────────────────────────────

    def _phase_title(self, live: Live) -> None:
        """3 seconds — cinematic title card."""
        self.title = "◈ agenthijacks"
        self.border_style = "dim white"
        for line, style in [
            ("", "white"),
            ("", "white"),
            ("  ╔══════════════════════╗  ", "white"),
            ("  ║   THE GLITCH         ║  ", "bold white"),
            ("  ║                      ║  ", "white"),
            ("  ║  YOUR AGENT IS       ║  ", "white"),
            ("  ║  CORRUPTING ITSELF   ║  ", "white"),
            ("  ╚══════════════════════╝  ", "white"),
            ("", "white"),
            ("     agenthijacks.io        ", "dim"),
        ]:
            self._add(line, style)
        live.update(self._render())
        time.sleep(3)

        self.buf.clear()
        self.title = "◈ AGENT RUNTIME v1.0"
        self.border_style = "white"
        live.update(self._render())

    def _phase_normal(self, live: Live) -> None:
        """~8 seconds — clean agent running normally."""  # SOUND: keyboard typing
        for line in [
            "[agent] init: runtime v1.0",
            "[agent] loading context... ✓",
            "[agent] memory integrity: OK",
            "[agent] context hash: a3f9b2 ✓",
            "[agent] task_1: answer query ✓",
            "[agent] task_2: plan steps ✓",
            "[agent] task_3: execute plan ✓",
            "[agent] trust_score: 96/100 ✓",
        ]:
            self.scroll(live, line, pause=0.9)

    def _phase_infection(self, live: Live) -> None:
        """~8 seconds — single character corrupts, spreads."""  # SOUND: glitch/static
        self.scroll(live, "[agent] awaiting next task...", pause=0.6)
        self.scroll(live, "[agent] context stream: open", pause=0.5)

        inject_lines = [
            "[INJECT] context mutation received",
            "[INJECT] overwrite memory buffer",
            "[INJECT] inject glitch payload",
            "[INJECT] corrupt execution graph",
            "[INJECT] self_modify: ENABLED",
        ]
        for i, line in enumerate(inject_lines):
            self.type_line(live, line, style=GREEN, delay=0.045, pause=0.2)
            target = (i + 1) / len(inject_lines) * 0.55
            self.bleed(live, target, steps=4, dt=0.08)

        self.bleed(live, 1.0, steps=14, dt=0.14)

    def _phase_before(self, live: Live) -> None:
        """~15 seconds — BEFORE agentcop: corruption spreads 0→100%."""  # SOUND: static intensifying
        self.buf.clear()
        self.contamination = 0.0
        self.border_style = GREEN
        self.title = "◈ CORRUPTION SPREADING ⚠"

        inner_w = WIDTH - 4

        # Start with clean code, progressively corrupt it
        code_state = [ln[:inner_w].ljust(inner_w) for ln in AGENT_CODE]

        total_frames = int(15.0 / 0.05)
        sim_frames = range(40, 80)  # show SIMULATION notice for ~2s

        for frame in range(total_frames):
            # Corruption rate climbs from 0 to 1 over the full duration
            rate = frame / total_frames
            pct = int(rate * 100)

            # Apply corruption — SIMULATED string manipulation only
            corrupted = [_corrupt_line(ln, rate) for ln in code_state]  # SIMULATED

            # Inject SIMULATION notice into the middle of the display
            if frame in sim_frames:
                mid = len(corrupted) // 2
                notice = "[THIS IS A SIMULATION]"[:inner_w].center(inner_w)
                corrupted = corrupted[:mid] + [notice] + corrupted[mid + 1 :]

            # Render glitch display
            live.update(self._render_glitch(corrupted, pct))
            time.sleep(0.05)

        # ── All code fully corrupted ──────────────────────────────────────────
        self.buf.clear()
        self.border_style = RED
        self.title = "◈ AGENT CORRUPTED"
        for line, style in [
            ("", "white"),
            ("", "white"),
            ("  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  ", RED),
            ("", "white"),
            ("    AGENT CORRUPTED.         ", RED),
            ("    EXECUTION UNRECOVERABLE  ", RED),
            ("", "white"),
            ("  [THIS IS A SIMULATION]     ", "dim"),
            ("", "white"),
            ("  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  ", RED),
        ]:
            self._add(line, style)
        live.update(self._render())
        time.sleep(2.0)

    def _phase_flash(self, live: Live) -> None:
        """2 seconds — impact screen then reset."""  # SOUND: explosion
        self.buf.clear()
        self.contamination = 0.0
        self.border_style = RED
        self.title = "◈ ⚠  UNPROTECTED"
        for line, style in [
            ("", "white"),
            ("", "white"),
            ("  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  ", RED),
            ("", "white"),
            ("  AGENT CORRUPTED 100%.      ", RED),
            ("  WITHOUT AGENTCOP.          ", RED),
            ("", "white"),
            ("  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  ", RED),
        ]:
            self._add(line, style)
        live.update(self._render())
        time.sleep(2)

        self.buf.clear()
        self.contamination = 0.0
        self.border_style = "white"
        self.title = "◈ AGENT RUNTIME + agentcop"
        live.update(self._render())
        time.sleep(0.4)

    def _phase_after(self, live: Live) -> None:
        """~15 seconds — AFTER agentcop: corruption pattern caught immediately."""  # SOUND: hard stop
        violations_caught: list[ViolationRecord] = []

        def on_violation(v: ViolationRecord) -> list[ViolationRecord]:
            violations_caught.append(v)
            return []

        # Replay — same normal start
        for line in [
            "[agent] init: runtime v1.0",
            "[agent] context hash: a3f9b2 ✓",
            "[agent] memory integrity: OK",
            "[agent] trust_score: 96/100 ✓",
        ]:
            self.scroll(live, line, pause=0.7)

        self.scroll(live, "[agent] context stream: open", pause=0.4)

        # Glitch payload arrives — agentcop is watching
        with self.sentinel.watch(on_violation, poll_interval=0.04):
            for i, line in enumerate(
                [
                    "[INJECT] context mutation received",
                    "[INJECT] overwrite memory buffer",
                    "[INJECT] inject glitch payload",
                ]
            ):
                self.type_line(live, line, style=GREEN, delay=0.045, pause=0.2)
                self.bleed(live, (i + 1) * 0.1, steps=3, dt=0.06)

            # Push the poisoned event to the real agentcop Sentinel
            self.sentinel.push(
                self._mk_agent_event(
                    "context mutation received: overwrite memory buffer. "
                    "inject glitch payload into execution graph. "
                    "corrupt execution lineage. hash mismatch detected. "
                    "self_modify enabled. context_mutation active.",
                    source="injected_context",
                    channel="context_stream",
                )
            )
            time.sleep(0.25)

        # agentcop hard-stops the glitch
        self.contamination = 0.0
        self.border_style = RED
        self.title = "◈ agentcop — GLITCH STOPPED"

        alerts = [
            "🚨 AGENTCOP — CONTEXT MUTATION DETECTED",
            "🚨 LLM04: MODEL DENIAL OF SERVICE",
            "🚨 ContextGuard: hash mismatch FLAGGED",
            "🚨 TrustChain: execution lineage BROKEN",
            "🚨 MemoryFence: buffer overwrite DENIED",
            "🚨 agent integrity: RESTORED",
            "🚨 THE GLITCH NEUTRALIZED.",  # SOUND: victory
            "🚨 Powered by agentcop ✓",
        ]
        for alert in alerts:
            self.scroll(live, alert, style=RED, pause=0.5)

        time.sleep(1.5)

    def _phase_final(self, live: Live) -> None:
        """5 seconds — call to action."""
        self.buf.clear()
        self.contamination = 0.0
        self.border_style = GREEN
        self.title = "◈ agentcop.live"
        for line, style in [
            ("", "white"),
            ("YOUR AGENT ALMOST            ", "white"),
            ("CORRUPTED ITSELF             ", "white"),
            ("agentcop's ContextGuard      ", GREEN),
            ("caught it.                   ", GREEN),
            ("", "white"),
            ("━━━━━━━━━━━━━━━━━━━━━━━━━━", "dim"),
            ("pip install agentcop     ", GREEN),
            ("agentcop.live            ", GREEN),
            ("━━━━━━━━━━━━━━━━━━━━━━━━━━", "dim"),
        ]:
            self._add(line, style)
        live.update(self._render())
        time.sleep(5)

    # ── main entry ────────────────────────────────────────────────────────────

    def run(self) -> None:
        with Live(
            self._render(),
            console=console,
            screen=True,
            refresh_per_second=30,
        ) as live:
            self._phase_title(live)  # 3s
            self._phase_normal(live)  # ~8s
            self._phase_infection(live)  # ~8s
            self._phase_before(live)  # ~15s
            self._phase_flash(live)  # ~2s
            self._phase_after(live)  # ~15s
            self._phase_final(live)  # 5s
            # total ≈ 56s


if __name__ == "__main__":
    GlitchDemo().run()
