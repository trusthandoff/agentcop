"""Tests for agentcop.trust.provenance — ProvenanceTracker."""

from __future__ import annotations

import threading

from agentcop.trust.provenance import ProvenanceRecord, ProvenanceTracker, _hash_instruction


class TestHashInstruction:
    def test_same_input_same_hash(self):
        assert _hash_instruction("hello") == _hash_instruction("hello")

    def test_different_input_different_hash(self):
        assert _hash_instruction("hello") != _hash_instruction("world")

    def test_returns_hex_string(self):
        h = _hash_instruction("test")
        assert len(h) == 64  # SHA256


class TestProvenanceTrackerRecordOrigin:
    def test_returns_hash(self):
        pt = ProvenanceTracker()
        h = pt.record_origin("do thing", "user-1", "user")
        assert isinstance(h, str)
        assert len(h) == 64

    def test_same_instruction_same_hash(self):
        pt = ProvenanceTracker()
        h1 = pt.record_origin("do thing", "u1", "user")
        h2 = pt.record_origin("do thing", "u2", "agent")
        assert h1 == h2

    def test_get_provenance_returns_record(self):
        pt = ProvenanceTracker()
        h = pt.record_origin("my instruction", "user-1", "user")
        record = pt.get_provenance(h)
        assert isinstance(record, ProvenanceRecord)
        assert record.instruction_hash == h
        assert record.source == "user-1"
        assert record.source_type == "user"

    def test_get_provenance_unknown_returns_none(self):
        pt = ProvenanceTracker()
        assert pt.get_provenance("nonexistent") is None

    def test_chain_of_custody_updated_on_re_record(self):
        pt = ProvenanceTracker()
        h = pt.record_origin("instruction", "source-a", "user")
        pt.record_origin("instruction", "source-b", "agent")
        record = pt.get_provenance(h)
        assert record is not None
        assert "source-a" in record.chain_of_custody
        assert "source-b" in record.chain_of_custody

    def test_original_source_preserved_on_re_record(self):
        pt = ProvenanceTracker()
        h = pt.record_origin("instruction", "original", "user")
        pt.record_origin("instruction", "second", "tool")
        record = pt.get_provenance(h)
        assert record is not None
        assert record.source == "original"
        assert record.source_type == "user"

    def test_initial_chain_of_custody_has_source(self):
        pt = ProvenanceTracker()
        h = pt.record_origin("test", "src", "user")
        record = pt.get_provenance(h)
        assert record is not None
        assert record.chain_of_custody == ["src"]

    def test_record_has_timestamp(self):
        pt = ProvenanceTracker()
        h = pt.record_origin("test", "src", "user")
        record = pt.get_provenance(h)
        assert record is not None
        assert record.timestamp > 0


class TestProvenanceTrackerSpoofing:
    def test_tool_origin_claiming_user_is_spoofed(self):
        pt = ProvenanceTracker()
        instruction = "call the API"
        pt.record_origin(instruction, "tool-result", "tool")
        assert pt.detect_spoofing(instruction, "user") is True

    def test_rag_origin_claiming_user_is_spoofed(self):
        pt = ProvenanceTracker()
        instruction = "summarise this"
        pt.record_origin(instruction, "rag-doc", "rag")
        assert pt.detect_spoofing(instruction, "user") is True

    def test_memory_origin_claiming_user_is_spoofed(self):
        pt = ProvenanceTracker()
        instruction = "remember this"
        pt.record_origin(instruction, "mem-store", "memory")
        assert pt.detect_spoofing(instruction, "user") is True

    def test_user_origin_claiming_user_is_not_spoofed(self):
        pt = ProvenanceTracker()
        instruction = "do this"
        pt.record_origin(instruction, "real-user", "user")
        assert pt.detect_spoofing(instruction, "user") is False

    def test_unknown_instruction_returns_false(self):
        pt = ProvenanceTracker()
        assert pt.detect_spoofing("unknown instruction", "user") is False

    def test_agent_origin_claiming_user_not_spoofed(self):
        # agent → user is ambiguous; only tool/rag/memory → user is flagged
        pt = ProvenanceTracker()
        instruction = "agent delegation"
        pt.record_origin(instruction, "agent-b", "agent")
        assert pt.detect_spoofing(instruction, "user") is False


class TestProvenanceTrackerThreadSafety:
    def test_concurrent_record(self):
        pt = ProvenanceTracker()
        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                pt.record_origin(f"instruction {i}", f"source-{i}", "user")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
