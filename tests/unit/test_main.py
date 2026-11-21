import pytest

import main
from research_agent.llm import LLMError


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(main, "load_dotenv", lambda: None)  # never read a real .env in tests


def run_main(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["main.py", *args])
    return main.main()


def test_prints_the_answer(monkeypatch, capsys):
    monkeypatch.setattr(main, "answer", lambda q: f"answer to: {q}")
    run_main(monkeypatch, "What", "is", "Basel III?")
    assert capsys.readouterr().out == "answer to: What is Basel III?\n"


def test_no_question_prints_usage_and_exits_nonzero(monkeypatch):
    with pytest.raises(SystemExit, match="Usage"):
        run_main(monkeypatch)


def test_model_errors_exit_with_a_clean_message_not_a_traceback(monkeypatch, capsys):
    def boom(q):
        raise LLMError(
            "GROQ_API_KEY is not set. Copy .env.example to .env and add a free Groq key."
        )

    monkeypatch.setattr(main, "answer", boom)
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, "q")
    assert str(exc.value).startswith("error: GROQ_API_KEY is not set")
