"""
agentcop.live — badge API server

Endpoints:
  GET  /badge/{badge_id}         — badge JSON
  GET  /badge/{badge_id}/card    — HTML badge card page
  GET  /badge/{badge_id}/svg     — custom SVG badge
  GET  /badge/{badge_id}/shield  — redirect to shields.io fallback
  GET  /badge/pubkey             — Ed25519 public key PEM
  POST /badge/verify             — verify badge JSON server-side

Run:
  pip install fastapi uvicorn agentcop[badge]
  uvicorn agentcop-scanner.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from agentcop.badge import (
    AgentBadge,
    BadgeIssuer,
    SQLiteBadgeStore,
    generate_badge_card,
    generate_svg,
    tier_from_score,
)

# ---------------------------------------------------------------------------
# Storage & issuer (singleton per process)
# ---------------------------------------------------------------------------

_DB_PATH = Path(os.environ.get("AGENTCOP_BADGE_DB", "agentcop-scanner.db"))
_store = SQLiteBadgeStore(_DB_PATH)
_issuer = BadgeIssuer(store=_store)

app = FastAPI(
    title="agentcop badge API",
    description="Cryptographically signed agent security badges",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# Shields.io label → tier
# ---------------------------------------------------------------------------

_SHIELDS_COLORS = {
    "SECURED": "brightgreen",
    "MONITORED": "yellow",
    "AT RISK": "red",
}


def _shields_url(badge: AgentBadge) -> str:
    label = "agentcop"
    message = f"{badge.tier} {int(badge.trust_score)}/100"
    color = _SHIELDS_COLORS.get(badge.tier, "lightgrey")
    from urllib.parse import quote
    return (
        f"https://img.shields.io/badge/{quote(label)}-{quote(message)}-{color}"
        f"?style=flat-square&logo=shield"
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/badge/pubkey", response_class=Response)
async def get_pubkey() -> Response:
    """Return the Ed25519 public key in PEM format for offline verification."""
    return Response(
        content=_issuer.public_key_pem(),
        media_type="application/x-pem-file",
        headers={"Content-Disposition": "inline; filename=\"agentcop-pubkey.pem\""},
    )


@app.get("/badge/{badge_id}", response_class=JSONResponse)
async def get_badge(badge_id: str) -> JSONResponse:
    """Return full badge JSON."""
    badge = _store.load(badge_id)
    if badge is None:
        raise HTTPException(status_code=404, detail="Badge not found")
    return JSONResponse(content=badge.model_dump(mode="json"))


@app.get("/badge/{badge_id}/card", response_class=HTMLResponse)
async def get_badge_card(badge_id: str) -> HTMLResponse:
    """Return the full premium HTML badge card page."""
    badge = _store.load(badge_id)
    if badge is None:
        raise HTTPException(status_code=404, detail="Badge not found")
    return HTMLResponse(content=generate_badge_card(badge))


@app.get("/badge/{badge_id}/svg", response_class=Response)
async def get_badge_svg(badge_id: str) -> Response:
    """Return the custom SVG badge."""
    badge = _store.load(badge_id)
    if badge is None:
        raise HTTPException(status_code=404, detail="Badge not found")
    return Response(
        content=generate_svg(badge),
        media_type="image/svg+xml",
        headers={"Cache-Control": "max-age=300"},
    )


@app.get("/badge/{badge_id}/shield")
async def get_badge_shield(badge_id: str) -> RedirectResponse:
    """Redirect to shields.io fallback badge."""
    badge = _store.load(badge_id)
    if badge is None:
        raise HTTPException(status_code=404, detail="Badge not found")
    return RedirectResponse(url=_shields_url(badge), status_code=302)


@app.post("/badge/verify", response_class=JSONResponse)
async def verify_badge(badge_json: dict) -> JSONResponse:
    """Verify a badge JSON object server-side.

    Returns ``{"valid": true/false, "signature_valid": ..., "revoked": ..., "expired": ...}``.
    """
    try:
        badge = AgentBadge.model_validate(badge_json)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid badge JSON: {exc}") from exc

    sig_valid = _issuer.verify(badge)
    return JSONResponse(content={
        "valid": sig_valid and badge.is_valid(),
        "signature_valid": sig_valid,
        "revoked": badge.revoked,
        "expired": badge.is_expired(),
        "tier": badge.tier,
        "trust_score": badge.trust_score,
        "agent_id": badge.agent_id,
        "badge_id": badge.badge_id,
        "revocation_reason": badge.revocation_reason,
    })
