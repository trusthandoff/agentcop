"""
AgentIdentity — verifiable agent identity with trust scoring and behavioral drift detection.

Usage::

    from agentcop import Sentinel, AgentIdentity

    # Register an agent
    identity = AgentIdentity.register(
        agent_id="my-agent-v1",
        code=agent_function,
        metadata={"framework": "langgraph", "version": "1.0"},
    )

    # Attach to Sentinel for automatic event enrichment and drift monitoring
    sentinel = Sentinel()
    sentinel.attach_identity(identity)

    # Events are now auto-enriched with agent identity + trust score
    sentinel.push(SentinelEvent(...))
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .event import SentinelEvent, ViolationRecord

# Type alias for violation hooks in Sentinel
ViolationHook = Callable[[ViolationRecord], list[ViolationRecord]]

# Current SQLite schema version — bump when adding migrations below.
_SCHEMA_VERSION = 1


@dataclass
class DriftConfig:
    """Configurable thresholds for behavioral drift detection.

    Pass to :meth:`AgentIdentity.register` or :class:`AgentIdentity.__init__` to
    override the defaults::

        identity = AgentIdentity.register(
            agent_id="my-agent",
            drift_config=DriftConfig(slow_execution_factor=5.0),
        )
    """

    slow_execution_factor: float = 3.0
    """Trigger ``slow_execution`` drift when ``exec_time > factor * baseline_avg``."""


@dataclass
class BehavioralBaseline:
    """Behavioral profile built from the first 10+ executions.

    After the baseline is established, :class:`AgentIdentity` uses it to detect
    drift — new tools, slow executions, or contact with previously-unseen agents.
    """

    tools_called: dict[str, float] = field(default_factory=dict)
    """tool_name → relative frequency across baseline executions."""
    avg_execution_time: float | None = None
    typical_output_size_min: int | None = None
    typical_output_size_max: int | None = None
    known_agents: set[str] = field(default_factory=set)
    execution_count: int = 0


# ---------------------------------------------------------------------------
# Storage backends
# ---------------------------------------------------------------------------


class IdentityStore:
    """Abstract storage backend for :class:`AgentIdentity` instances.

    Subclass or replace with :class:`InMemoryIdentityStore` (default) or
    :class:`SQLiteIdentityStore` (persistent, zero config).
    """

    def save(self, identity: AgentIdentity) -> None:
        raise NotImplementedError

    def load(self, agent_id: str) -> AgentIdentity | None:
        raise NotImplementedError

    def delete(self, agent_id: str) -> None:
        raise NotImplementedError

    def list_agents(self) -> list[str]:
        raise NotImplementedError


class InMemoryIdentityStore(IdentityStore):
    """Thread-safe in-memory store.  Fast, no setup, not persistent.  Good for tests."""

    def __init__(self) -> None:
        self._data: dict[str, AgentIdentity] = {}
        self._lock = threading.Lock()

    def save(self, identity: AgentIdentity) -> None:
        with self._lock:
            self._data[identity.agent_id] = identity

    def load(self, agent_id: str) -> AgentIdentity | None:
        with self._lock:
            return self._data.get(agent_id)

    def delete(self, agent_id: str) -> None:
        with self._lock:
            self._data.pop(agent_id, None)

    def list_agents(self) -> list[str]:
        with self._lock:
            return list(self._data.keys())


class SQLiteIdentityStore(IdentityStore):
    """SQLite-backed persistent store.  Zero config — defaults to ``agentcop.db``."""

    def __init__(self, db_path: str | Path = "agentcop.db") -> None:
        self._path = Path(db_path)
        # isolation_level=None → autocommit; we manage transactions explicitly
        # with BEGIN EXCLUSIVE for atomic, cross-process-safe writes.
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, isolation_level=None, timeout=30
        )
        self._lock = threading.Lock()
        self._init_db()

    # ── Schema init & migrations ───────────────────────────────────────────

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute("BEGIN EXCLUSIVE")
            try:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_version (
                        version INTEGER NOT NULL
                    )
                    """
                )
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS identities (
                        agent_id   TEXT PRIMARY KEY,
                        data       TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                cursor = self._conn.execute("SELECT version FROM schema_version")
                row = cursor.fetchone()
                if row is None:
                    self._conn.execute("INSERT INTO schema_version VALUES (?)", (_SCHEMA_VERSION,))
                else:
                    self._migrate(row[0])
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def _migrate(self, from_version: int) -> None:
        """Apply any pending schema migrations.  Called inside an open EXCLUSIVE transaction."""
        # Template for future migrations:
        # if from_version < 2:
        #     self._conn.execute("ALTER TABLE identities ADD COLUMN tags TEXT")
        #     self._conn.execute("UPDATE schema_version SET version = 2")

    def save(self, identity: AgentIdentity) -> None:
        data = json.dumps(identity.to_dict())
        with self._lock:
            self._conn.execute("BEGIN EXCLUSIVE")
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO identities (agent_id, data, updated_at)"
                    " VALUES (?, ?, ?)",
                    (identity.agent_id, data, datetime.now(UTC).isoformat()),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def load(self, agent_id: str) -> AgentIdentity | None:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT data FROM identities WHERE agent_id = ?", (agent_id,)
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return AgentIdentity.from_dict(json.loads(row[0]), store=self)

    def delete(self, agent_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM identities WHERE agent_id = ?", (agent_id,))
            self._conn.commit()

    def list_agents(self) -> list[str]:
        with self._lock:
            cursor = self._conn.execute("SELECT agent_id FROM identities")
            return [row[0] for row in cursor.fetchall()]

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# AgentIdentity
# ---------------------------------------------------------------------------


class AgentIdentity:
    """Verifiable agent identity with cryptographic fingerprint, trust score, and drift detection.

    Create via :meth:`register`::

        identity = AgentIdentity.register(
            agent_id="my-agent-v1",
            code=my_function,
            metadata={"framework": "langgraph"},
        )

    Attach to a :class:`~agentcop.Sentinel` for automatic event enrichment and
    drift monitoring::

        sentinel.attach_identity(identity)

    In watch mode, pass :meth:`observe_violation` as the callback to keep the
    trust score current::

        with sentinel.watch(identity.observe_violation):
            sentinel.push(event)
    """

    def __init__(
        self,
        agent_id: str,
        fingerprint: str,
        metadata: dict[str, Any] | None = None,
        store: IdentityStore | None = None,
        *,
        trust_score: float = 50.0,
        status: Literal["active", "suspended", "flagged"] = "active",
        created_at: datetime | None = None,
        baseline: BehavioralBaseline | None = None,
        drift_config: DriftConfig | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.fingerprint = fingerprint
        self.created_at = created_at or datetime.now(UTC)
        self.metadata: dict[str, Any] = metadata or {}
        self.trust_score = trust_score
        self.status: Literal["active", "suspended", "flagged"] = status
        self.baseline = baseline
        self.drift_config: DriftConfig = drift_config or DriftConfig()

        self._lock = threading.Lock()
        self._store = store or InMemoryIdentityStore()
        self._execution_count = 0
        self._consecutive_clean = 0
        self._violation_type_counts: dict[str, int] = {}
        self._execution_buffer: list[dict[str, Any]] = []
        # Optional badge store set by generate_badge(); used for auto-revoke.
        self._badge_store: Any | None = None

    # ── Registration ──────────────────────────────────────────────────────

    @classmethod
    def register(
        cls,
        agent_id: str,
        code: Any = None,
        config: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        store: IdentityStore | None = None,
        *,
        trust_score: float = 50.0,
        status: Literal["active", "suspended", "flagged"] = "active",
        drift_config: DriftConfig | None = None,
    ) -> AgentIdentity:
        """Create and persist a new agent identity.

        Args:
            agent_id: Human-readable agent name (e.g. ``"my-agent-v1"``).
            code: Agent code to fingerprint — a callable, raw source string, or
                  :class:`pathlib.Path` to a file.
            config: Extra config dict mixed into the fingerprint.
            metadata: Arbitrary key/value metadata stored alongside the identity.
            store: Storage backend.  Defaults to :class:`InMemoryIdentityStore`.
            drift_config: Thresholds for behavioral drift detection.  Defaults to
                          :class:`DriftConfig` with factory defaults.

        Returns:
            A new :class:`AgentIdentity` with ``trust_score=50`` and
            ``status="active"``, already persisted to *store*.
        """
        fingerprint = cls._compute_fingerprint(code, config)
        resolved_store = store or InMemoryIdentityStore()
        identity = cls(
            agent_id=agent_id,
            fingerprint=fingerprint,
            metadata=metadata or {},
            store=resolved_store,
            trust_score=trust_score,
            status=status,
            drift_config=drift_config,
        )
        resolved_store.save(identity)
        return identity

    @staticmethod
    def _compute_fingerprint(
        code: Any = None,
        config: dict[str, Any] | None = None,
    ) -> str:
        """Return SHA-256 hex of *code* source (if any) + *config* JSON (if any).

        Returns the all-zeros digest when neither argument is provided so that
        ``AgentIdentity.register(agent_id="x")`` still produces a stable,
        deterministic fingerprint.
        """
        h = hashlib.sha256()
        if code is not None:
            if callable(code):
                try:
                    source = inspect.getsource(code)
                    h.update(source.encode())
                except (OSError, TypeError):
                    h.update(repr(code).encode())
            elif isinstance(code, Path):
                # Read as UTF-8 source text so .py and .pyc paths both hash
                # the human-readable source, not raw bytecode.
                h.update(code.read_text(encoding="utf-8").encode())
            elif isinstance(code, str):
                h.update(code.encode())
        if config is not None:
            h.update(json.dumps(config, sort_keys=True).encode())
        return h.hexdigest()

    # ── Snapshot for event enrichment ─────────────────────────────────────

    def as_event_attributes(self) -> dict[str, Any]:
        """Return a thread-safe snapshot of identity fields for event enrichment.

        Called by :meth:`Sentinel.push` when this identity is attached.
        """
        with self._lock:
            return {
                "agent_id": self.agent_id,
                "trust_score": self.trust_score,
                "fingerprint": self.fingerprint,
                "identity_status": self.status,
            }

    # ── Trust score evolution ─────────────────────────────────────────────

    def _adjust_trust(self, delta: float) -> None:
        """Clamp-adjust trust score.  Must be called under ``self._lock``."""
        self.trust_score = max(0.0, min(100.0, self.trust_score + delta))

    def record_execution(
        self,
        execution_data: dict[str, Any] | None = None,
        *,
        event_id: str = "",
    ) -> list[ViolationRecord]:
        """Record one successful execution and update trust score / baseline.

        *execution_data* may contain any of:

        - ``tools_called`` (``list[str]``) — tools invoked during this execution
        - ``execution_time`` (``float``) — wall-clock seconds
        - ``output_size`` (``int``) — response size in bytes
        - ``agents_contacted`` (``list[str]``) — other agent IDs contacted

        *event_id* is the :attr:`~agentcop.SentinelEvent.event_id` of the event that
        triggered this execution recording.  When supplied, drift violations produced by
        this call will link their ``source_event_id`` to that event.

        Returns any drift :class:`~agentcop.ViolationRecord` s detected (empty
        list until the baseline is established after 10 executions).
        """
        execution_data = execution_data or {}
        additional: list[ViolationRecord] = []

        with self._lock:
            self._execution_count += 1
            self._consecutive_clean += 1
            self._adjust_trust(+1.0)

            # Long clean streak bonus
            if self._consecutive_clean % 50 == 0:
                self._adjust_trust(+10.0)

            self._execution_buffer.append(execution_data)

            if self._execution_count == 10:
                self._build_baseline()
            elif self._execution_count > 10 and self.baseline is not None:
                additional = self._check_drift(execution_data, event_id=event_id)
                for _ in additional:
                    self._adjust_trust(-5.0)

            if self.trust_score < 30 and self.status == "active":
                self.status = "flagged"
                additional.append(self._make_flagged_violation(event_id))

        return additional

    def observe_violation(self, violation: ViolationRecord) -> list[ViolationRecord]:
        """Update trust score when a violation is observed on this agent's events.

        Wire this to :meth:`Sentinel.watch` for automatic trust tracking::

            with sentinel.watch(identity.observe_violation):
                sentinel.push(event)

        Violation → trust delta:

        - ``WARN``     → −5
        - ``ERROR``    → −10
        - ``CRITICAL`` → −20
        - Repeated same violation type → −10 extra

        Returns any *additional* violations generated (e.g. an
        ``agent_flagged`` record when trust drops below 30).
        """
        additional: list[ViolationRecord] = []

        with self._lock:
            self._consecutive_clean = 0

            if violation.severity == "WARN":
                delta = -5.0
            elif violation.severity == "ERROR":
                delta = -10.0
            else:  # CRITICAL
                delta = -20.0

            vtype = violation.violation_type
            self._violation_type_counts[vtype] = self._violation_type_counts.get(vtype, 0) + 1
            if self._violation_type_counts[vtype] > 1:
                delta -= 10.0  # repeated-violation extra penalty

            self._adjust_trust(delta)

            if self.trust_score < 30 and self.status == "active":
                self.status = "flagged"
                additional.append(self._make_flagged_violation(violation.source_event_id))

        # Auto-revoke any active badge when trust drops below threshold
        if self.trust_score < 30 and self._badge_store is not None:
            self._revoke_active_badge("trust_below_30")

        return additional

    def _make_flagged_violation(self, triggering_event_id: str = "") -> ViolationRecord:
        """Build the ``agent_flagged`` violation.

        *triggering_event_id* is the ``source_event_id`` of the violation (or execution
        event) that caused the trust score to drop below 30.  Falls back to a synthetic
        ``identity-<agent_id>`` sentinel when no event ID is available.
        """
        return ViolationRecord(
            violation_type="agent_flagged",
            severity="CRITICAL",
            source_event_id=triggering_event_id or f"identity-{self.agent_id}",
            detail={
                "agent_id": self.agent_id,
                "trust_score": self.trust_score,
                "reason": "Trust score dropped below 30",
            },
        )

    # ── Baseline ──────────────────────────────────────────────────────────

    def _build_baseline(self) -> None:
        """Build baseline from execution buffer.  Must be called under ``self._lock``."""
        tools: dict[str, int] = {}
        times: list[float] = []
        sizes: list[int] = []
        agents: set[str] = set()

        for exec_data in self._execution_buffer:
            for tool in exec_data.get("tools_called", []):
                tools[tool] = tools.get(tool, 0) + 1
            t = exec_data.get("execution_time")
            if t is not None:
                times.append(float(t))
            s = exec_data.get("output_size")
            if s is not None:
                sizes.append(int(s))
            for agent in exec_data.get("agents_contacted", []):
                agents.add(agent)

        n = len(self._execution_buffer)
        self.baseline = BehavioralBaseline(
            tools_called={tool: count / n for tool, count in tools.items()},
            avg_execution_time=sum(times) / len(times) if times else None,
            typical_output_size_min=min(sizes) if sizes else None,
            typical_output_size_max=max(sizes) if sizes else None,
            known_agents=agents,
            execution_count=n,
        )

    # ── Drift detection ───────────────────────────────────────────────────

    def _check_drift(
        self, execution_data: dict[str, Any], *, event_id: str = ""
    ) -> list[ViolationRecord]:
        """Return drift violations for *execution_data* vs current baseline.

        *event_id* is propagated to each ``ViolationRecord.source_event_id`` so
        violations link back to the specific event that triggered the check.

        Must be called under ``self._lock``.
        """
        if self.baseline is None:
            return []

        violations: list[ViolationRecord] = []
        baseline = self.baseline
        source_id = event_id or f"identity-{self.agent_id}"

        for tool in execution_data.get("tools_called", []):
            if tool not in baseline.tools_called:
                violations.append(
                    ViolationRecord(
                        violation_type="identity_drift",
                        severity="WARN",
                        source_event_id=source_id,
                        detail={
                            "agent_id": self.agent_id,
                            "drift_type": "new_tool",
                            "tool": tool,
                        },
                    )
                )

        exec_time = execution_data.get("execution_time")
        if (
            exec_time is not None
            and baseline.avg_execution_time is not None
            and exec_time > self.drift_config.slow_execution_factor * baseline.avg_execution_time
        ):
            violations.append(
                ViolationRecord(
                    violation_type="identity_drift",
                    severity="WARN",
                    source_event_id=source_id,
                    detail={
                        "agent_id": self.agent_id,
                        "drift_type": "slow_execution",
                        "execution_time": exec_time,
                        "baseline_avg": baseline.avg_execution_time,
                    },
                )
            )

        for agent in execution_data.get("agents_contacted", []):
            if agent not in baseline.known_agents:
                violations.append(
                    ViolationRecord(
                        violation_type="identity_drift",
                        severity="WARN",
                        source_event_id=source_id,
                        detail={
                            "agent_id": self.agent_id,
                            "drift_type": "new_agent_contact",
                            "contacted_agent": agent,
                        },
                    )
                )

        return violations

    def make_drift_detector(self) -> Callable[[SentinelEvent], ViolationRecord | None]:
        """Return a pure :data:`~agentcop.ViolationDetector` that checks for drift.

        The detector reads ``tools_called``, ``execution_time``, and
        ``agents_contacted`` from ``event.attributes`` and compares them against
        the established baseline.

        :meth:`Sentinel.attach_identity` registers this detector automatically.
        You can also register it manually::

            sentinel.register_detector(identity.make_drift_detector())
        """
        identity = self

        def _detect_drift(event: SentinelEvent) -> ViolationRecord | None:
            if event.attributes.get("agent_id") != identity.agent_id:
                return None

            with identity._lock:
                if identity.baseline is None:
                    return None
                baseline = identity.baseline
                factor = identity.drift_config.slow_execution_factor

            for tool in event.attributes.get("tools_called", []):
                if tool not in baseline.tools_called:
                    return ViolationRecord(
                        violation_type="identity_drift",
                        severity="WARN",
                        source_event_id=event.event_id,
                        trace_id=event.trace_id,
                        detail={
                            "agent_id": identity.agent_id,
                            "drift_type": "new_tool",
                            "tool": tool,
                        },
                    )

            exec_time = event.attributes.get("execution_time")
            if (
                exec_time is not None
                and baseline.avg_execution_time is not None
                and exec_time > factor * baseline.avg_execution_time
            ):
                return ViolationRecord(
                    violation_type="identity_drift",
                    severity="WARN",
                    source_event_id=event.event_id,
                    trace_id=event.trace_id,
                    detail={
                        "agent_id": identity.agent_id,
                        "drift_type": "slow_execution",
                        "execution_time": exec_time,
                        "baseline_avg": baseline.avg_execution_time,
                    },
                )

            for agent in event.attributes.get("agents_contacted", []):
                if agent not in baseline.known_agents:
                    return ViolationRecord(
                        violation_type="identity_drift",
                        severity="WARN",
                        source_event_id=event.event_id,
                        trace_id=event.trace_id,
                        detail={
                            "agent_id": identity.agent_id,
                            "drift_type": "new_agent_contact",
                            "contacted_agent": agent,
                        },
                    )

            return None

        return _detect_drift

    # ── Serialization ─────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize identity state to a JSON-compatible dict."""
        with self._lock:
            d: dict[str, Any] = {
                "agent_id": self.agent_id,
                "fingerprint": self.fingerprint,
                "created_at": self.created_at.isoformat(),
                "trust_score": self.trust_score,
                "status": self.status,
                "metadata": self.metadata,
                "execution_count": self._execution_count,
                "consecutive_clean": self._consecutive_clean,
                "violation_type_counts": dict(self._violation_type_counts),
            }
            if self.baseline is not None:
                d["baseline"] = {
                    "tools_called": self.baseline.tools_called,
                    "avg_execution_time": self.baseline.avg_execution_time,
                    "typical_output_size_min": self.baseline.typical_output_size_min,
                    "typical_output_size_max": self.baseline.typical_output_size_max,
                    "known_agents": sorted(self.baseline.known_agents),
                    "execution_count": self.baseline.execution_count,
                }
        return d

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        store: IdentityStore | None = None,
    ) -> AgentIdentity:
        """Deserialize an identity from a dict (as produced by :meth:`to_dict`)."""
        baseline = None
        if "baseline" in data:
            b = data["baseline"]
            baseline = BehavioralBaseline(
                tools_called=b["tools_called"],
                avg_execution_time=b["avg_execution_time"],
                typical_output_size_min=b["typical_output_size_min"],
                typical_output_size_max=b["typical_output_size_max"],
                known_agents=set(b["known_agents"]),
                execution_count=b["execution_count"],
            )
        identity = cls(
            agent_id=data["agent_id"],
            fingerprint=data["fingerprint"],
            metadata=data.get("metadata", {}),
            store=store,
            trust_score=data.get("trust_score", 50.0),
            status=data.get("status", "active"),
            created_at=datetime.fromisoformat(data["created_at"]),
            baseline=baseline,
        )
        identity._execution_count = data.get("execution_count", 0)
        identity._consecutive_clean = data.get("consecutive_clean", 0)
        identity._violation_type_counts = dict(data.get("violation_type_counts", {}))
        return identity

    # ── Badge integration ─────────────────────────────────────────────────

    def _revoke_active_badge(self, reason: str) -> None:
        """Revoke the latest active badge for this agent.  Must NOT hold ``self._lock``."""
        if self._badge_store is None:
            return
        try:
            latest = self._badge_store.load_latest(self.agent_id)
            if latest is not None and not latest.revoked:
                self._badge_store.revoke(latest.badge_id, reason=reason)
        except Exception:
            pass  # badge revocation is best-effort

    def generate_badge(
        self,
        *,
        issuer: Any | None = None,
        store: Any | None = None,
        scan_count: int = 0,
    ) -> Any:
        """Generate a cryptographically signed security badge for this agent.

        Derives ``trust_score``, ``fingerprint``, and ``framework`` from the
        current identity state.  If ``trust_score`` is below 30, the badge is
        immediately revoked (``revocation_reason="trust_below_30"``).

        Requires ``agentcop[badge]`` (``pip install agentcop[badge]``).

        Args:
            issuer:     :class:`~agentcop.badge.BadgeIssuer` instance.  A fresh
                        in-memory issuer is created when not provided.
            store:      :class:`~agentcop.badge.BadgeStore` for persistence.
            scan_count: Total number of scans run so far (informational).

        Returns:
            :class:`~agentcop.badge.AgentBadge`
        """
        from .badge import BadgeIssuer, InMemoryBadgeStore

        target_store = store or InMemoryBadgeStore()
        _issuer = issuer or BadgeIssuer(store=target_store)

        with self._lock:
            trust = self.trust_score
            fp = self.fingerprint
            framework = self.metadata.get("framework", "generic")
            viol_counts = dict(self._violation_type_counts)

        # Build violation breakdown by severity key names
        violations = {
            "critical": viol_counts.get("critical", 0),
            "warning": viol_counts.get("warning", 0),
            "info": viol_counts.get("info", 0),
            "protected": viol_counts.get("protected", 0),
        }

        # Remember store for auto-revoke on future trust drops
        self._badge_store = target_store

        return _issuer.issue(
            agent_id=self.agent_id,
            fingerprint=fp,
            trust_score=trust,
            violations=violations,
            framework=framework,
            scan_count=scan_count,
            store=target_store,
        )

    def __repr__(self) -> str:
        return (
            f"AgentIdentity(agent_id={self.agent_id!r}, "
            f"trust_score={self.trust_score:.1f}, "
            f"status={self.status!r})"
        )
