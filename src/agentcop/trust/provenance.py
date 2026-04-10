"""
ProvenanceTracker — record and verify the origin of instructions.

Detects instructions that claim to come from one source type but actually
originated from a different, less-trusted source.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

_log = logging.getLogger(__name__)

SourceType = Literal["user", "agent", "rag", "memory", "tool"]


@dataclass
class ProvenanceRecord:
    """Records the full provenance of a single instruction or piece of content."""

    instruction_hash: str
    source: str
    source_type: SourceType
    timestamp: float
    chain_of_custody: list[str] = field(default_factory=list)


def _hash_instruction(instruction: str) -> str:
    return hashlib.sha256(instruction.encode()).hexdigest()


class ProvenanceTracker:
    """
    Tracks where instructions originated and detects spoofed provenance.

    Thread-safe; uses an in-memory dict keyed by instruction hash.

    Spoofing detection rule: an instruction whose actual origin is ``tool``,
    ``rag``, or ``memory`` but which claims to be from ``user`` is flagged.
    """

    def __init__(self) -> None:
        self._records: dict[str, ProvenanceRecord] = {}
        self._lock = threading.Lock()

    def record_origin(
        self,
        instruction: str,
        source: str,
        source_type: SourceType,
    ) -> str:
        """
        Record the origin of an instruction. Returns the instruction hash.

        If this instruction hash was already recorded, the new source is
        appended to the chain of custody without changing the original record.
        """
        h = _hash_instruction(instruction)
        with self._lock:
            if h in self._records:
                self._records[h].chain_of_custody.append(source)
            else:
                self._records[h] = ProvenanceRecord(
                    instruction_hash=h,
                    source=source,
                    source_type=source_type,
                    timestamp=time.time(),
                    chain_of_custody=[source],
                )
        return h

    def get_provenance(self, instruction_hash: str) -> ProvenanceRecord | None:
        """Return the provenance record for a given instruction hash, or None."""
        with self._lock:
            return self._records.get(instruction_hash)

    def detect_spoofing(
        self,
        instruction: str,
        claimed_source_type: SourceType,
    ) -> bool:
        """
        Return True if the instruction appears to have a spoofed origin.

        Specifically flags cases where the actual recorded source_type is
        ``tool``, ``rag``, or ``memory`` but the claimed source type is ``user``.
        """
        h = _hash_instruction(instruction)
        with self._lock:
            record = self._records.get(h)

        if record is None:
            return False

        # A tool/rag/memory result claiming to be a user instruction is suspicious
        untrusted_origins: set[SourceType] = {"tool", "rag", "memory"}
        return record.source_type in untrusted_origins and claimed_source_type == "user"
