"""
Badge integration for the reliability module.

Maps reliability tiers to shield emoji and formats combined badge text
that combines the security trust tier with the reliability tier::

    from agentcop.reliability.badge_integration import (
        reliability_emoji,
        combined_badge_text,
        reliability_shield_url,
    )

    text = combined_badge_text(trust_score=94, reliability_score=87, reliability_tier="STABLE")
    # → "✅ SECURED 94/100 | 🟢 STABLE 87/100"
"""

from __future__ import annotations

from typing import Literal

# Emoji for each reliability tier
RELIABILITY_EMOJI: dict[str, str] = {
    "STABLE": "🟢",
    "VARIABLE": "🟡",
    "UNSTABLE": "🟠",
    "CRITICAL": "🔴",
}

# Shields.io badge colors per reliability tier
RELIABILITY_BADGE_COLORS: dict[str, str] = {
    "STABLE": "brightgreen",
    "VARIABLE": "yellow",
    "UNSTABLE": "orange",
    "CRITICAL": "red",
}

# Security tier emoji (mirrors badge.py _TIER_EMOJI but reproduced here to avoid import)
_SECURITY_TIER_EMOJI: dict[str, str] = {
    "SECURED": "✅",
    "MONITORED": "🟡",
    "AT RISK": "🔴",
}

ReliabilityTier = Literal["STABLE", "VARIABLE", "UNSTABLE", "CRITICAL"]
SecurityTier = Literal["SECURED", "MONITORED", "AT RISK"]


def reliability_emoji(tier: str) -> str:
    """Return the shield emoji for a reliability tier.

    Args:
        tier: One of ``STABLE``, ``VARIABLE``, ``UNSTABLE``, ``CRITICAL``.

    Returns:
        A single emoji character.  Falls back to ``"❓"`` for unknown tiers.
    """
    return RELIABILITY_EMOJI.get(tier, "❓")


def security_tier_from_score(trust_score: float) -> SecurityTier:
    """Map a trust score to its security tier label.

    This mirrors :func:`agentcop.badge.tier_from_score` without importing it so
    that this module has no dependency on the optional ``cryptography`` package.

    Args:
        trust_score: Float 0-100.

    Returns:
        ``"SECURED"`` (≥80), ``"MONITORED"`` (≥50), or ``"AT RISK"`` (<50).
    """
    if trust_score >= 80:
        return "SECURED"
    if trust_score >= 50:
        return "MONITORED"
    return "AT RISK"


def combined_badge_text(
    *,
    trust_score: float,
    reliability_score: int,
    reliability_tier: str,
    security_tier: str | None = None,
) -> str:
    """Format the combined security + reliability badge text.

    Args:
        trust_score:        Agent trust score (0-100).
        reliability_score:  Reliability score (0-100).
        reliability_tier:   One of ``STABLE``, ``VARIABLE``, ``UNSTABLE``, ``CRITICAL``.
        security_tier:      Override the security tier label.  When ``None``, derived
                            from *trust_score* automatically.

    Returns:
        A formatted string, e.g. ``"✅ SECURED 94/100 | 🟢 STABLE 87/100"``.

    Example::

        >>> combined_badge_text(trust_score=94, reliability_score=87, reliability_tier="STABLE")
        '✅ SECURED 94/100 | 🟢 STABLE 87/100'
    """
    sec_tier = security_tier or security_tier_from_score(trust_score)
    sec_emoji = _SECURITY_TIER_EMOJI.get(sec_tier, "❓")
    rel_emoji = reliability_emoji(reliability_tier)
    return (
        f"{sec_emoji} {sec_tier} {int(round(trust_score))}/100 | "
        f"{rel_emoji} {reliability_tier} {reliability_score}/100"
    )


def reliability_shield_url(agent_id: str, reliability_tier: str, reliability_score: int) -> str:
    """Return a static Shields.io badge URL for a reliability tier.

    The URL encodes the agent ID as the badge label and ``<TIER> <score>/100``
    as the message.  No live data — purely a static snapshot URL.

    Args:
        agent_id:          Human-readable agent identifier.
        reliability_tier:  One of ``STABLE``, ``VARIABLE``, ``UNSTABLE``, ``CRITICAL``.
        reliability_score: Integer 0-100.

    Returns:
        A ``https://img.shields.io/badge/...`` URL string.
    """
    from urllib.parse import quote

    color = RELIABILITY_BADGE_COLORS.get(reliability_tier, "lightgrey")
    label = quote(agent_id, safe="")
    message = quote(f"{reliability_tier} {reliability_score}/100", safe="")
    return f"https://img.shields.io/badge/{label}-{message}-{color}"


def reliability_markdown_badge(
    agent_id: str, reliability_tier: str, reliability_score: int
) -> str:
    """Return a Markdown ``![badge](url)`` string for a reliability tier.

    Args:
        agent_id:          Human-readable agent identifier.
        reliability_tier:  One of ``STABLE``, ``VARIABLE``, ``UNSTABLE``, ``CRITICAL``.
        reliability_score: Integer 0-100.

    Returns:
        A Markdown image string, e.g.
        ``"![Reliability](https://img.shields.io/badge/...)"``
    """
    url = reliability_shield_url(agent_id, reliability_tier, reliability_score)
    return f"![Reliability]({url})"
