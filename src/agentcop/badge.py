"""
AgentBadge — cryptographically signed, publicly verifiable agent security badge.

Like SSL for websites, but for agents. Every agent running agentcop can earn a
verifiable badge. Other agents and humans can verify it offline using the Ed25519
public key.

Usage::

    from agentcop.badge import BadgeIssuer, SQLiteBadgeStore, generate_svg

    # Issue a badge
    store = SQLiteBadgeStore("agentcop.db")
    issuer = BadgeIssuer(store=store)
    badge = issuer.issue(
        agent_id="my-agent",
        fingerprint="abc123...",
        trust_score=85.0,
        violations={"critical": 0, "warning": 1, "info": 0, "protected": 3},
        framework="langgraph",
        scan_count=42,
    )

    # Verify
    assert issuer.verify(badge)

    # SVG for README
    svg = generate_svg(badge)
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, Field

from . import __version__

BADGE_BASE_URL = "https://agentcop.live/badge"
_BADGE_EXPIRY_DAYS = 30
_REVOKE_TRUST_THRESHOLD = 30.0
_RENEW_THRESHOLD_DAYS = 7

_TIER_COLORS: dict[str, str] = {
    "SECURED": "#00ff88",
    "MONITORED": "#ffaa00",
    "AT RISK": "#ff3333",
}

_TIER_EMOJI: dict[str, str] = {
    "SECURED": "🟢",
    "MONITORED": "🟡",
    "AT RISK": "🔴",
}


def _require_badge() -> None:
    """Raise ImportError if cryptography is not installed."""
    try:
        import cryptography  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "agentcop[badge] requires the 'cryptography' package. "
            "Install it with: pip install agentcop[badge]"
        ) from exc


def tier_from_score(trust_score: float) -> Literal["SECURED", "MONITORED", "AT RISK"]:
    """Return the badge tier for a given trust score (0-100)."""
    if trust_score >= 80:
        return "SECURED"
    if trust_score >= 50:
        return "MONITORED"
    return "AT RISK"


# ---------------------------------------------------------------------------
# Badge model
# ---------------------------------------------------------------------------


class AgentBadge(BaseModel):
    """Cryptographically signed agent security badge.

    Immutable after construction.  Verify the signature with
    :meth:`BadgeIssuer.verify`.  Check lifecycle state with
    :meth:`is_valid`, :meth:`is_expired`, :meth:`expires_soon`.
    """

    badge_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str
    fingerprint: str
    trust_score: float
    tier: Literal["SECURED", "MONITORED", "AT RISK"]
    last_scan: datetime
    scan_count: int
    violations: dict[str, int]
    """Keys: critical, warning, info, protected."""
    framework: str
    agentcop_version: str
    issued_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    issuer: str = "agentcop"
    signature: str = ""
    """Hex-encoded Ed25519 signature over canonical JSON (sorted keys, no signature field)."""
    public_key: str = ""
    """Verifier public key in PEM format."""
    verification_url: str = ""
    shield_url: str = ""
    revoked: bool = False
    revocation_reason: str | None = None

    model_config = {"frozen": True}

    def is_expired(self) -> bool:
        """Return True if the badge has passed its expiry date."""
        return datetime.now(UTC) >= self.expires_at

    def is_valid(self) -> bool:
        """Return True if badge is neither revoked nor expired."""
        return not self.revoked and not self.is_expired()

    def expires_soon(self, threshold_days: int = _RENEW_THRESHOLD_DAYS) -> bool:
        """Return True if the badge expires within *threshold_days* days."""
        return datetime.now(UTC) >= self.expires_at - timedelta(days=threshold_days)


# ---------------------------------------------------------------------------
# Storage backends
# ---------------------------------------------------------------------------


class BadgeStore:
    """Abstract storage backend for :class:`AgentBadge` instances."""

    def save(self, badge: AgentBadge) -> None:
        raise NotImplementedError

    def load(self, badge_id: str) -> AgentBadge | None:
        raise NotImplementedError

    def load_latest(self, agent_id: str) -> AgentBadge | None:
        raise NotImplementedError

    def revoke(self, badge_id: str, reason: str = "") -> bool:
        """Mark a badge as revoked.  Returns False if not found."""
        raise NotImplementedError

    def list_badges(self, agent_id: str | None = None) -> list[str]:
        raise NotImplementedError

    def save_key_pair(self, private_pem: bytes, public_pem: bytes) -> None:
        raise NotImplementedError

    def load_key_pair(self) -> tuple[bytes, bytes] | None:
        raise NotImplementedError


class InMemoryBadgeStore(BadgeStore):
    """Thread-safe in-memory badge store.  Fast, no setup, not persistent.  Good for tests."""

    def __init__(self) -> None:
        self._badges: dict[str, AgentBadge] = {}
        self._private_pem: bytes | None = None
        self._public_pem: bytes | None = None
        self._lock = threading.Lock()

    def save(self, badge: AgentBadge) -> None:
        with self._lock:
            self._badges[badge.badge_id] = badge

    def load(self, badge_id: str) -> AgentBadge | None:
        with self._lock:
            return self._badges.get(badge_id)

    def load_latest(self, agent_id: str) -> AgentBadge | None:
        with self._lock:
            matches = [b for b in self._badges.values() if b.agent_id == agent_id]
        if not matches:
            return None
        return max(matches, key=lambda b: b.issued_at)

    def revoke(self, badge_id: str, reason: str = "") -> bool:
        with self._lock:
            badge = self._badges.get(badge_id)
            if badge is None:
                return False
            self._badges[badge_id] = badge.model_copy(
                update={"revoked": True, "revocation_reason": reason or None}
            )
        return True

    def list_badges(self, agent_id: str | None = None) -> list[str]:
        with self._lock:
            if agent_id is None:
                return list(self._badges.keys())
            return [bid for bid, b in self._badges.items() if b.agent_id == agent_id]

    def save_key_pair(self, private_pem: bytes, public_pem: bytes) -> None:
        with self._lock:
            self._private_pem = private_pem
            self._public_pem = public_pem

    def load_key_pair(self) -> tuple[bytes, bytes] | None:
        with self._lock:
            if self._private_pem and self._public_pem:
                return self._private_pem, self._public_pem
            return None


class SQLiteBadgeStore(BadgeStore):
    """SQLite-backed persistent badge store.  Can share an existing agentcop.db."""

    def __init__(self, db_path: str | Path = "agentcop.db") -> None:
        self._path = Path(db_path)
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, isolation_level=None, timeout=30
        )
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute("BEGIN EXCLUSIVE")
            try:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS badges (
                        badge_id          TEXT PRIMARY KEY,
                        agent_id          TEXT NOT NULL,
                        data              TEXT NOT NULL,
                        issued_at         TEXT NOT NULL,
                        revoked           INTEGER NOT NULL DEFAULT 0,
                        revocation_reason TEXT
                    )
                    """
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS badges_agent_id ON badges(agent_id)"
                )
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS badge_keys (
                        id          INTEGER PRIMARY KEY CHECK (id = 1),
                        private_pem BLOB NOT NULL,
                        public_pem  BLOB NOT NULL
                    )
                    """
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def save(self, badge: AgentBadge) -> None:
        data = badge.model_dump_json()
        with self._lock:
            self._conn.execute("BEGIN EXCLUSIVE")
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO badges"
                    " (badge_id, agent_id, data, issued_at, revoked, revocation_reason)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        badge.badge_id,
                        badge.agent_id,
                        data,
                        badge.issued_at.isoformat(),
                        1 if badge.revoked else 0,
                        badge.revocation_reason,
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def load(self, badge_id: str) -> AgentBadge | None:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT data FROM badges WHERE badge_id = ?", (badge_id,)
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return AgentBadge.model_validate_json(row[0])

    def load_latest(self, agent_id: str) -> AgentBadge | None:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT data FROM badges WHERE agent_id = ? ORDER BY issued_at DESC LIMIT 1",
                (agent_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return AgentBadge.model_validate_json(row[0])

    def revoke(self, badge_id: str, reason: str = "") -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT data FROM badges WHERE badge_id = ?", (badge_id,)
            )
            row = cursor.fetchone()
            if row is None:
                return False
            badge = AgentBadge.model_validate_json(row[0])
            updated = badge.model_copy(
                update={"revoked": True, "revocation_reason": reason or None}
            )
            self._conn.execute("BEGIN EXCLUSIVE")
            try:
                self._conn.execute(
                    "UPDATE badges SET data = ?, revoked = 1, revocation_reason = ?"
                    " WHERE badge_id = ?",
                    (updated.model_dump_json(), reason or None, badge_id),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return True

    def list_badges(self, agent_id: str | None = None) -> list[str]:
        with self._lock:
            if agent_id is None:
                cursor = self._conn.execute("SELECT badge_id FROM badges")
            else:
                cursor = self._conn.execute(
                    "SELECT badge_id FROM badges WHERE agent_id = ?", (agent_id,)
                )
            return [row[0] for row in cursor.fetchall()]

    def save_key_pair(self, private_pem: bytes, public_pem: bytes) -> None:
        with self._lock:
            self._conn.execute("BEGIN EXCLUSIVE")
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO badge_keys (id, private_pem, public_pem) VALUES (1, ?, ?)",
                    (private_pem, public_pem),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def load_key_pair(self) -> tuple[bytes, bytes] | None:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT private_pem, public_pem FROM badge_keys WHERE id = 1"
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return bytes(row[0]), bytes(row[1])

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# BadgeIssuer — Ed25519 signing and verification
# ---------------------------------------------------------------------------


class BadgeIssuer:
    """Ed25519 badge issuer — generates, signs, and verifies agent security badges.

    On first use, generates an Ed25519 key pair and persists it to the store.
    Subsequent instantiations reload the same key pair for consistent verification.

    Signature verification is constant-time at the C level (libcrypto Ed25519).

    Usage::

        issuer = BadgeIssuer(store=SQLiteBadgeStore("agentcop.db"))
        badge = issuer.issue(agent_id="my-agent", trust_score=85, ...)
        assert issuer.verify(badge)
    """

    def __init__(self, store: BadgeStore | None = None) -> None:
        _require_badge()
        self._store = store or InMemoryBadgeStore()
        self._lock = threading.Lock()
        self._private_key: Any = None
        self._public_key: Any = None
        self._public_pem: str = ""
        self._load_or_generate_keys()

    def _load_or_generate_keys(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
            PublicFormat,
            load_pem_private_key,
        )

        pair = self._store.load_key_pair()
        if pair is not None:
            private_pem, public_pem = pair
            self._private_key = load_pem_private_key(private_pem, password=None)
            self._public_key = self._private_key.public_key()
            self._public_pem = public_pem.decode()
        else:
            self._private_key = Ed25519PrivateKey.generate()
            self._public_key = self._private_key.public_key()
            private_pem = self._private_key.private_bytes(
                Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
            )
            public_pem_bytes = self._public_key.public_bytes(
                Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
            )
            self._public_pem = public_pem_bytes.decode()
            self._store.save_key_pair(private_pem, public_pem_bytes)

    def issue(
        self,
        *,
        agent_id: str,
        fingerprint: str,
        trust_score: float,
        violations: dict[str, int] | None = None,
        framework: str = "generic",
        scan_count: int = 0,
        last_scan: datetime | None = None,
        store: BadgeStore | None = None,
    ) -> AgentBadge:
        """Issue and sign a new badge for an agent.

        The badge is persisted to *store* (falls back to the issuer's store).
        If *trust_score* < :data:`_REVOKE_TRUST_THRESHOLD` (30), the badge is
        immediately marked revoked with ``revocation_reason="trust_below_30"``.
        """
        now = datetime.now(UTC)
        tier = tier_from_score(trust_score)
        badge_id = str(uuid.uuid4())
        expires_at = now + timedelta(days=_BADGE_EXPIRY_DAYS)
        viol = violations or {"critical": 0, "warning": 0, "info": 0, "protected": 0}
        auto_revoke = trust_score < _REVOKE_TRUST_THRESHOLD

        unsigned = AgentBadge(
            badge_id=badge_id,
            agent_id=agent_id,
            fingerprint=fingerprint,
            trust_score=trust_score,
            tier=tier,
            last_scan=last_scan or now,
            scan_count=scan_count,
            violations=viol,
            framework=framework,
            agentcop_version=__version__,
            issued_at=now,
            expires_at=expires_at,
            issuer="agentcop",
            signature="",
            public_key=self._public_pem,
            verification_url=f"{BADGE_BASE_URL}/{badge_id}",
            shield_url=f"{BADGE_BASE_URL}/{badge_id}/shield",
            revoked=auto_revoke,
            revocation_reason="trust_below_30" if auto_revoke else None,
        )

        sig_hex = self._sign(unsigned)
        badge = unsigned.model_copy(update={"signature": sig_hex})

        target_store = store or self._store
        target_store.save(badge)
        return badge

    def _signing_payload(self, badge: AgentBadge) -> bytes:
        """Canonical JSON payload: all fields except ``signature``, sorted keys."""
        d = badge.model_dump(mode="json")
        d.pop("signature", None)
        return json.dumps(d, sort_keys=True, default=str).encode()

    def _sign(self, badge: AgentBadge) -> str:
        payload = self._signing_payload(badge)
        with self._lock:
            sig = self._private_key.sign(payload)
        return sig.hex()

    def verify(self, badge: AgentBadge) -> bool:
        """Verify the Ed25519 signature on *badge*.

        Returns True only if the cryptographic signature is valid.
        This is constant-time at the libcrypto level — no Python-level
        branch on signature content prevents timing side-channels.

        This does NOT check expiry or revocation status; call
        :meth:`AgentBadge.is_valid` for that.
        """
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        if not badge.signature or not badge.public_key:
            return False
        try:
            pub_key = load_pem_public_key(badge.public_key.encode())
            if not isinstance(pub_key, Ed25519PublicKey):
                return False
            sig_bytes = bytes.fromhex(badge.signature)
            payload = self._signing_payload(badge)
            # Ed25519 verify() raises InvalidSignature on failure — constant-time internally
            pub_key.verify(sig_bytes, payload)
            return True
        except (InvalidSignature, ValueError, Exception):
            return False

    def verify_json(self, badge_json: str) -> bool:
        """Verify a badge from its JSON string."""
        try:
            badge = AgentBadge.model_validate_json(badge_json)
            return self.verify(badge)
        except Exception:
            return False

    def public_key_pem(self) -> str:
        """Return the Ed25519 public key in PEM format."""
        return self._public_pem

    def renew(self, badge: AgentBadge, *, store: BadgeStore | None = None) -> AgentBadge:
        """Issue a renewal badge and revoke the old one as superseded."""
        target_store = store or self._store
        target_store.revoke(badge.badge_id, reason="superseded_by_renewal")
        return self.issue(
            agent_id=badge.agent_id,
            fingerprint=badge.fingerprint,
            trust_score=badge.trust_score,
            violations=dict(badge.violations),
            framework=badge.framework,
            scan_count=badge.scan_count,
            store=target_store,
        )

    def revoke(self, badge_id: str, *, reason: str = "", store: BadgeStore | None = None) -> bool:
        """Revoke a badge by ID.  Returns False if badge not found."""
        target_store = store or self._store
        return target_store.revoke(badge_id, reason=reason)


# ---------------------------------------------------------------------------
# SVG badge generation
# ---------------------------------------------------------------------------


def generate_svg(badge: AgentBadge) -> str:
    """Generate a custom SVG badge for embedding in GitHub READMEs.

    - SECURED: bright green #00ff88 with glow animation
    - MONITORED: amber #ffaa00, static
    - AT RISK: red #ff3333 with pulse animation (urgent)
    """
    color = _TIER_COLORS[badge.tier]
    score = int(badge.trust_score)
    tier_label = badge.tier

    if badge.tier == "SECURED":
        anim_css = (
            ".badge-right { animation: glow 2s ease-in-out infinite alternate; }"
            "@keyframes glow {"
            "from { filter: drop-shadow(0 0 3px #00ff88); }"
            "to { filter: drop-shadow(0 0 10px #00ff88) drop-shadow(0 0 20px #00ff8866); }"
            "}"
        )
        anim_class = "badge-right"
    elif badge.tier == "AT RISK":
        anim_css = (
            ".badge-right { animation: pulse 1s ease-in-out infinite; }"
            "@keyframes pulse {"
            "0%,100% { opacity: 1; } 50% { opacity: 0.55; }"
            "}"
        )
        anim_class = "badge-right"
    else:
        anim_css = ""
        anim_class = "badge-right"

    style_tag = f"<style>{anim_css}</style>" if anim_css else ""

    # Escape XML special chars
    tier_safe = tier_label.replace("&", "&amp;")

    return (
        f'<svg width="200" height="48" viewBox="0 0 200 48"'
        f' xmlns="http://www.w3.org/2000/svg"'
        f' role="img" aria-label="agentcop {tier_safe} badge score {score}/100">'
        f"<title>agentcop {tier_safe} — {score}/100</title>"
        f"{style_tag}"
        f'<rect width="200" height="48" rx="6" fill="#0a0a0a"/>'
        f'<rect x="0" y="0" width="70" height="48" rx="6" fill="#111111"/>'
        f'<rect x="64" y="0" width="8" height="48" fill="#111111"/>'
        f'<text x="35" y="19" font-size="15" text-anchor="middle" fill="#cccccc"'
        f' font-family="monospace">🤖</text>'
        f'<text x="35" y="36" font-size="8" text-anchor="middle" fill="#666666"'
        f' font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif"'
        f' letter-spacing="0.5">agentcop</text>'
        f'<g class="{anim_class}">'
        f'<text x="134" y="21" font-size="12" font-weight="700" text-anchor="middle"'
        f' fill="{color}"'
        f' font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif">'
        f"{tier_safe}</text>"
        f'<text x="134" y="38" font-size="11" text-anchor="middle" fill="{color}"'
        f' font-family="monospace" opacity="0.85">{score}/100</text>'
        f"</g>"
        f"</svg>"
    )


# ---------------------------------------------------------------------------
# Badge card HTML page
# ---------------------------------------------------------------------------


def generate_badge_card(badge: AgentBadge) -> str:
    """Generate a full-page HTML badge card — screenshot-worthy, shareable.

    Includes: animated score gauge, tier label, violation breakdown, framework,
    fingerprint, share button, verify button, Sentinel signature.
    """
    color = _TIER_COLORS[badge.tier]
    emoji = _TIER_EMOJI[badge.tier]
    score = int(badge.trust_score)
    tier = badge.tier
    fp_short = badge.fingerprint[:8] if len(badge.fingerprint) >= 8 else badge.fingerprint

    viol = badge.violations
    critical = viol.get("critical", 0)
    warning = viol.get("warning", 0)
    protected = viol.get("protected", 0)

    last_scan_str = badge.last_scan.strftime("%Y-%m-%d %H:%M UTC")
    expires_str = badge.expires_at.strftime("%Y-%m-%d")
    status_label = "REVOKED" if badge.revoked else ("EXPIRED" if badge.is_expired() else "ACTIVE")
    status_color = "#ff3333" if badge.revoked or badge.is_expired() else color

    share_text = quote(
        f"my agent just got agentcop {tier} {emoji} score: {score}/100 "
        f"→ {badge.verification_url} #AgentSecurity #agentcop"
    )
    x_share_url = f"https://x.com/intent/tweet?text={share_text}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>agentcop badge — {badge.agent_id}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #050505;
    color: #e0e0e0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }}
  .card {{
    background: #0d0d0d;
    border: 1px solid #1e1e1e;
    border-radius: 16px;
    padding: 40px 48px;
    max-width: 540px;
    width: 100%;
    box-shadow: 0 0 60px rgba(0,0,0,0.8);
  }}
  .header {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 32px;
  }}
  .robot {{ font-size: 32px; }}
  .brand {{ font-size: 13px; color: #555; letter-spacing: 2px; text-transform: uppercase; }}
  .agent-id {{ font-size: 18px; font-weight: 600; color: #fff; margin-top: 2px; }}
  .gauge-wrap {{
    display: flex;
    flex-direction: column;
    align-items: center;
    margin: 24px 0;
  }}
  .gauge-ring {{
    width: 160px;
    height: 160px;
    position: relative;
  }}
  .gauge-ring svg {{ transform: rotate(-90deg); }}
  .gauge-ring .track {{ fill: none; stroke: #1a1a1a; stroke-width: 14; }}
  .gauge-ring .fill {{
    fill: none;
    stroke: {color};
    stroke-width: 14;
    stroke-linecap: round;
    stroke-dasharray: 408;
    stroke-dashoffset: 408;
    transition: stroke-dashoffset 1.5s cubic-bezier(0.4, 0, 0.2, 1);
  }}
  .gauge-inner {{
    position: absolute;
    top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    text-align: center;
  }}
  .score-num {{
    font-size: 40px;
    font-weight: 800;
    color: {color};
    line-height: 1;
    font-variant-numeric: tabular-nums;
  }}
  .score-denom {{ font-size: 14px; color: #555; margin-top: 2px; }}
  .tier-label {{
    font-size: 22px;
    font-weight: 800;
    color: {color};
    letter-spacing: 2px;
    margin-top: 12px;
    text-shadow: 0 0 20px {color}55;
  }}
  .status-pill {{
    display: inline-block;
    padding: 3px 12px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    border: 1px solid {status_color};
    color: {status_color};
    margin-top: 6px;
  }}
  .stats-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin: 28px 0;
  }}
  .stat {{
    background: #111;
    border: 1px solid #1e1e1e;
    border-radius: 10px;
    padding: 14px 16px;
  }}
  .stat-label {{ font-size: 11px; color: #555; letter-spacing: 1px; text-transform: uppercase; }}
  .stat-value {{ font-size: 16px; font-weight: 600; color: #ddd; margin-top: 4px; }}
  .stat-value.critical {{ color: #ff3333; }}
  .stat-value.warning {{ color: #ffaa00; }}
  .stat-value.protected {{ color: {color}; }}
  .meta-row {{
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    color: #444;
    padding: 6px 0;
    border-bottom: 1px solid #151515;
  }}
  .meta-row:last-child {{ border-bottom: none; }}
  .meta-val {{ color: #666; font-family: monospace; }}
  .actions {{
    display: flex;
    gap: 12px;
    margin-top: 28px;
  }}
  .btn {{
    flex: 1;
    padding: 12px;
    border-radius: 10px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    text-align: center;
    text-decoration: none;
    border: none;
  }}
  .btn-primary {{
    background: {color};
    color: #000;
  }}
  .btn-secondary {{
    background: #111;
    color: {color};
    border: 1px solid {color}44;
  }}
  .signature {{
    margin-top: 24px;
    text-align: center;
    font-size: 11px;
    color: #333;
  }}
  .signature span {{ color: #ff4444; }}
  @keyframes gaugeIn {{
    from {{ stroke-dashoffset: 408; }}
    to {{ stroke-dashoffset: {408 - int(408 * score / 100)}; }}
  }}
  .fill {{ animation: gaugeIn 1.5s cubic-bezier(0.4,0,0.2,1) forwards; }}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <div class="robot">🤖</div>
    <div>
      <div class="brand">agentcop · verified</div>
      <div class="agent-id">{badge.agent_id}</div>
    </div>
  </div>

  <div class="gauge-wrap">
    <div class="gauge-ring">
      <svg width="160" height="160" viewBox="0 0 160 160">
        <circle class="track" cx="80" cy="80" r="65"/>
        <circle class="fill" cx="80" cy="80" r="65"/>
      </svg>
      <div class="gauge-inner">
        <div class="score-num" id="score-num">0</div>
        <div class="score-denom">/100</div>
      </div>
    </div>
    <div class="tier-label">{emoji} {tier}</div>
    <div class="status-pill">{status_label}</div>
  </div>

  <div class="stats-grid">
    <div class="stat">
      <div class="stat-label">Critical</div>
      <div class="stat-value critical">{critical}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Warnings</div>
      <div class="stat-value warning">{warning}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Protected</div>
      <div class="stat-value protected">{protected}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Scans Run</div>
      <div class="stat-value">{badge.scan_count}</div>
    </div>
  </div>

  <div>
    <div class="meta-row">
      <span>Framework</span>
      <span class="meta-val">{badge.framework}</span>
    </div>
    <div class="meta-row">
      <span>Last scan</span>
      <span class="meta-val">{last_scan_str}</span>
    </div>
    <div class="meta-row">
      <span>Expires</span>
      <span class="meta-val">{expires_str}</span>
    </div>
    <div class="meta-row">
      <span>Fingerprint</span>
      <span class="meta-val">{fp_short}...</span>
    </div>
    <div class="meta-row">
      <span>Badge ID</span>
      <span class="meta-val">{badge.badge_id[:8]}...</span>
    </div>
  </div>

  <div class="actions">
    <a class="btn btn-primary" href="{x_share_url}" target="_blank" rel="noopener">
      Share on X
    </a>
    <a class="btn btn-secondary" href="{badge.verification_url}" target="_blank" rel="noopener">
      Verify badge
    </a>
  </div>

  <div class="signature">
    Verified by <span>Sentinel 🚨</span> — The Cop for Agent Fleets
    &nbsp;·&nbsp; agentcop v{badge.agentcop_version}
  </div>
</div>

<script>
  (function() {{
    var target = {score};
    var el = document.getElementById('score-num');
    var start = null;
    var duration = 1500;
    function step(ts) {{
      if (!start) start = ts;
      var progress = Math.min((ts - start) / duration, 1);
      var ease = 1 - Math.pow(1 - progress, 3);
      el.textContent = Math.round(ease * target);
      if (progress < 1) requestAnimationFrame(step);
    }}
    requestAnimationFrame(step);
  }})();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Markdown badge snippet
# ---------------------------------------------------------------------------


def generate_markdown(badge: AgentBadge) -> str:
    """Return a Markdown snippet for embedding the badge in a README."""
    return (
        f"[![agentcop {badge.tier}]({badge.shield_url})]({badge.verification_url})\n\n"
        f"<!-- agentcop badge: {badge.badge_id} -->"
    )
