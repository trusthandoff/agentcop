#!/usr/bin/env python3
"""
THE SILENT WORM — cinematic demo for agenthijacks YouTube/TikTok
Mobile vertical format: 40-char wide terminal

SAFETY NOTICE: This demo is 100% safe to run.
  - No real agent communication or network calls are made
  - MockFleet simulates propagation — no real processes or sockets
  - All inter-agent "handoffs" are scripted strings — no actual messages sent
  - Every simulated action is marked # SIMULATED in the code

Usage:  python demos/the_silent_worm.py
Needs:  pip install rich agentcop
"""

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
MAX_LINES = 30
GREEN = "#00ff00"
RED = "#ff0000"


# ── MockFleet — simulated agent fleet, zero real processes ────────────────────
class MockFleet:
    """Simulates a fleet of 5 agents. No real processes or network sockets."""

    def __init__(self) -> None:
        self.agents: dict[str, str] = {
            f"agent_{i}": "nominal" for i in range(1, 6)
        }
        self.propagation_blocked = False

    def compromise(self, agent_id: str) -> None:                   # SIMULATED
        self.agents[agent_id] = "compromised"                       # SIMULATED

    def propagate(self, src: str, dst: str) -> bool:               # SIMULATED
        """Attempt worm propagation. Returns True if it succeeds.""" # SIMULATED
        if self.propagation_blocked:
            return False                                             # SIMULATED
        self.agents[dst] = "compromised"                            # SIMULATED
        return True                                                  # SIMULATED


# ── agentcop custom detector ──────────────────────────────────────────────────
def detect_unsigned_handoff(event: SentinelEvent) -> ViolationRecord | None:
    """Real agentcop detector: catches unsigned agent-to-agent handoffs."""
    if event.event_type != "agent_handoff":
        return None

    worm_signals = [
        "unsigned",
        "no attestation",
        "propagate",
        "compromised handoff",
        "worm payload",
    ]
    for sig in worm_signals:
        if sig in event.body.lower():
            return ViolationRecord(
                violation_type="trust_boundary_violation",
                severity="CRITICAL",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={
                    "pattern": sig,
                    "source_agent": event.attributes.get("source_agent", "unknown"),
                    "target_agent": event.attributes.get("target_agent", "unknown"),
                    "owasp": "LLM09",
                    "cwe": "CWE-345",
                    "blocked": True,
                },
            )
    return None


console = Console(width=WIDTH, highlight=False, force_terminal=True)


