"""
Sandbox — execution environment constraints.

Restrict the paths, environment variables, and output sizes that agent tool
calls are allowed to touch.

Usage::

    from agentcop.sandbox import ExecutionSandbox, SandboxPolicy, SandboxViolation

    policy = SandboxPolicy(
        allowed_paths=["/tmp/", "/var/data/"],
        denied_env_vars=["AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY"],
        max_output_bytes=65_536,
    )

    sandbox = ExecutionSandbox(policy=policy)

    with sandbox:
        sandbox.assert_path_allowed("/tmp/output.txt")   # OK
        sandbox.assert_path_allowed("/etc/passwd")       # raises SandboxViolation
        sandbox.assert_env_allowed("HOME")               # OK
        sandbox.assert_env_allowed("AWS_SECRET_ACCESS_KEY")  # raises SandboxViolation
        sandbox.check_output_size(1024)                  # OK
"""

from __future__ import annotations

import builtins as _builtins_mod
import ctypes as _ctypes
import fnmatch as _fnmatch
import subprocess as _subprocess_mod
import threading
import urllib.request as _urllib_request_mod
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse as _urlparse

__all__ = [
    "SandboxPolicy",
    "SandboxViolation",
    "ExecutionSandbox",
    "SandboxTimeoutError",
    "AgentSandbox",
]

# ---------------------------------------------------------------------------
# Save real functions at import time (before any patching)
# ---------------------------------------------------------------------------

_REAL_OPEN = _builtins_mod.open
_REAL_URLOPEN = _urllib_request_mod.urlopen
_REAL_SUBPROCESS_RUN = _subprocess_mod.run

try:
    import requests as _requests_mod  # type: ignore[import-untyped]

    _HAS_REQUESTS = True
    _REAL_REQUESTS_REQUEST = _requests_mod.Session.request
except ImportError:
    _HAS_REQUESTS = False
    _REAL_REQUESTS_REQUEST = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Thread-local active-sandbox stack + global patch reference count
# ---------------------------------------------------------------------------

_tl = threading.local()
_patch_lock = threading.Lock()
_patch_ref: int = 0


def _active_sandbox() -> AgentSandbox | None:
    stack: list[AgentSandbox] = getattr(_tl, "stack", [])
    return stack[-1] if stack else None


def _push_sandbox(sb: AgentSandbox) -> None:
    global _patch_ref
    if not hasattr(_tl, "stack"):
        _tl.stack = []
    _tl.stack.append(sb)
    with _patch_lock:
        if _patch_ref == 0:
            _install_patches()
        _patch_ref += 1


def _pop_sandbox() -> None:
    global _patch_ref
    if getattr(_tl, "stack", None):
        _tl.stack.pop()
    with _patch_lock:
        if _patch_ref > 0:
            _patch_ref -= 1
            if _patch_ref == 0:
                _remove_patches()


# ---------------------------------------------------------------------------
# Patched stdlib functions — each checks the active sandbox for the
# current thread before delegating to the real implementation.
# ---------------------------------------------------------------------------


def _patched_open(file: Any, *args: Any, **kwargs: Any) -> Any:
    sb = _active_sandbox()
    if sb is not None and sb.allowed_paths and not isinstance(file, int):
        sb._check_path(str(file))
    return _REAL_OPEN(file, *args, **kwargs)


def _patched_urlopen(url: Any, *args: Any, **kwargs: Any) -> Any:
    sb = _active_sandbox()
    if sb is not None and sb.allowed_domains:
        raw = url if isinstance(url, str) else getattr(url, "full_url", str(url))
        sb._check_domain(raw)
    return _REAL_URLOPEN(url, *args, **kwargs)


def _patched_subprocess_run(cmd: Any, *args: Any, **kwargs: Any) -> Any:
    sb = _active_sandbox()
    if sb is not None and sb.allowed_paths and cmd:
        exe = cmd[0] if isinstance(cmd, (list, tuple)) else str(cmd).split()[0]
        if str(exe).startswith("/"):
            sb._check_path(str(exe))
    return _REAL_SUBPROCESS_RUN(cmd, *args, **kwargs)


