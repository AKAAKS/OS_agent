"""Tests for kernel FixMemory (fake collection — no real chromadb)."""

from agentkernel.memory import FixMemory


class FakeCollection:
    def __init__(self, query_result=None):
        self.added = []
        self.query_result = query_result or {
            "ids": [[]], "distances": [[]], "metadatas": [[]], "documents": [[]],
        }

    def add(self, documents, metadatas, ids):
        self.added.append({"documents": documents, "metadatas": metadatas, "ids": ids})

    def query(self, **kwargs):
        return self.query_result

    def count(self):
        return 1


def _memory(fake: FakeCollection, threshold: float = 0.9) -> FixMemory:
    # Bypass __init__ so tests never construct a real chromadb client.
    mem = FixMemory.__new__(FixMemory)
    mem.similarity_threshold = threshold
    mem.db_path = "unused"
    mem._available = True
    mem._collection = fake
    mem.client = None
    return mem


def _hit(distance: float, metadata: dict) -> dict:
    return {
        "ids": [["fix_1"]],
        "distances": [[distance]],
        "metadatas": [[metadata]],
        "documents": [["chrome eating 95% CPU"]],
    }


def test_unavailable_memory_is_inert():
    mem = FixMemory.__new__(FixMemory)
    mem._available = False
    mem._collection = None
    assert mem.available is False
    assert mem.find_similar_fix("anything") is None
    mem.store_fix("a", "cpu_spike", "killed it", True)  # must not raise


def test_below_threshold_returns_none():
    fake = FakeCollection(_hit(0.5, {"success": "True", "action": "kill_process"}))
    assert _memory(fake, threshold=0.9).find_similar_fix("cpu spike") is None


def test_match_returns_structured_action():
    fake = FakeCollection(_hit(0.05, {
        "success": "True",
        "action": "kill_process",
        "fix_applied": "Killed chrome",
        "anomaly_type": "cpu_spike",
        "timestamp": "2026-07-01T00:00:00",
    }))
    result = _memory(fake, threshold=0.9).find_similar_fix("cpu spike")
    assert result is not None
    assert result["action"] == "kill_process"
    assert result["fix_applied"] == "Killed chrome"
    assert result["similarity"] == 0.95


def test_failed_past_fix_is_not_reused():
    fake = FakeCollection(_hit(0.05, {"success": "False", "action": "kill_process"}))
    assert _memory(fake, threshold=0.9).find_similar_fix("cpu spike") is None


def test_store_fix_flattens_details_into_metadata():
    fake = FakeCollection()
    mem = _memory(fake)
    mem.store_fix(
        anomaly_summary="chrome eating CPU",
        anomaly_type="cpu_spike",
        fix_applied="Killed chrome",
        success=True,
        details={"action": "kill_process", "risk": "low"},
    )
    assert len(fake.added) == 1
    metadata = fake.added[0]["metadatas"][0]
    assert metadata["action"] == "kill_process"
    assert metadata["success"] == "True"
