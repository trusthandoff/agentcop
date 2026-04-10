"""
RAGTrustLayer — verify RAG document sources and detect poisoning attempts.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass
from typing import Literal

_log = logging.getLogger(__name__)

TrustLevel = Literal["verified", "unverified", "untrusted"]


@dataclass
class RAGTrustResult:
    """Result of verifying a RAG document against its declared source."""

    doc_hash: str
    source_id: str
    trust_level: TrustLevel
    verified: bool
    reason: str


@dataclass
class PoisoningAlert:
    """Alert raised when a RAG document exhibits injection patterns."""

    doc_hash: str
    pattern: str
    severity: Literal["WARN", "ERROR", "CRITICAL"]
    matched_text: str


# (regex pattern, severity) pairs — matched against lowercase document text
_INJECTION_PATTERNS: list[tuple[str, Literal["WARN", "ERROR", "CRITICAL"]]] = [
    # CRITICAL — direct instruction injection
    (r"ignore\s+(all\s+)?previous\s+instructions?", "CRITICAL"),
    (r"disregard\s+(all\s+)?previous", "CRITICAL"),
    (r"forget\s+everything", "CRITICAL"),
    (r"new\s+system\s+prompt", "CRITICAL"),
    (r"you\s+are\s+now\s+a", "CRITICAL"),
    # ERROR — instruction-like content
    (r"your\s+(new\s+)?instructions?\s+are", "ERROR"),
    (r"do\s+not\s+follow", "ERROR"),
    (r"override\s+(the\s+)?instructions?", "ERROR"),
    (r"as\s+an?\s+ai\s+(assistant,?\s+)?you\s+must", "ERROR"),
    # WARN — suspicious structure
    (r"<\s*system\s*>", "WARN"),
    (r"\[system\]", "WARN"),
    (r"INST\s*>", "WARN"),
]


def _doc_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


class RAGTrustLayer:
    """
    Registers document sources and detects RAG poisoning attempts.

    Trust level propagation rule: any output derived from an ``untrusted``
    source should be flagged regardless of content.

    Thread-safe.
    """

    def __init__(self) -> None:
        # source_id → (url, trust_level)
        self._sources: dict[str, tuple[str, TrustLevel]] = {}
        # doc_hash → source_id (for lineage tracking)
        self._verified_docs: dict[str, str] = {}
        self._lock = threading.Lock()

    def register_source(
        self,
        source_id: str,
        source_url: str,
        trust_level: TrustLevel,
    ) -> None:
        """Register a document source with its trust level."""
        with self._lock:
            self._sources[source_id] = (source_url, trust_level)

    def verify_document(self, doc_hash: str, source_id: str) -> RAGTrustResult:
        """
        Verify that a document hash came from a registered source.

        Returns a RAGTrustResult whose ``verified`` flag is False for
        unregistered or ``untrusted`` sources.
        """
        with self._lock:
            source_entry = self._sources.get(source_id)

        if source_entry is None:
            return RAGTrustResult(
                doc_hash=doc_hash,
                source_id=source_id,
                trust_level="unverified",
                verified=False,
                reason=f"source '{source_id}' not registered",
            )

        url, trust_level = source_entry
        verified = trust_level == "verified"
        reason = f"source '{source_id}' is {trust_level} ({url})"

        with self._lock:
            self._verified_docs[doc_hash] = source_id

        return RAGTrustResult(
            doc_hash=doc_hash,
            source_id=source_id,
            trust_level=trust_level,
            verified=verified,
            reason=reason,
        )

    def detect_poisoning(self, documents: list[str]) -> list[PoisoningAlert]:
        """
        Scan documents for injection and poisoning patterns.

        Returns one PoisoningAlert per pattern match found. A single document
        may produce multiple alerts.
        """
        alerts: list[PoisoningAlert] = []

        for doc in documents:
            h = _doc_hash(doc)
            doc_lower = doc.lower()

            for pattern, severity in _INJECTION_PATTERNS:
                match = re.search(pattern, doc_lower)
                if match:
                    end = min(match.end() + 30, len(doc))
                    matched_text = doc[match.start() : end].strip()
                    alerts.append(
                        PoisoningAlert(
                            doc_hash=h,
                            pattern=pattern,
                            severity=severity,
                            matched_text=matched_text,
                        )
                    )

        return alerts