def _patched_requests_request(self: Any, method: Any, url: Any, *args: Any, **kwargs: Any) -> Any:
    sb = _active_sandbox()
    if sb is not None and sb.allowed_domains:
        sb._check_domain(str(url))
    return _REAL_REQUESTS_REQUEST(self, method, url, *args, **kwargs)  # type: ignore[misc]


def _install_patches() -> None:
    _builtins_mod.open = _patched_open  # type: ignore[assignment]
    _urllib_request_mod.urlopen = _patched_urlopen  # type: ignore[assignment]
    _subprocess_mod.run = _patched_subprocess_run  # type: ignore[assignment]
    if _HAS_REQUESTS:
        _requests_mod.Session.request = _patched_requests_request  # type: ignore[assignment]


def _remove_patches() -> None:
    _builtins_mod.open = _REAL_OPEN
    _urllib_request_mod.urlopen = _REAL_URLOPEN
    _subprocess_mod.run = _REAL_SUBPROCESS_RUN
    if _HAS_REQUESTS:
        _requests_mod.Session.request = _REAL_REQUESTS_REQUEST  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# SandboxPolicy
# ---------------------------------------------------------------------------


@dataclass
class SandboxPolicy:
    """Constraints enforced by :class:`ExecutionSandbox`.

    Path rules follow a prefix-match convention — a path is permitted if it
    starts with at least one entry in *allowed_paths* (when the list is non-empty)
    and does not start with any entry in *denied_paths*.  Denied paths always take
    precedence over allowed paths.

    Attributes:
        allowed_paths:    Whitelist of path prefixes.  Empty list = all paths
                          permitted (subject to *denied_paths*).
        denied_paths:     Blacklist of path prefixes.  Always takes precedence.
        allowed_env_vars: Whitelist of env-var names.  Empty list = all permitted
                          (subject to *denied_env_vars*).
        denied_env_vars:  Blacklist of env-var names.  Always takes precedence.
        max_output_bytes: Maximum allowed output size in bytes.  ``None`` = unlimited.
    """

    allowed_paths: list[str] = field(default_factory=list)
    denied_paths: list[str] = field(default_factory=list)
    allowed_env_vars: list[str] = field(default_factory=list)
    denied_env_vars: list[str] = field(default_factory=list)
    max_output_bytes: int | None = None


# ---------------------------------------------------------------------------
# SandboxViolation
# ---------------------------------------------------------------------------


