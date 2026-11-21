from research_agent.evidence import EvidenceStore
from research_agent.synthesis import (
    Answer,
    Claim,
    Synthesis,
    Unverified,
    classify,
    clean_text,
    normalize_ids,
    render,
    synthesize,
)
from research_agent.tools.base import Source
from tests.fakes import answer_json


def make_store():
    store = EvidenceStore()
    store.add(
        "W",
        [
            Source(
                tool="wikipedia",
                title="Discount window",
                url="https://en.wikipedia.org/wiki/Discount_window",
                text="Primary credit is priced about 100 basis points above the target. "
                "Borrowing reached $46 billion after 9/11, or 1,200 loans.",
            )
        ],
    )
    store.add(
        "A",
        [
            Source(
                tool="arxiv",
                title="Credit models",
                url="https://arxiv.org/abs/2501.1",
                text="We study default prediction.",
                native_id="arXiv:2501.1",
                meta={"published": "2025-01-05"},
            )
        ],
    )
    return store


def answer(*claims, inferences=(), caveats=(), refusal=""):
    return Answer(
        claims=[Claim(text=t, evidence_ids=ids) for t, ids in claims],
        inferences=list(inferences),
        caveats=list(caveats),
        refusal_reason=refusal,
    )


def test_normalize_ids_splits_comma_strings_and_dedupes():
    assert normalize_ids(["w1, a1", "W1", " ", "a1;W2"]) == ["W1", "A1", "W2"]


def test_clean_text_strips_model_written_markers():
    assert clean_text("Rates rose 【1†L1-L4】 sharply [W1] .") == "Rates rose sharply ."


def test_valid_claims_are_sourced():
    result = classify(answer(("Priced about 100 basis points above target.", ["W1"])), make_store())
    assert [c.evidence_ids for c in result.sourced] == [["W1"]]
    assert result.unverified == []


def test_unknown_id_demotes_claim_with_reason_and_keeps_text():
    result = classify(answer(("Something unsupported.", ["W9"])), make_store())
    assert result.sourced == []
    assert result.unverified[0].text == "Something unsupported."
    assert "unknown evidence IDs ['W9']" in result.unverified[0].reason


def test_claim_with_one_valid_and_one_unknown_id_is_demoted_whole():
    result = classify(answer(("Mixed.", ["W1", "A9"])), make_store())
    assert len(result.unverified) == 1 and result.sourced == []


def test_claim_without_evidence_is_demoted():
    result = classify(answer(("No cite.", [])), make_store())
    assert result.unverified[0].reason == "cites no evidence"


def test_figure_not_in_cited_source_is_demoted():
    result = classify(answer(("Borrowing reached $77 billion.", ["W1"])), make_store())
    assert "77" in result.unverified[0].reason


def test_figure_near_the_wrong_words_is_demoted():
    result = classify(answer(("Dividends were capped at 100 basis points.", ["W1"])), make_store())
    assert "not near" in result.unverified[0].reason


def test_figures_in_source_pass_including_commas_dates_and_ids():
    ok = answer(
        ("Borrowing reached $46 billion across 1200 loans.", ["W1"]),
        ("Published 2025-01-05 as arXiv:2501.1.", ["A1"]),
    )
    result = classify(ok, make_store())
    assert len(result.sourced) == 2 and not result.unverified


def test_single_digit_numbers_are_not_checked():
    assert classify(answer(("There are 3 facilities.", ["W1"])), make_store()).unverified == []


def test_comma_joined_ids_in_one_string_are_accepted():
    result = classify(answer(("Priced about 100 basis points.", ["W1, A1"])), make_store())
    assert result.sourced[0].evidence_ids == ["W1", "A1"]


def test_synthesize_repairs_unknown_id_once(fake_llm):
    fake_llm.queue(
        answer_json(("Priced about 100 basis points.", ["W7"])),
        answer_json(("Priced about 100 basis points.", ["W1"])),
    )
    result = synthesize("q", make_store())
    assert result.sourced and not result.unverified
    assert "Valid evidence IDs: ['W1', 'A1']" in fake_llm.last_user_message


def test_synthesize_demotes_when_repair_fails(fake_llm):
    fake_llm.always(answer_json(("Unsupported.", ["W7"])))
    result = synthesize("q", make_store())
    assert result.sourced == []
    assert result.unverified[0].text == "Unsupported."  # kept, not dropped
    assert len(fake_llm.calls) == 2


def test_synthesize_reports_unparseable_model_output(fake_llm):
    fake_llm.always("nope")
    result = synthesize("q", make_store())
    assert "valid structured answer" in result.refusal_reason


def test_render_writes_markers_and_separates_sections():
    result = Synthesis(
        sourced=[Claim(text="Fact.", evidence_ids=["W1", "A1"])],
        unverified=[Unverified(text="Guess.", reason="cites no evidence")],
        inferences=["Probably so."],
        caveats=["Old data."],
    )
    out = render(result, make_store())
    assert "- Fact. [W1][A1]" in out
    assert (
        out.index("## From sources") < out.index("## Unverified") < out.index("## Model inference")
    )
    assert "- Guess. (not shown as sourced: cites no evidence)" in out
    assert "- Inference: Probably so." in out and "- Caveat: Old data." in out
    assert "[W1] Discount window - https://en.wikipedia.org/wiki/Discount_window" in out
    assert "[A1] Credit models (arXiv:2501.1) - https://arxiv.org/abs/2501.1" in out


def test_render_refusal_without_sources():
    out = render(Synthesis(refusal_reason="Out of scope."), EvidenceStore())
    assert "## Cannot answer from sources\nOut of scope." in out
    assert "## Sources\n(none)" in out
    assert "## From sources" not in out


def test_spelled_out_numbers_in_sources_match_digits_in_claims():
    store = EvidenceStore()
    store.add(
        "W",
        [
            Source(
                tool="wikipedia",
                title="T",
                url="u",
                text="Loan terms were extended to up to thirty days, later ninety days.",
            )
        ],
    )
    result = classify(answer(("Terms rose to 30 days and then 90 days.", ["W1"])), store)
    assert len(result.sourced) == 1 and not result.unverified


def test_render_partial_answer_shows_the_claims_and_the_gap():
    result = Synthesis(
        sourced=[Claim(text="In 2008 the Fed cut rates.", evidence_ids=["W1"])],
        refusal_reason="The sources do not cover the Fed's COVID-19 response.",
    )
    out = render(result, make_store())
    assert "- In 2008 the Fed cut rates. [W1]" in out
    assert "## Gaps\nThe sources do not cover the Fed's COVID-19 response." in out
    assert "## Cannot answer from sources" not in out
