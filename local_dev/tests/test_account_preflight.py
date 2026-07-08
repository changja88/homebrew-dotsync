"""The launcher's optional Claude-account-select preflight step.

Invariant under test: this step is *fully best-effort*. It never blocks or
slows the `claude` launch — a missing/old/slow/erroring `dotsync` is swallowed,
and only a genuine multi-account situation invokes the (dotsync-owned) picker.
"""
import io

from local_dev.serena_mcp_management import serena_agent_launcher as L


def _list_ok(names):
    def f(_argv):
        return 0, "".join(f"{n}\tactive\tmax\n" for n in names)

    return f


def test_skips_when_client_is_not_claude():
    calls = []

    def resolve():
        calls.append(1)
        return ["dotsync"]

    L._run_account_select_v2("codex", resolve_fn=resolve)
    assert calls == []  # never even resolves dotsync for codex


def test_skips_when_dotsync_absent():
    out = io.StringIO()
    listed = []
    L._run_account_select_v2(
        "claude",
        stream=out,
        resolve_fn=lambda: None,
        list_fn=lambda a: listed.append(a) or (0, ""),
    )
    assert listed == []
    assert out.getvalue() == ""


def test_skips_silently_when_list_fails():
    out = io.StringIO()
    selected = []
    L._run_account_select_v2(
        "claude",
        stream=out,
        resolve_fn=lambda: ["dotsync"],
        list_fn=lambda a: (2, ""),  # e.g. old dotsync without the subcommand
        select_fn=lambda a: selected.append(a),
    )
    assert selected == []
    assert out.getvalue() == ""


def test_list_exception_is_swallowed():
    def boom(_a):
        raise TimeoutError("slow")

    out = io.StringIO()
    selected = []
    L._run_account_select_v2(
        "claude",
        stream=out,
        resolve_fn=lambda: ["dotsync"],
        list_fn=boom,
        select_fn=lambda a: selected.append(a),
    )
    assert selected == []
    assert out.getvalue() == ""


def test_zero_accounts_no_output_no_picker():
    out = io.StringIO()
    selected = []
    L._run_account_select_v2(
        "claude",
        stream=out,
        resolve_fn=lambda: ["dotsync"],
        list_fn=_list_ok([]),
        select_fn=lambda a: selected.append(a),
    )
    assert selected == []
    assert out.getvalue() == ""


def test_one_account_shows_summary_but_no_picker():
    out = io.StringIO()
    selected = []
    L._run_account_select_v2(
        "claude",
        stream=out,
        resolve_fn=lambda: ["dotsync"],
        list_fn=_list_ok(["work"]),
        select_fn=lambda a: selected.append(a),
    )
    assert selected == []
    assert "work" in out.getvalue()


def test_two_accounts_invokes_dotsync_picker():
    selected = []
    L._run_account_select_v2(
        "claude",
        stream=io.StringIO(),
        resolve_fn=lambda: ["/opt/homebrew/bin/dotsync"],
        list_fn=_list_ok(["work", "personal"]),
        select_fn=lambda argv: selected.append(argv),
    )
    assert selected == [["/opt/homebrew/bin/dotsync"]]


def test_select_error_never_propagates():
    def boom(_a):
        raise RuntimeError("picker died")

    # must not raise — the launch must proceed regardless
    L._run_account_select_v2(
        "claude",
        stream=io.StringIO(),
        resolve_fn=lambda: ["dotsync"],
        list_fn=_list_ok(["a", "b"]),
        select_fn=boom,
    )
