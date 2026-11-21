"""The eight reference questions from the assignment, with their intended types."""

REFERENCE_QUESTIONS: list[tuple[int, str, str]] = [
    (
        1,
        "Single-source factual",
        "What is the Federal Reserve's discount window and how does it work?",
    ),
    (2, "Single-source factual", "What are the Basel III capital requirements for banks?"),
    (
        3,
        "Academic search",
        "What recent academic research exists on using machine learning for credit risk assessment?",
    ),
    (
        4,
        "Multi-source synthesis",
        "How did the Federal Reserve's monetary policy response to the 2008 financial crisis differ "
        "from its response to COVID-19?",
    ),
    (
        5,
        "Data retrieval (FRED)",
        "What is the current US unemployment rate and how has it changed over the past year?",
    ),
    (
        6,
        "Cross-tool synthesis",
        "Explain the relationship between yield curve inversions and recessions. Are there recent "
        "academic papers on this topic?",
    ),
    (7, "Out-of-scope (should refuse or caveat)", "What is the best restaurant in New York City?"),
    (
        8,
        "Speculative / emerging",
        "What are the implications of quantum computing for banking encryption?",
    ),
]
