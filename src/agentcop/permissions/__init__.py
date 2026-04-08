"""
Permissions — role-based tool access control and capability-based permission layer.

Two complementary APIs live here:

**Role-based (existing)** — ``PermissionSet`` + ``PermissionChecker``::

    from agentcop.permissions import PermissionSet, PermissionChecker, ToolPermission

    perms = PermissionSet()
    perms.grant("admin", ToolPermission(tool="*"))
    checker = PermissionChecker(perms)
    allowed, reason = checker.check("readonly", "file_read")

**Capability-based (new)** — ``ToolPermissionLayer``::

    from agentcop.permissions import ToolPermissionLayer, WritePermission, NetworkPermission

    layer = ToolPermissionLayer()
    layer.declare("my-agent", [
        WritePermission(paths=["/tmp/*", "/output/*"]),
        NetworkPermission(domains=["api.openai.com", "agentcop.live"]),
    ])
    result = layer.verify("my-agent", "file_write", {"path": "/tmp/out.txt"})
    # PermissionResult(granted=True, reason="path '/tmp/out.txt' matches '/tmp/*'")
"""

from __future__ import annotations

import fnmatch
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from agentcop.gate import ExecutionGate
    from agentcop.sentinel import Sentinel

__all__ = [
    # Role-based API
    "ToolPermission",
    "PermissionSet",
    "PermissionChecker",
    "PermissionDenied",
    # Capability-based API
    "PermissionResult",
    "Permission",
    "ReadPermission",
    "WritePermission",
    "NetworkPermission",
    "ExecutePermission",
    "ToolPermissionLayer",
]


# ---------------------------------------------------------------------------
# ToolPermission
# ---------------------------------------------------------------------------


@dataclass
class ToolPermission:
    """A single allow-or-deny rule for one tool (or ``"*"`` for all tools).

    Attributes:
        tool:        Tool name this rule applies to.  Use ``"*"`` as a wildcard
                     that matches any tool not covered by a more specific rule.
        allow:       ``True`` (default) to permit the tool; ``False`` to deny it.
        deny_reason: Message returned when *allow* is ``False``.
        conditions:  Optional list of ``(args: dict) -> bool`` callables.  All
                     must return ``True`` for the permission to be granted.  Only
                     evaluated when *allow* is ``True``.
    """

    tool: str
    allow: bool = True
    deny_reason: str = "tool not permitted"
    conditions: list[Any] = field(default_factory=list)

    def matches(self, tool_name: str) -> bool:
        """Return ``True`` if this rule applies to *tool_name*."""
        return self.tool == "*" or self.tool == tool_name

    def evaluate(self, args: dict[str, Any]) -> tuple[bool, str]:
        """Evaluate allow/deny for the given *args*.

        Returns:
            ``(allowed, reason)`` tuple.
        """
        if not self.allow:
            return False, self.deny_reason
        for condition in self.conditions:
            if not condition(args):
                return False, "permission condition not satisfied"
        return True, "tool allowed"


# ---------------------------------------------------------------------------
# PermissionSet
# ---------------------------------------------------------------------------


class PermissionSet:
    """Registry mapping roles to ordered lists of :class:`ToolPermission` rules.

    Rules are evaluated in insertion order.  The **first matching rule** wins.
    If no rule matches, the call is **denied** by default (deny-by-default posture).

    Thread-safe.
    """

    def __init__(self) -> None:
        # role → list of ToolPermission, checked in order
        self._rules: dict[str, list[ToolPermission]] = {}
        self._lock = threading.Lock()

    def grant(self, role: str, permission: ToolPermission) -> None:
        """Append *permission* to the rule list for *role*.

        Rules are evaluated in insertion order; more specific rules should be
        added before wildcard rules.
        """
        with self._lock:
            self._rules.setdefault(role, []).append(permission)

    def revoke(self, role: str, tool_name: str) -> None:
        """Remove all rules for *tool_name* (exact match only) from *role*."""
        with self._lock:
            rules = self._rules.get(role, [])
            self._rules[role] = [r for r in rules if r.tool != tool_name]

    def get_rules(self, role: str) -> list[ToolPermission]:
        """Return a snapshot of rules for *role* (empty list if role unknown)."""
        with self._lock:
            return list(self._rules.get(role, []))

    def list_roles(self) -> list[str]:
        """Return the names of all roles that have at least one rule."""
        with self._lock:
            return list(self._rules.keys())


