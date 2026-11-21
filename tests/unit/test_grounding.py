import pytest

from research_agent.grounding import figure_problem, figures, keywords

SRC = (
    "Basel III sets a minimum Common Equity Tier 1 ratio of 4.5% of risk-weighted assets. "
    "The leverage ratio is 3 percent of total exposure. Primary credit is priced about "
    "100 basis points above the federal funds target. Borrowing reached $46 billion after "
    "the attacks, across 1,200 loans. Loan terms were extended to up to thirty days. "
    "Staff numbered 50\u00a0000."
)


def values(text):
    return [(format(f.value, "f"), f.unit) for f in figures(text)]


def test_figures_normalize_commas_currency_scale_units_and_words():
    assert values("1,200 loans") == [("1200", None)]
    assert values("$46 billion") == [("46000000000", None)]
    assert values("4.50%") == [("4.5", "%")]
    assert values("4.5 percent and 4.5 per cent") == [("4.5", "%"), ("4.5", "%")]
    assert values("100 bps, 100 basis points") == [("100", "bp"), ("100", "bp")]
    assert values("up to thirty days") == [("30", None)]
    assert values("5k") == [("5000", None)]


def test_figures_ignore_digits_inside_identifiers_and_use_unicode_dashes():
    assert values("CET1 and Q3 ratios") == []
    assert [str(f.value) for f in figures("2007–2009")] == ["2007", "2009"]
    assert [f.value for f in figures("published 2025‑01‑05")] == ["2025-01-05"]


@pytest.mark.parametrize(
    "claim",
    [
        "The minimum Common Equity Tier 1 ratio is 4.5 percent of risk-weighted assets.",  # % vs percent
        "Borrowing reached $46 billion after the attacks.",
        "There were 1200 loans.",  # comma
        "The leverage ratio is 3% of exposure.",  # percent vs %
        "Primary credit is priced about 100 bps above the federal funds target.",  # bps
        "Loan terms were extended up to 30 days.",  # digits vs spelled-out
        "Staff numbered 50000.",
        "There are 3 facilities.",  # single digit: not checked
    ],
)
def test_normalized_figures_are_grounded(claim):
    assert figure_problem(claim, SRC) is None


def test_figure_not_in_source_is_reported():
    problem = figure_problem("Borrowing reached $77 billion.", SRC)
    assert "does not appear" in problem and "77" in problem


def test_unit_mismatch_is_not_a_match():
    # 100 is in the source only as basis points, not as a percent
    assert "does not appear" in figure_problem("The ratio is 100%.", SRC)


def test_right_number_wrong_fact_is_caught_by_proximity():
    # "3 percent" is in the source, next to "leverage ratio", not next to "dividend payout"
    problem = figure_problem("The dividend payout limit is 3% of earnings.", SRC)
    assert "not near" in problem and "dividend" in problem


def test_right_number_right_fact_passes_proximity():
    assert figure_problem("The leverage ratio minimum is 3%.", SRC) is None


def test_plural_and_case_variants_count_as_near():
    assert figure_problem("Leverage Ratios must be at least 3%.", SRC) is None


def test_proximity_uses_words_near_each_number_not_the_whole_claim():
    claim = "Primary credit costs 100 basis points over target, while the leverage ratio is 3%."
    assert figure_problem(claim, SRC) is None
    swapped = "The leverage ratio is 100 basis points, and primary credit is 3%."
    assert "not near" in figure_problem(swapped, SRC)


def test_dates_are_compared_as_dates():
    hay = "Some paper\nabstract\npublished 2025-01-05"
    assert figure_problem("It was published on 2025-01-05.", hay) is None
    assert figure_problem("It was published on 2025-01-06.", hay)


def test_window_stops_at_the_sentence_boundary():
    src = "The leverage ratio is 5 percent. Dividends are separate and capped at 7 percent."
    assert figure_problem("The leverage ratio is 5%.", src) is None
    assert "not near" in figure_problem("The leverage ratio is 7%.", src)


def test_a_claim_with_two_figures_in_one_clause_shares_context():
    # Known limit: with no comma or period between them, neighbouring words are shared, so
    # swapped figures in one clause can pass. Clause bounding only separates what punctuation does.
    swapped = "The leverage ratio is 100 basis points and primary credit is 3%."
    assert figure_problem(swapped, SRC) is None


LIST_SRC = (
    "=== Common Equity Tier 1 (CET1) capital requirements ===\n"
    "Basel III requires banks to have a minimum CET1 ratio (Common Tier 1 capital divided by "
    "risk-weighted assets (RWAs)) at all times of:\n\n4.5%\nPlus:\n\n"
    'A mandatory "capital conservation buffer" of at least 2.5% of RWAs.'
)


def test_number_alone_on_its_own_line_is_described_by_its_lead_in():
    assert figure_problem("Basel III requires a minimum CET1 ratio of 4.5%.", LIST_SRC) is None


def test_lead_in_does_not_ground_an_unrelated_claim():
    assert "not near" in figure_problem("Dividend payouts are capped at 4.5%.", LIST_SRC)


def test_dividend_is_not_confused_with_divided():
    # "divided" is in LIST_SRC; "dividend" is not
    assert "not near" in figure_problem("The dividend limit is 4.5%.", LIST_SRC)


def test_years_only_need_to_be_present():
    src = "The Fed acted on March 16, 2008, announcing a new facility."
    assert figure_problem("The Fed announced a new facility in 2008.", src) is None
    assert "does not appear" in figure_problem("The facility opened in 2009.", src)


def test_stem_strips_light_suffixes_only():
    from research_agent.grounding import stem

    assert [stem(w) for w in ("ratios", "divided", "dividend", "requires", "rate")] == [
        "ratio",
        "divid",
        "dividend",
        "requir",
        "rate",
    ]


def test_keywords_drop_stopwords_short_words_and_numbers():
    assert keywords("The ratio of 4.5 is about the minimum") == {"ratio"}
