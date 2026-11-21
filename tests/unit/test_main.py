import pytest

import main
from research_agent.llm import LLMError


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(main, "load_dotenv", lambda: None)  # never read a real .env in tests


def run_main(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["main.py", *args])
    return main.main()


def test_prints_the_answer_and_defaults_to_multi_step(monkeypatch, capsys):
    seen = {}

    def fake_answer(q, max_steps):
        seen.update(q=q, max_steps=max_steps)
        return f"answer to: {q}"

    monkeypatch.setattr(main, "answer", fake_answer)
    run_main(monkeypatch, "What", "is", "Basel III?")
    assert capsys.readouterr().out == "answer to: What is Basel III?\n"
    assert seen == {"q": "What is Basel III?", "max_steps": main.DEFAULT_MAX_STEPS}
    assert main.DEFAULT_MAX_STEPS > 1


@pytest.mark.parametrize(
    "flags, expected",
    [(["--single-pass"], 1), (["--max-steps", "5"], 5), (["--max-steps", "1"], 1)],
)
def test_the_step_flags(monkeypatch, flags, expected):
    seen = {}
    monkeypatch.setattr(main, "answer", lambda q, max_steps: seen.update(n=max_steps) or "a")
    run_main(monkeypatch, *flags, "q")
    assert seen["n"] == expected


def test_flags_can_follow_the_question(monkeypatch):
    seen = {}
    monkeypatch.setattr(main, "answer", lambda q, max_steps: seen.update(q=q, n=max_steps) or "a")
    run_main(monkeypatch, "What", "is", "X?", "--single-pass")
    assert seen == {"q": "What is X?", "n": 1}


def test_single_pass_and_max_steps_are_mutually_exclusive(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, "--single-pass", "--max-steps", "3", "q")
    assert exc.value.code == 2


def test_max_steps_must_be_positive(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, "--max-steps", "0", "q")
    assert exc.value.code == 2


def test_no_question_is_a_usage_error(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch)
    assert exc.value.code == 2


def test_model_errors_exit_with_a_clean_message_not_a_traceback(monkeypatch):
    def boom(q, max_steps):
        raise LLMError(
            "GROQ_API_KEY is not set. Copy .env.example to .env and add a free Groq key."
        )

    monkeypatch.setattr(main, "answer", boom)
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, "q")
    assert str(exc.value).startswith("error: GROQ_API_KEY is not set")
