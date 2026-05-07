import io

from local_dev.serena_mcp_management.ui import confirm


def test_confirm_returns_true_for_yes_input():
    stream = io.StringIO()
    answers = iter(["y"])
    assert confirm("Run codex?", default=False,
                   stream=stream, input_fn=lambda: next(answers)) is True


def test_confirm_returns_false_for_no_input():
    stream = io.StringIO()
    answers = iter(["n"])
    assert confirm("Run codex?", default=True,
                   stream=stream, input_fn=lambda: next(answers)) is False


def test_confirm_returns_default_on_empty_input():
    stream = io.StringIO()
    assert confirm("Run codex?", default=True,
                   stream=stream, input_fn=lambda: "") is True
    assert confirm("Run codex?", default=False,
                   stream=stream, input_fn=lambda: "") is False


def test_confirm_uppercase_default_marker():
    stream = io.StringIO()
    confirm("Run codex?", default=True, stream=stream, input_fn=lambda: "")
    assert "[Y/n]" in stream.getvalue()
    stream2 = io.StringIO()
    confirm("Run codex?", default=False, stream=stream2, input_fn=lambda: "")
    assert "[y/N]" in stream2.getvalue()


def test_confirm_accepts_yes_no_words():
    stream = io.StringIO()
    answers_yes = iter(["yes"])
    answers_no = iter(["no"])
    assert confirm("?", default=False, stream=stream,
                   input_fn=lambda: next(answers_yes)) is True
    assert confirm("?", default=True, stream=stream,
                   input_fn=lambda: next(answers_no)) is False