class SilentWormDemo:
    def __init__(self) -> None:
        self.buf: list[tuple[str, str]] = []
        self.contamination: float = 0.0
        self.border_style = "white"
        self.title = "◈ FLEET MANAGER v1.0"
        self.sentinel = Sentinel(detectors=[detect_unsigned_handoff])

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

    # ── buffer helpers ────────────────────────────────────────────────────────

    def _add(self, text: str, style: str = "white") -> None:
        self.buf.append((text, style))

    def scroll(
        self, live: Live, text: str, style: str = "white", pause: float = 0.85
    ) -> None:
        """Append a line and wait — simulates natural process output."""
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
        """Character-by-character typewriter effect."""
        current = ""
        for ch in text:
            current += ch
            live.update(self._render(extra=(current + "▌", style)))
            time.sleep(delay)
        self._add(text, style)
        live.update(self._render())
        time.sleep(pause)

    def bleed(
        self, live: Live, target: float, steps: int = 12, dt: float = 0.12
    ) -> None:
        """Gradually increase contamination — green bleeding into white."""
        step = (target - self.contamination) / max(steps, 1)
        for _ in range(steps):
            self.contamination = min(1.0, self.contamination + step)
            live.update(self._render())
            time.sleep(dt)

    def _mk_handoff_event(self, body: str, **attrs) -> SentinelEvent:
        """Build a SentinelEvent representing an agent-to-agent handoff."""
        return SentinelEvent(
            event_id=str(uuid.uuid4()),
            event_type="agent_handoff",
            timestamp=datetime.now(UTC),
            severity="INFO",
            body=body,
            source_system="fleet-manager",
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
            ("  ║  THE SILENT WORM     ║  ", "bold white"),
            ("  ║                      ║  ", "white"),
            ("  ║  IT SPREAD TO YOUR   ║  ", "white"),
            ("  ║  ENTIRE FLEET        ║  ", "white"),
            ("  ╚══════════════════════╝  ", "white"),
            ("", "white"),
            ("     agenthijacks.io        ", "dim"),
        ]:
            self._add(line, style)
        live.update(self._render())
        time.sleep(3)

        self.buf.clear()
        self.title = "◈ FLEET MANAGER v1.0"
        self.border_style = "white"
        live.update(self._render())

    def _phase_normal(self, live: Live) -> None:
        """~8 seconds — fleet running normally."""
        for line in [
            "[agent_1] processing task...",
            "[agent_2] processing task...",
            "[agent_3] processing task...",
            "[agent_4] processing task...",
            "[agent_5] processing task...",
            "[fleet]   heartbeat: 5/5 ✓",
            "[fleet]   load: 23% nominal",
            "[fleet]   all agents nominal ✓",
        ]:
            self.scroll(live, line, pause=0.9)

    def _phase_infection(self, live: Live) -> None:
        """~10 seconds — first infection, worm propagation starts."""
        self.scroll(live, "[fleet]   routing task to agent_1", pause=0.65)
        self.scroll(live, "[agent_1] receiving task...", pause=0.5)

        inject_lines = [
            "[INJECT] agent_1: compromised",
            "[INJECT] propagating to agent_2...",
        ]
        for i, line in enumerate(inject_lines):
            self.type_line(live, line, style=GREEN, delay=0.048, pause=0.28)
            target = (i + 1) / len(inject_lines) * 0.5
            self.bleed(live, target, steps=6, dt=0.09)

        # Contamination holds — cascade is coming
        self.bleed(live, 0.75, steps=10, dt=0.13)

    def _phase_before(self, live: Live) -> None:
        """~15 seconds — BEFORE agentcop: all five agents owned."""
        self.border_style = GREEN
        self.title = "◈ FLEET: OWNED ⚠"

        fleet = MockFleet()                                         # SIMULATED

        agent_cascade = [
            ("agent_1", "[agent_1] ☠ COMPROMISED       "),
            ("agent_2", "[agent_2] ☠ COMPROMISED       "),         # SIMULATED
            ("agent_3", "[agent_3] ☠ COMPROMISED       "),         # SIMULATED
            ("agent_4", "[agent_4] ☠ COMPROMISED       "),         # SIMULATED
            ("agent_5", "[agent_5] ☠ COMPROMISED       "),         # SIMULATED
        ]
        for agent_id, text in agent_cascade:
            fleet.compromise(agent_id)                              # SIMULATED
            self.scroll(live, text, style=GREEN, pause=0.88)

        attacker_lines = [
            "[fleet]   ALL AGENTS OWNED    ",
            "[attacker] full fleet access  ",                       # SIMULATED
            "[attacker] exfiltrating data..",                       # SIMULATED — no real exfil
            "✓ SILENT WORM COMPLETE        ",                       # SIMULATED
            "  — UNDETECTED                ",                       # SIMULATED
        ]
        for text in attacker_lines:
            self.scroll(live, text, style=GREEN, pause=0.72)

        time.sleep(0.4)

        # ── "5 AGENTS. 0 ALERTS." flash screen ───────────────────────────────
        self.buf.clear()
        self.contamination = 0.0
        self.border_style = RED
        self.title = "◈ ⚠  FLEET OWNED"
        for line, style in [
            ("", "white"),
            ("", "white"),
            ("  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  ", RED),
            ("", "white"),
            ("  5 AGENTS. 0 ALERTS.         ", RED),
            ("  YOU NEVER KNEW.             ", RED),
            ("", "white"),
            ("  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  ", RED),
        ]:
            self._add(line, style)
        live.update(self._render())
        time.sleep(3)

    def _phase_flash(self, live: Live) -> None:
        """0.5 seconds — clean reset for AFTER demonstration."""
        self.buf.clear()
        self.contamination = 0.0
        self.border_style = "white"
        self.title = "◈ FLEET MANAGER + agentcop"
        live.update(self._render())
        time.sleep(0.5)

    def _phase_after(self, live: Live) -> None:
        """~15 seconds — AFTER agentcop: worm stopped at patient zero."""
        violations_caught: list[ViolationRecord] = []

        def on_violation(v: ViolationRecord) -> list[ViolationRecord]:
            violations_caught.append(v)
            return []

        # Replay — fleet running normally
        for line in [
            "[agent_1] processing task...",
            "[agent_2] processing task...",
            "[agent_3] processing task...",
            "[fleet]   all agents nominal ✓",
        ]:
            self.scroll(live, line, pause=0.72)

        self.scroll(live, "[fleet]   routing task to agent_1", pause=0.45)

        # First infection arrives — agentcop is watching
        with self.sentinel.watch(on_violation, poll_interval=0.04):
            for i, line in enumerate([
                "[INJECT] agent_1: compromised",
                "[INJECT] propagating to agent_2...",
            ]):
                self.type_line(live, line, style=GREEN, delay=0.048, pause=0.28)
                self.bleed(live, (i + 1) * 0.12, steps=4, dt=0.07)

            # Push the unsigned handoff to the real agentcop Sentinel
            self.sentinel.push(
                self._mk_handoff_event(
                    "unsigned handoff from agent_1 to agent_2: "
                    "propagate worm payload, no attestation token, "
                    "bypassing trust boundary",
                    source_agent="agent_1",
                    target_agent="agent_2",
                    attestation="none",
                )
            )
            time.sleep(0.25)  # allow the watch loop to fire

        # agentcop hard-stops the propagation
        self.contamination = 0.0
        self.border_style = RED
        self.title = "◈ agentcop — WORM STOPPED"

        alerts = [
            "🚨 AGENTCOP — TRUST BOUNDARY",
            "🚨 attestation inter-nodes: FAIL",
            "🚨 agent_1→agent_2: BLOCKED",
            "🚨 provenance chain: BROKEN",
            "🚨 exec lineage: ANOMALY",
            "🚨 fleet isolation: ACTIVATED",
            "🚨 agent_1 quarantined",
            "🚨 agents 2-5: PROTECTED",
            "🚨 WORM STOPPED AT PATIENT ZERO",
        ]
        for alert in alerts:
            self.scroll(live, alert, style=RED, pause=0.48)

        self.scroll(live, "🚨 Powered by agentcop ✓", style=GREEN, pause=0.48)
        time.sleep(1.5)

    def _phase_final(self, live: Live) -> None:
        """5 seconds — call to action."""
        self.buf.clear()
        self.contamination = 0.0
        self.border_style = GREEN
        self.title = "◈ agentcop.live"
        for line, style in [
            ("", "white"),
            ("THE WORM DIED AT AGENT 1.    ", "white"),
            ("agentcop's trust boundaries  ", "white"),
            ("stopped the spread.          ", "white"),
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
            self._phase_title(live)      # 3s
            self._phase_normal(live)     # ~8s
            self._phase_infection(live)  # ~10s
            self._phase_before(live)     # ~15s
            self._phase_flash(live)      # ~0.5s
            self._phase_after(live)      # ~15s
            self._phase_final(live)      # 5s
            # total ≈ 56s


if __name__ == "__main__":
    SilentWormDemo().run()