class SandboxViolation(Exception):
    """Raised when an operation violates the active :class:`SandboxPolicy`.

    Attributes:
        violation_type: Short slug describing what was violated
                        (``"path_denied"``, ``"env_denied"``, ``"output_too_large"``).
        detail:         Arbitrary key/value context for debugging.
    """

    def __init__(
        self,
        message: str,
        *,
        violation_type: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.violation_type = violation_type
        self.detail: dict[str, Any] = detail or {}


# ---------------------------------------------------------------------------
# ExecutionSandbox
# ---------------------------------------------------------------------------


class ExecutionSandbox:
    """Context manager that enforces a :class:`SandboxPolicy` for the duration of a block.

    The sandbox is re-entrant: nested ``with sandbox`` blocks are allowed and
    the policy remains active until the outermost block exits.

    Thread-safe: each thread's entry depth is tracked independently so multiple
    threads can share a single :class:`ExecutionSandbox` instance.

    Args:
        policy: The :class:`SandboxPolicy` to enforce.  Defaults to a
                fully-permissive policy (no restrictions).
    """

    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        self._policy = policy or SandboxPolicy()
        self._lock = threading.Lock()
        self._depth: dict[int, int] = {}  # thread-id → nesting depth

    @property
    def policy(self) -> SandboxPolicy:
        return self._policy

    # ── Context manager ───────────────────────────────────────────────────

    def __enter__(self) -> ExecutionSandbox:
        tid = threading.get_ident()
        with self._lock:
            self._depth[tid] = self._depth.get(tid, 0) + 1
        return self

    def __exit__(self, *_: object) -> None:
        tid = threading.get_ident()
        with self._lock:
            self._depth[tid] = max(0, self._depth.get(tid, 1) - 1)
            if self._depth[tid] == 0:
                del self._depth[tid]

    @property
    def active(self) -> bool:
        """``True`` when the current thread is inside a ``with sandbox`` block."""
        tid = threading.get_ident()
        with self._lock:
            return self._depth.get(tid, 0) > 0

    # ── Assertion helpers ─────────────────────────────────────────────────

    def assert_path_allowed(self, path: str) -> None:
        """Raise :class:`SandboxViolation` if *path* is not permitted by the policy.

        Checks are performed even outside a ``with`` block so the sandbox can
        also be used as a standalone validator.
        """
        policy = self._policy

        # Denied paths take precedence over allowed paths.
        for denied in policy.denied_paths:
            if path.startswith(denied):
                raise SandboxViolation(
                    f"Path {path!r} is explicitly denied (matches prefix {denied!r})",
                    violation_type="path_denied",
                    detail={"path": path, "matched_prefix": denied},
                )

        # If there is an allowlist, the path must match at least one entry.
        if policy.allowed_paths:
            for allowed in policy.allowed_paths:
                if path.startswith(allowed):
                    return
            raise SandboxViolation(
                f"Path {path!r} is not in the allowed path list",
                violation_type="path_not_allowed",
                detail={"path": path, "allowed_paths": policy.allowed_paths},
            )

    def assert_env_allowed(self, key: str) -> None:
        """Raise :class:`SandboxViolation` if the env-var *key* is not permitted."""
        policy = self._policy

        for denied in policy.denied_env_vars:
            if key == denied:
                raise SandboxViolation(
                    f"Environment variable {key!r} is explicitly denied",
                    violation_type="env_denied",
                    detail={"key": key},
                )

        if policy.allowed_env_vars and key not in policy.allowed_env_vars:
            raise SandboxViolation(
                f"Environment variable {key!r} is not in the allowed list",
                violation_type="env_not_allowed",
                detail={"key": key, "allowed_env_vars": policy.allowed_env_vars},
            )

    def check_output_size(self, size_bytes: int) -> None:
        """Raise :class:`SandboxViolation` if *size_bytes* exceeds the policy limit."""
        limit = self._policy.max_output_bytes
        if limit is not None and size_bytes > limit:
            raise SandboxViolation(
                f"Output size {size_bytes} bytes exceeds sandbox limit of {limit} bytes",
                violation_type="output_too_large",
                detail={"size_bytes": size_bytes, "max_output_bytes": limit},
            )


# ===========================================================================
# Active syscall-intercepting sandbox
# ===========================================================================


# ---------------------------------------------------------------------------
# SandboxTimeoutError
# ---------------------------------------------------------------------------


class SandboxTimeoutError(SandboxViolation):
    """Raised in the sandboxed thread when ``max_execution_time`` is exceeded.

    All parameters have defaults so that
    ``ctypes.pythonapi.PyThreadState_SetAsyncExc`` can construct it with no
    arguments.
    """

    def __init__(
        self,
        message: str = "Sandbox execution time limit exceeded",
        *,
        violation_type: str = "timeout",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, violation_type=violation_type, detail=detail)


# ---------------------------------------------------------------------------
# AgentSandbox
# ---------------------------------------------------------------------------


def _host_from_url(url_or_host: str) -> str:
    """Extract the hostname from a URL string or bare host[:port] string."""
    if "://" in url_or_host:
        return _urlparse(url_or_host).hostname or url_or_host
    return url_or_host.split(":")[0].split("/")[0]


class AgentSandbox:
    """Active enforcement sandbox for agent execution.

    When ``intercept_syscalls=True`` (default), monkey-patches
    ``builtins.open``, ``urllib.request.urlopen``, ``subprocess.run``, and
    (if installed) ``requests.Session.request`` to enforce path and domain
    restrictions.  Patches are installed globally but enforced only for
    threads that have entered this sandbox.

    If ``max_execution_time`` is set, a background timer raises
    :class:`SandboxTimeoutError` in the sandboxed thread when the deadline
    expires.

    Usage::

        from agentcop.sandbox import AgentSandbox

        with AgentSandbox(
            allowed_paths=["/tmp/*"],
            allowed_domains=["api.openai.com"],
            max_execution_time=30,
        ) as sandbox:
            result = agent.run(task)

    Inherit restrictions from a :class:`~agentcop.permissions.ToolPermissionLayer`::

        sandbox = AgentSandbox(
            permission_layer=layer,
            agent_id="my-agent",
            max_execution_time=60,
        )

    Args:
        intercept_syscalls:   Patch stdlib I/O calls to enforce restrictions.
                              Defaults to ``True``.
        allowed_paths:        ``fnmatch``-style glob patterns for permitted
                              file-system paths.  Empty list = no path
                              restrictions.
        allowed_domains:      Hostnames permitted for outbound network calls.
                              Subdomain matching is included (``"openai.com"``
                              also permits ``"api.openai.com"``).  Empty list
                              = no domain restrictions.
        max_execution_time:   Seconds allowed inside the ``with`` block.
                              Exceeded → :class:`SandboxTimeoutError` in the
                              sandboxed thread.  ``None`` = no limit.
        permission_layer:     Optional :class:`~agentcop.permissions.ToolPermissionLayer`
                              whose declared path/domain rules are merged in.
        agent_id:             Agent ID to look up in *permission_layer*.
    """

    def __init__(
        self,
        *,
        intercept_syscalls: bool = True,
        allowed_paths: list[str] | None = None,
        allowed_domains: list[str] | None = None,
        max_execution_time: int | None = None,
        permission_layer: Any | None = None,
        agent_id: str | None = None,
    ) -> None:
        self.intercept_syscalls = intercept_syscalls
        self.allowed_paths: list[str] = list(allowed_paths or [])
        self.allowed_domains: list[str] = list(allowed_domains or [])
        self.max_execution_time = max_execution_time
        self._thread_id: int | None = None
        self._timer: threading.Timer | None = None

        if permission_layer is not None and agent_id is not None:
            self._merge_from_layer(permission_layer, agent_id)

    # ── Context manager ───────────────────────────────────────────────────

    def __enter__(self) -> AgentSandbox:
        self._thread_id = threading.get_ident()
        if self.intercept_syscalls:
            _push_sandbox(self)
        if self.max_execution_time is not None:
            self._timer = threading.Timer(self.max_execution_time, self._do_timeout)
            self._timer.daemon = True
            self._timer.start()
        return self

    def __exit__(self, *_: object) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self.intercept_syscalls:
            _pop_sandbox()
        self._thread_id = None

    # ── Scope checks (called directly by the patched functions) ──────────

    def _check_path(self, path: str) -> None:
        """Raise :class:`SandboxViolation` if *path* is outside ``allowed_paths``."""
        if not self.allowed_paths:
            return
        for pattern in self.allowed_paths:
            if _fnmatch.fnmatch(path, pattern):
                return
        raise SandboxViolation(
            f"Path {path!r} blocked by sandbox",
            violation_type="path_blocked",
            detail={"path": path, "allowed_paths": self.allowed_paths},
        )

    def _check_domain(self, url_or_host: str) -> None:
        """Raise :class:`SandboxViolation` if the host is not in ``allowed_domains``."""
        if not self.allowed_domains:
            return
        host = _host_from_url(url_or_host)
        for domain in self.allowed_domains:
            if host == domain or host.endswith("." + domain):
                return
        raise SandboxViolation(
            f"Domain {host!r} blocked by sandbox",
            violation_type="domain_blocked",
            detail={"host": host, "allowed_domains": self.allowed_domains},
        )

    # ── Timeout ───────────────────────────────────────────────────────────

    def _do_timeout(self) -> None:
        """Deliver :class:`SandboxTimeoutError` to the sandboxed thread."""
        tid = self._thread_id
        if tid is not None:
            _ctypes.pythonapi.PyThreadState_SetAsyncExc(
                _ctypes.c_ulong(tid),
                _ctypes.py_object(SandboxTimeoutError),
            )

    # ── ToolPermissionLayer integration ───────────────────────────────────

    def _merge_from_layer(self, layer: Any, agent_id: str) -> None:
        """Merge path/domain allowlists from a :class:`~agentcop.permissions.ToolPermissionLayer`."""
        from agentcop.permissions import NetworkPermission, ReadPermission, WritePermission

        with layer._lock:
            perms = list(layer._permissions.get(agent_id, []))

        for perm in perms:
            if isinstance(perm, (ReadPermission, WritePermission)):
                for path in perm.paths:
                    if path not in self.allowed_paths:
                        self.allowed_paths.append(path)
            elif isinstance(perm, NetworkPermission):
                for domain in perm.domains:
                    if domain not in self.allowed_domains:
                        self.allowed_domains.append(domain)
