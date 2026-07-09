"""The launcher's per-tab Claude-account resolution step.

Invariant under test: this step is *fully best-effort*. It never blocks or
slows the `claude` launch — a missing/old/slow/erroring `dotsync` is swallowed.
When it does resolve an account it returns a `TabAccount(name, token)` that the
launcher injects as `CLAUDE_CODE_OAUTH_TOKEN` into that tab's child process, so
different tabs can run different subscription accounts at once.
"""
import io

from local_dev.serena_mcp_management import serena_agent_launcher as L


def _list(rows):
    """rows: list of (name, has_token). Emits the 4-column porcelain format."""

    def f(_argv):
        return 0, "".join(
            f"{n}\tactive\tmax\t{'token' if has else ''}\n" for n, has in rows
        )

    return f


# --- best-effort skips ---------------------------------------------------------

def test_skips_when_client_is_not_claude():
    calls = []
    L._resolve_tab_account_v2("codex", resolve_fn=lambda: calls.append(1) or ["d"])
    assert calls == []


def test_skips_when_dotsync_absent():
    assert L._resolve_tab_account_v2("claude", resolve_fn=lambda: None) is None


def test_skips_when_list_fails():
    r = L._resolve_tab_account_v2(
        "claude", resolve_fn=lambda: ["d"], list_fn=lambda a: (2, "")
    )
    assert r is None


def test_list_exception_is_swallowed():
    def boom(_a):
        raise TimeoutError("slow")

    r = L._resolve_tab_account_v2("claude", resolve_fn=lambda: ["d"], list_fn=boom)
    assert r is None


def test_zero_accounts_returns_none():
    r = L._resolve_tab_account_v2(
        "claude", resolve_fn=lambda: ["d"], list_fn=_list([])
    )
    assert r is None


# --- single account ------------------------------------------------------------

def test_single_account_with_token_auto_injects():
    tok_calls = []

    r = L._resolve_tab_account_v2(
        "claude",
        stream=io.StringIO(),
        resolve_fn=lambda: ["d"],
        list_fn=_list([("work", True)]),
        pick_fn=lambda a: (_ for _ in ()).throw(AssertionError("must not pick")),
        token_fn=lambda a, name: tok_calls.append(name) or "TKN-work",
    )
    assert r == L.TabAccount("work", "TKN-work")
    assert tok_calls == ["work"]


def test_single_account_without_token_no_injection():
    """Preserve prior single-account behavior: info row, no injection (no token
    yet). Only changes once the user opts in by saving a token."""
    out = io.StringIO()
    tok_calls = []
    r = L._resolve_tab_account_v2(
        "claude",
        stream=out,
        resolve_fn=lambda: ["d"],
        list_fn=_list([("work", False)]),
        token_fn=lambda a, name: tok_calls.append(name) or "X",
    )
    assert r is None
    assert tok_calls == []
    assert "work" in out.getvalue()


# --- multiple accounts ---------------------------------------------------------

def test_multi_account_pick_then_token():
    r = L._resolve_tab_account_v2(
        "claude",
        stream=io.StringIO(),
        resolve_fn=lambda: ["d"],
        list_fn=_list([("work", True), ("personal", True)]),
        pick_fn=lambda a: "personal",
        token_fn=lambda a, name: f"TKN-{name}",
    )
    assert r == L.TabAccount("personal", "TKN-personal")


def test_multi_account_pick_cancelled_returns_none():
    r = L._resolve_tab_account_v2(
        "claude",
        stream=io.StringIO(),
        resolve_fn=lambda: ["d"],
        list_fn=_list([("work", True), ("personal", True)]),
        pick_fn=lambda a: None,  # cancelled / non-tty
        token_fn=lambda a, name: "should-not-be-called",
    )
    assert r is None


def test_picked_token_less_account_warns_and_no_injection():
    """Picking an account with no token must NOT silently fall through to the
    global login (that reproduces the reported bug). Warn loudly, no injection."""
    out = io.StringIO()
    r = L._resolve_tab_account_v2(
        "claude",
        stream=out,
        resolve_fn=lambda: ["d"],
        list_fn=_list([("work", True), ("personal", False)]),
        pick_fn=lambda a: "personal",
        token_fn=lambda a, name: "should-not-be-called",
    )
    assert r is None
    msg = out.getvalue()
    assert "personal" in msg and "token set" in msg


def test_pick_exception_is_swallowed():
    def boom(_a):
        raise RuntimeError("picker died")

    r = L._resolve_tab_account_v2(
        "claude",
        stream=io.StringIO(),
        resolve_fn=lambda: ["d"],
        list_fn=_list([("a", True), ("b", True)]),
        pick_fn=boom,
    )
    assert r is None


def test_token_fetch_failure_returns_none():
    r = L._resolve_tab_account_v2(
        "claude",
        stream=io.StringIO(),
        resolve_fn=lambda: ["d"],
        list_fn=_list([("work", True)]),
        token_fn=lambda a, name: "",  # env command found no token
    )
    assert r is None


# --- pure helpers --------------------------------------------------------------

def test_child_env_injects_token_and_scrubs_overriding_vars():
    base = {
        "PATH": "/usr/bin",
        "SERENA_REAL_CLAUDE": "/x",
        "ANTHROPIC_API_KEY": "sk-ant-api03-leak",
        "ANTHROPIC_AUTH_TOKEN": "bearer",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "CLAUDE_CODE_USE_VERTEX": "1",
        "CLAUDE_CODE_USE_FOUNDRY": "1",
    }
    env = L._child_env_with_token(base, "TKN")
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "TKN"
    # every source that outranks the OAuth token is removed
    for k in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    ):
        assert k not in env
    # unrelated vars the child needs are preserved; base is not mutated
    assert env["PATH"] == "/usr/bin"
    assert env["SERENA_REAL_CLAUDE"] == "/x"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in base


def test_emit_tab_identity_states_name_and_sets_tab_title():
    out = io.StringIO()
    L._emit_tab_identity(out, "changja00")
    text = out.getvalue()
    assert "changja00" in text  # visible banner
    assert "\x1b]0;claude: changja00\x07" in text  # OSC terminal-title escape


def test_parse_account_rows():
    rows = L._parse_account_rows(
        "work\tactive\tmax\ttoken\n"
        "personal\t\tmax\t\n"
        "\n"  # blank line ignored
        "legacy\t\tmax\n"  # 3-col (old dotsync) -> no token
    )
    assert rows == [("work", True), ("personal", False), ("legacy", False)]
