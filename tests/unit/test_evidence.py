from research_agent.evidence import EvidenceStore
from research_agent.tools.base import Source


def src(url, title="t"):
    return Source(tool="x", title=title, url=url, text="body")


def test_ids_use_prefix_and_count_per_prefix():
    store = EvidenceStore()
    assert store.add("W", [src("u1"), src("u2")]) == ["W1", "W2"]
    assert store.add("A", [src("u3")]) == ["A1"]
    assert store.add("W", [src("u4")]) == ["W3"]  # stable across steps
    assert [eid for eid, _ in store.items()] == ["W1", "W2", "A1", "W3"]


def test_same_url_keeps_its_original_id():
    store = EvidenceStore()
    store.add("W", [src("u1")])
    assert store.add("W", [src("u1"), src("u2")]) == ["W1", "W2"]
    assert len(store) == 2


def test_get_unknown_returns_none():
    assert EvidenceStore().get("W9") is None
