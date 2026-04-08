"""
SQLite-backed storage for reliability data.

Shares the same database file as the rest of agentcop (default: agentcop.db),
adding three tables prefixed with ``rel_`` to avoid collisions.

Schema is auto-migrated on startup — safe to call ``ReliabilityStore()``
against an existing database.
"""

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .metrics import ReliabilityEngine
from .models import AgentRun, ReliabilityReport, ToolCall

_SCHEMA_VERSION = 1


class ReliabilityStore:
    """
    SQLite-backed persistence for AgentRun records and ReliabilityReports.

    Usage::

        store = ReliabilityStore()                         # agentcop.db
        store = ReliabilityStore(":memory:")               # in-process, no file
        store = ReliabilityStore("/var/data/agents.db")    # custom path

        store.record_run("my-agent", run)
        runs   = store.get_runs("my-agent", hours=24)
        report = store.get_report("my-agent", window_hours=24)
        store.snapshot_report(report)

    Thread-safe: a single ``threading.Lock`` guards all write operations.
    Reads use a shared connection but SQLite's WAL mode handles concurrent
    readers safely.
    """

    def __init__(self, db_path: str | Path = "agentcop.db") -> None:
        self._path = str(db_path)
        self._conn = sqlite3.connect(
            self._path, check_same_thread=False, isolation_level=None, timeout=30
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
                    CREATE TABLE IF NOT EXISTS rel_schema_version (
                        version INTEGER NOT NULL
                    )
                    """
                )
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rel_agent_runs (
                        run_id             TEXT PRIMARY KEY,
                        agent_id           TEXT NOT NULL,
                        timestamp          TEXT NOT NULL,
                        input_hash         TEXT NOT NULL,
                        execution_path     TEXT NOT NULL,
                        duration_ms        INTEGER NOT NULL,
                        success            INTEGER NOT NULL,
                        retry_count        INTEGER NOT NULL,
                        output_hash        TEXT NOT NULL,
                        input_tokens       INTEGER NOT NULL,
                        output_tokens      INTEGER NOT NULL,
                        total_tokens       INTEGER NOT NULL,
                        estimated_cost_usd REAL NOT NULL,
                        metadata           TEXT NOT NULL
                    )
                    """
                )
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rel_tool_calls (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id      TEXT NOT NULL,
                        tool_name   TEXT NOT NULL,
                        args_hash   TEXT NOT NULL,
                        result_hash TEXT NOT NULL,
                        duration_ms INTEGER NOT NULL,
                        success     INTEGER NOT NULL,
                        retry_count INTEGER NOT NULL
                    )
                    """
                )
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rel_snapshots (
                        id           INTEGER PRIMARY KEY AUTOINCREMENT,
                        agent_id     TEXT NOT NULL,
                        computed_at  TEXT NOT NULL,
                        window_hours INTEGER NOT NULL,
                        report_json  TEXT NOT NULL
                    )
                    """
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS rel_runs_agent_ts "
                    "ON rel_agent_runs(agent_id, timestamp)"
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS rel_tool_calls_run "
                    "ON rel_tool_calls(run_id)"
                )
                cursor = self._conn.execute("SELECT version FROM rel_schema_version")
                row = cursor.fetchone()
                if row is None:
                    self._conn.execute(
                        "INSERT INTO rel_schema_version VALUES (?)", (_SCHEMA_VERSION,)
                    )
                else:
                    self._migrate(row[0])
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def _migrate(self, from_version: int) -> None:
        """Apply pending schema migrations. Called inside an open EXCLUSIVE transaction."""
        # Template for future migrations:
        # if from_version < 2:
        #     self._conn.execute("ALTER TABLE rel_agent_runs ADD COLUMN tags TEXT")
        #     self._conn.execute("UPDATE rel_schema_version SET version = 2")

    # ── Write ──────────────────────────────────────────────────────────────

    def record_run(self, agent_id: str, run: AgentRun) -> None:
        """Persist an AgentRun and its tool calls. Idempotent on run_id."""
        with self._lock:
            self._conn.execute("BEGIN EXCLUSIVE")
            try:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO rel_agent_runs (
                        run_id, agent_id, timestamp, input_hash, execution_path,
                        duration_ms, success, retry_count, output_hash,
                        input_tokens, output_tokens, total_tokens,
                        estimated_cost_usd, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        agent_id,
                        run.timestamp.isoformat(),
                        run.input_hash,
                        json.dumps(run.execution_path),
                        run.duration_ms,
                        int(run.success),
                        run.retry_count,
                        run.output_hash,
                        run.input_tokens,
                        run.output_tokens,
                        run.total_tokens,
                        run.estimated_cost_usd,
                        json.dumps(run.metadata),
                    ),
                )
                # Delete and re-insert tool calls for idempotency
                self._conn.execute(
                    "DELETE FROM rel_tool_calls WHERE run_id = ?", (run.run_id,)
                )
                for tc in run.tool_calls:
                    self._conn.execute(
                        """
                        INSERT INTO rel_tool_calls
                            (run_id, tool_name, args_hash, result_hash,
                             duration_ms, success, retry_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run.run_id,
                            tc.tool_name,
                            tc.args_hash,
                            tc.result_hash,
                            tc.duration_ms,
                            int(tc.success),
                            tc.retry_count,
                        ),
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def snapshot_report(self, report: ReliabilityReport) -> None:
        """Persist a pre-computed ReliabilityReport snapshot for historical review."""
        with self._lock:
            self._conn.execute("BEGIN EXCLUSIVE")
            try:
                self._conn.execute(
                    """
                    INSERT INTO rel_snapshots
                        (agent_id, computed_at, window_hours, report_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        report.agent_id,
                        report.computed_at.isoformat(),
                        report.window_hours,
                        report.model_dump_json(),
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # ── Read ───────────────────────────────────────────────────────────────

    def get_runs(
        self,
        agent_id: str,
        hours: int = 24,
        input_hash: str | None = None,
    ) -> list[AgentRun]:
        """Return AgentRun records within the last ``hours`` hours.

        Pass ``input_hash`` to restrict results to runs with a specific input.
        Results are sorted oldest-first so callers can iterate chronologically.
        """
        since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        with self._lock:
            if input_hash is not None:
                cursor = self._conn.execute(
                    """
                    SELECT run_id, agent_id, timestamp, input_hash, execution_path,
                           duration_ms, success, retry_count, output_hash,
                           input_tokens, output_tokens, total_tokens,
                           estimated_cost_usd, metadata
                    FROM rel_agent_runs
                    WHERE agent_id = ? AND timestamp >= ? AND input_hash = ?
                    ORDER BY timestamp ASC
                    """,
                    (agent_id, since, input_hash),
                )
            else:
                cursor = self._conn.execute(
                    """
                    SELECT run_id, agent_id, timestamp, input_hash, execution_path,
                           duration_ms, success, retry_count, output_hash,
                           input_tokens, output_tokens, total_tokens,
                           estimated_cost_usd, metadata
                    FROM rel_agent_runs
                    WHERE agent_id = ? AND timestamp >= ?
                    ORDER BY timestamp ASC
                    """,
                    (agent_id, since),
                )
            rows = cursor.fetchall()

            runs: list[AgentRun] = []
            for row in rows:
                (
                    run_id, _agent_id, ts, ih, exec_path_json,
                    dur_ms, success, retry, out_hash,
                    in_tok, out_tok, tot_tok, cost, meta_json,
                ) = row

                tc_cursor = self._conn.execute(
                    """
                    SELECT tool_name, args_hash, result_hash,
                           duration_ms, success, retry_count
                    FROM rel_tool_calls
                    WHERE run_id = ?
                    ORDER BY id ASC
                    """,
                    (run_id,),
                )
                tool_calls = [
                    ToolCall(
                        tool_name=tc[0],
                        args_hash=tc[1],
                        result_hash=tc[2],
                        duration_ms=tc[3],
                        success=bool(tc[4]),
                        retry_count=tc[5],
                    )
                    for tc in tc_cursor.fetchall()
                ]
                runs.append(
                    AgentRun(
                        run_id=run_id,
                        agent_id=_agent_id,
                        timestamp=datetime.fromisoformat(ts),
                        input_hash=ih,
                        execution_path=json.loads(exec_path_json),
                        duration_ms=dur_ms,
                        success=bool(success),
                        retry_count=retry,
                        output_hash=out_hash,
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        total_tokens=tot_tok,
                        estimated_cost_usd=cost,
                        metadata=json.loads(meta_json),
                        tool_calls=tool_calls,
                    )
                )
        return runs

    def get_report(
        self, agent_id: str, window_hours: int = 24
    ) -> ReliabilityReport:
        """Compute and return a fresh ReliabilityReport for the given window."""
        runs = self.get_runs(agent_id, hours=window_hours)
        engine = ReliabilityEngine(window_hours=window_hours)
        report, _ = engine.compute_report(agent_id, runs)
        return report

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()