# ---------------------------------------------------------------------------
# PermissionChecker
# ---------------------------------------------------------------------------


class PermissionDenied(Exception):
    """Raised by :meth:`PermissionChecker.enforce` when access is denied."""


class PermissionChecker:
    """Evaluates :class:`PermissionSet` rules for a given role + tool combination.

    Args:
        permission_set: The :class:`PermissionSet` to consult.
        default_allow:  When *no* rule matches a tool, allow (``True``) or deny
                        (``False``, the default — deny-by-default).
    """

    def __init__(
        self,
        permission_set: PermissionSet,
        *,
        default_allow: bool = False,
    ) -> None:
        self._perms = permission_set
        self._default_allow = default_allow

    def check(
        self,
        role: str,
        tool_name: str,
        args: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Evaluate rules for *role* + *tool_name*.

        Returns:
            ``(allowed: bool, reason: str)`` tuple.
        """
        args = args or {}
        for rule in self._perms.get_rules(role):
            if rule.matches(tool_name):
                return rule.evaluate(args)
        if self._default_allow:
            return True, "no matching rule; default allow"
        return False, f"no rule grants {role!r} access to {tool_name!r}"

    def is_allowed(
        self,
        role: str,
        tool_name: str,
        args: dict[str, Any] | None = None,
    ) -> bool:
        """Convenience wrapper — returns only the boolean result of :meth:`check`."""
        allowed, _ = self.check(role, tool_name, args)
        return allowed

    def enforce(
        self,
        role: str,
        tool_name: str,
        args: dict[str, Any] | None = None,
    ) -> None:
        """Like :meth:`check`, but raises :class:`PermissionDenied` on denial."""
        allowed, reason = self.check(role, tool_name, args)
        if not allowed:
            raise PermissionDenied(
                f"Role {role!r} is not permitted to call {tool_name!r}: {reason}"
            )


# ===========================================================================
# Capability-based permission layer
# ===========================================================================


# ---------------------------------------------------------------------------
# PermissionResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PermissionResult:
    """Result returned by :meth:`ToolPermissionLayer.verify`.

    Attributes:
        granted: ``True`` when the tool call is permitted.
        reason:  Human-readable explanation of the decision.
    """

    granted: bool
    reason: str


# ---------------------------------------------------------------------------
# Permission base + concrete types
# ---------------------------------------------------------------------------


class Permission:
    """Base class for capability-scoped permissions.

    Each concrete subclass declares a default set of tool names it covers and
    implements :meth:`check_scope` to validate call arguments against the
    permission's scope restrictions.

    Subclass to add custom capability types::

        class S3Permission(Permission):
            _default_tools = frozenset({"s3_put", "s3_get"})

            def __init__(self, buckets: list[str], **kw) -> None:
                super().__init__(**kw)
                self.buckets = buckets

            def check_scope(self, args):
                bucket = args.get("bucket", "")
                if bucket in self.buckets:
                    return True, f"bucket {bucket!r} allowed"
                return False, f"bucket {bucket!r} not in {self.buckets}"
    """

    #: Override in subclasses to declare the tool names this permission covers.
    _default_tools: frozenset[str] = frozenset()

    def __init__(self, *, tool_names: list[str] | None = None) -> None:
        self._tool_names: frozenset[str] = (
            frozenset(tool_names) if tool_names is not None else self._default_tools
        )

    def covers(self, tool_name: str) -> bool:
        """Return ``True`` if this permission applies to *tool_name*."""
        return tool_name in self._tool_names

    def check_scope(self, args: dict[str, Any]) -> tuple[bool, str]:
        """Validate *args* against this permission's scope restrictions.

        Returns:
            ``(ok, reason)`` — ``ok`` is ``True`` when args are within scope.
        """
        return True, "no scope restrictions"


class ReadPermission(Permission):
    """Grants read access to files matching the supplied path globs.

    Covered tools (default): ``file_read``, ``read_file``, ``open_file``,
    ``read``, ``get_file``, ``load_file``, ``cat``.

    Args:
        paths:      List of ``fnmatch``-style path patterns (e.g. ``"/tmp/*"``).
                    An empty list permits any path.
        tool_names: Override the default covered tool names.

    The path is extracted from the ``path``, ``file_path``, ``file``, or
    ``filename`` argument key (first found).
    """

    _default_tools: frozenset[str] = frozenset(
        {"file_read", "read_file", "open_file", "read", "get_file", "load_file", "cat"}
    )

    def __init__(self, paths: list[str], *, tool_names: list[str] | None = None) -> None:
        super().__init__(tool_names=tool_names)
        self.paths = list(paths)

    def check_scope(self, args: dict[str, Any]) -> tuple[bool, str]:
        path = (
            args.get("path")
            or args.get("file_path")
            or args.get("file")
            or args.get("filename", "")
        )
        if not path:
            return True, "no path argument to restrict"
        if not self.paths:
            return True, "no path restrictions declared"
        for pattern in self.paths:
            if fnmatch.fnmatch(str(path), pattern):
                return True, f"path {path!r} matches allowed pattern {pattern!r}"
        return False, f"path {path!r} not within allowed paths: {self.paths}"


class WritePermission(Permission):
    """Grants write access to files matching the supplied path globs.

    Covered tools (default): ``file_write``, ``write_file``, ``save_file``,
    ``create_file``, ``write``, ``put_file``, ``append_file``.

    Args:
        paths:      List of ``fnmatch``-style path patterns (e.g. ``"/tmp/*"``).
        tool_names: Override the default covered tool names.
    """

    _default_tools: frozenset[str] = frozenset(
        {
            "file_write",
            "write_file",
            "save_file",
            "create_file",
            "write",
            "put_file",
            "append_file",
        }
    )

    def __init__(self, paths: list[str], *, tool_names: list[str] | None = None) -> None:
        super().__init__(tool_names=tool_names)
        self.paths = list(paths)

    def check_scope(self, args: dict[str, Any]) -> tuple[bool, str]:
        path = (
            args.get("path")
            or args.get("file_path")
            or args.get("file")
            or args.get("filename", "")
        )
        if not path:
            return True, "no path argument to restrict"
        if not self.paths:
            return True, "no path restrictions declared"
        for pattern in self.paths:
            if fnmatch.fnmatch(str(path), pattern):
                return True, f"path {path!r} matches allowed pattern {pattern!r}"
        return False, f"path {path!r} not within allowed paths: {self.paths}"


class NetworkPermission(Permission):
    """Grants outbound network access to the supplied domain allowlist.

    Covered tools (default): ``http_get``, ``http_post``, ``http_request``,
    ``fetch``, ``request``, ``web_search``, ``api_call``, ``curl``.

    Args:
        domains:    List of allowed hostnames (e.g. ``"api.openai.com"``).
                    Subdomains are NOT matched automatically — add them
                    explicitly or use :attr:`allow_subdomains`.
                    An empty list permits any domain.
        allow_subdomains: When ``True``, a domain entry ``"openai.com"`` also
                          permits ``"api.openai.com"``, ``"files.openai.com"``,
                          etc.
        tool_names: Override the default covered tool names.

    The domain is extracted from the ``url``, ``endpoint``, ``host``, or
    ``domain`` argument key (first found).  Full URLs are parsed with
    :mod:`urllib.parse` to extract the hostname.
    """

    _default_tools: frozenset[str] = frozenset(
        {
            "http_get",
            "http_post",
            "http_request",
            "fetch",
            "request",
            "web_search",
            "api_call",
            "curl",
        }
    )

    def __init__(
        self,
        domains: list[str],
        *,
        allow_subdomains: bool = False,
        tool_names: list[str] | None = None,
    ) -> None:
        super().__init__(tool_names=tool_names)
        self.domains = list(domains)
        self.allow_subdomains = allow_subdomains

    @staticmethod
    def _extract_host(value: str) -> str:
        """Return the hostname from a URL or bare hostname string."""
        if "://" in value:
            return urlparse(value).hostname or value
        # bare host or host:port
        return value.split(":")[0].split("/")[0]

    def _domain_allowed(self, host: str) -> bool:
        if not self.domains:
            return True
        for allowed in self.domains:
            if host == allowed:
                return True
            if self.allow_subdomains and host.endswith("." + allowed):
                return True
        return False

    def check_scope(self, args: dict[str, Any]) -> tuple[bool, str]:
        raw = args.get("url") or args.get("endpoint") or args.get("host") or args.get("domain", "")
        if not raw:
            return True, "no network target argument to restrict"
        if not self.domains:
            return True, "no domain restrictions declared"
        host = self._extract_host(str(raw))
        if self._domain_allowed(host):
            return True, f"host {host!r} is in the allowed domain list"
        return False, f"host {host!r} not in allowed domains: {self.domains}"


class ExecutePermission(Permission):
    """Grants command execution for the supplied command allowlist.

    Covered tools (default): ``shell_exec``, ``run_command``, ``execute``,
    ``bash``, ``exec``, ``subprocess``, ``run``.

    Args:
        commands:   List of allowed command names or exact strings.  Each entry
                    is compared against the leading token of the ``command`` /
                    ``cmd`` argument.  An empty list permits any command.
        tool_names: Override the default covered tool names.
    """

    _default_tools: frozenset[str] = frozenset(
        {"shell_exec", "run_command", "execute", "bash", "exec", "subprocess", "run"}
    )

    def __init__(self, commands: list[str], *, tool_names: list[str] | None = None) -> None:
        super().__init__(tool_names=tool_names)
        self.commands = list(commands)

    def check_scope(self, args: dict[str, Any]) -> tuple[bool, str]:
        cmd = args.get("command") or args.get("cmd", "")
        if not cmd:
            return True, "no command argument to restrict"
        if not self.commands:
            return True, "no command restrictions declared"
        # Compare the leading token of the command string
        cmd_str = str(cmd).strip()
        leading = cmd_str.split()[0] if cmd_str else ""
        if leading in self.commands or cmd_str in self.commands:
            return True, f"command {leading!r} is in the allowed list"
        return False, f"command {leading!r} not in allowed commands: {self.commands}"


# ---------------------------------------------------------------------------
# ToolPermissionLayer
# ---------------------------------------------------------------------------


class ToolPermissionLayer:
    """Agent-centric capability gate: declare upfront, verify at runtime.

    Each agent declares its capabilities once via :meth:`declare`.  Every
    subsequent :meth:`verify` call is checked against those declared permissions.
    Deny-by-default: an agent that never called :meth:`declare`, or that tries
    to use a tool not covered by any declared permission, is automatically
    blocked.

    Optionally attach a :class:`~agentcop.Sentinel` to emit
    ``"permission_violation"`` :class:`~agentcop.SentinelEvent` s whenever an
    unauthorized attempt is detected.

    Usage::

        from agentcop.permissions import ToolPermissionLayer, WritePermission, NetworkPermission

        layer = ToolPermissionLayer()
        layer.declare("my-agent", [
            WritePermission(paths=["/tmp/*", "/output/*"]),
            NetworkPermission(domains=["api.openai.com", "agentcop.live"]),
        ])

        result = layer.verify("my-agent", "file_write", {"path": "/tmp/report.csv"})
        # PermissionResult(granted=True, ...)

    Integration with :class:`~agentcop.gate.ExecutionGate`::

        from agentcop.gate import ExecutionGate

        gate = ExecutionGate()
        layer.attach_to_gate(gate, "my-agent")

        @gate.wrap
        def file_write(path, content): ...

    Args:
        sentinel: Optional :class:`~agentcop.Sentinel` instance.  When
                  provided, a ``SentinelEvent`` with
                  ``event_type="permission_violation"`` is pushed on every
                  denial.
    """

    def __init__(self, sentinel: Sentinel | None = None) -> None:
        self._permissions: dict[str, list[Permission]] = {}
        self._lock = threading.Lock()
        self._sentinel = sentinel

    # ── Declaration ───────────────────────────────────────────────────────

    def declare(self, agent_id: str, permissions: list[Permission]) -> None:
        """Register *agent_id*'s declared capabilities.

        Calling :meth:`declare` a second time replaces the previous declaration.

        Args:
            agent_id:    Unique agent identifier.
            permissions: Ordered list of :class:`Permission` objects describing
                         what the agent is allowed to do.
        """
        with self._lock:
            self._permissions[agent_id] = list(permissions)

    # ── Verification ──────────────────────────────────────────────────────

    def verify(
        self,
        agent_id: str,
        tool_name: str,
        args: dict[str, Any] | None = None,
    ) -> PermissionResult:
        """Check whether *agent_id* may call *tool_name* with *args*.

        Deny-by-default rules:

        1. Agent has no declared permissions → denied.
        2. No declared permission covers *tool_name* → denied.
        3. All covering permissions fail their scope check → denied.

        A single passing scope check is sufficient for the call to be granted.

        Args:
            agent_id:  Agent whose permissions to consult.
            tool_name: The tool being invoked.
            args:      Argument mapping for the call (used for scope checks).

        Returns:
            :class:`PermissionResult` with the final verdict.
        """
        args = args or {}
        with self._lock:
            permissions = list(self._permissions.get(agent_id, []))

        if not permissions:
            result = PermissionResult(
                granted=False,
                reason=f"agent {agent_id!r} has no declared permissions",
            )
            self._fire_violation(agent_id, tool_name, args, result)
            return result

        covering = [p for p in permissions if p.covers(tool_name)]
        if not covering:
            result = PermissionResult(
                granted=False,
                reason=f"tool {tool_name!r} not in {agent_id!r}'s declared capabilities",
            )
            self._fire_violation(agent_id, tool_name, args, result)
            return result

        # Try each covering permission; first pass wins.
        last_reason = ""
        for perm in covering:
            ok, reason = perm.check_scope(args)
            if ok:
                return PermissionResult(granted=True, reason=reason)
            last_reason = reason

        result = PermissionResult(granted=False, reason=last_reason)
        self._fire_violation(agent_id, tool_name, args, result)
        return result

    # ── Gate integration ──────────────────────────────────────────────────

    def as_gate_policy(self, agent_id: str, tool_name: str) -> Any:
        """Return a :class:`~agentcop.gate.ConditionalPolicy` for *tool_name*.

        The returned policy calls :meth:`verify` on each gate check.  Suitable
        for passing directly to
        :meth:`~agentcop.gate.ExecutionGate.register_policy`::

            gate.register_policy("file_write", layer.as_gate_policy("my-agent", "file_write"))
        """
        from agentcop.gate import ConditionalPolicy

        layer = self

        return ConditionalPolicy(
            allow_if=lambda args: layer.verify(agent_id, tool_name, args).granted,
            deny_reason=f"permission layer denied {tool_name!r} for agent {agent_id!r}",
        )

    def attach_to_gate(self, gate: ExecutionGate, agent_id: str) -> None:
        """Register gate policies for every tool declared by *agent_id*.

        After this call, the gate will consult the permission layer for each
        tool that the agent declared.  Tools not declared remain unregistered
        in the gate (subject to whatever the gate's existing policies say).

        Args:
            gate:     :class:`~agentcop.gate.ExecutionGate` to register on.
            agent_id: Agent whose declared tools should be registered.
        """
        with self._lock:
            permissions = list(self._permissions.get(agent_id, []))
        registered: set[str] = set()
        for perm in permissions:
            for tool_name in perm._tool_names:
                if tool_name not in registered:
                    gate.register_policy(tool_name, self.as_gate_policy(agent_id, tool_name))
                    registered.add(tool_name)

    # ── Sentinel event ────────────────────────────────────────────────────

    def _fire_violation(
        self,
        agent_id: str,
        tool_name: str,
        args: dict[str, Any],
        result: PermissionResult,
    ) -> None:
        """Push a ``permission_violation`` SentinelEvent if a Sentinel is attached."""
        if self._sentinel is None:
            return
        from agentcop.event import SentinelEvent

        event = SentinelEvent(
            event_id=f"perm-{uuid.uuid4()}",
            event_type="permission_violation",
            timestamp=datetime.now(UTC),
            severity="ERROR",
            body=(
                f"Permission denied: agent {agent_id!r} attempted {tool_name!r} — {result.reason}"
            ),
            source_system="agentcop.permissions",
            attributes={
                "agent_id": agent_id,
                "tool_name": tool_name,
                "reason": result.reason,
            },
        )
        self._sentinel.push(event)
