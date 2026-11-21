from research_agent.evidence import EvidenceStore
from research_agent.tools.base import Source


def src(url, title="t", text="body " * 100):
    return Source(tool="x", title=title, url=url, text=text)


def test_ids_use_prefix_and_count_per_prefix():
    store = EvidenceStore()
    assert store.add("W", [src("u1"), src("u2")]).ids == ["W1", "W2"]
    assert store.add("A", [src("u3")]).ids == ["A1"]
    assert store.add("W", [src("u4")]).ids == ["W3"]  # stable across steps
    assert [eid for eid, _ in store.items()] == ["W1", "W2", "A1", "W3"]


def test_same_url_keeps_its_original_id_and_is_not_new():
    store = EvidenceStore()
    store.add("W", [src("u1")])
    added = store.add("W", [src("u1"), src("u2")])
    assert added.ids == ["W1", "W2"] and added.new_ids == ["W2"]
    assert len(store) == 2


def test_get_unknown_returns_none():
    assert EvidenceStore().get("W9") is None


def test_budget_trims_the_source_that_crosses_it_and_drops_later_ones():
    store = EvidenceStore(char_budget=1000)
    added = store.add(
        "W", [src("u1", text="x" * 600), src("u2", text="y" * 600), src("u3", text="z" * 600)]
    )
    assert added.ids == ["W1", "W2"] and added.trimmed == 1 and added.dropped == 1
    assert len(store.get("W1").text) == 600  # fits whole
    assert 0 < len(store.get("W2").text) < 600  # trimmed to what was left
    assert store.chars_left < 300  # nearly spent


def test_budget_counts_each_source_once_even_if_returned_again():
    store = EvidenceStore(char_budget=1000)
    store.add("W", [src("u1", text="x" * 500)])
    left = store.chars_left
    store.add("W", [src("u1", text="x" * 500)])
    assert store.chars_left == left


def test_a_spent_budget_drops_everything_and_stays_consistent():
    store = EvidenceStore(char_budget=400)
    store.add("W", [src("u1", text="x" * 5000)])
    added = store.add("W", [src("u2")])
    assert added.ids == [] and added.dropped == 1 and len(store) == 1
