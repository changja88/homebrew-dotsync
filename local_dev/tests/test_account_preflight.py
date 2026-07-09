"""The launcher's per-tab Claude-account resolution step.

Invariant: fully best-effort — never blocks/slows the `claude` launch. When it
resolves an account it returns a TabAccount(name, token) the launcher injects as
CLAUDE_CODE_OAUTH_TOKEN. Every saved tab account has a token, so there is no
token-less branch.
"""
import io

from local_dev.serena_mcp_management import serena_agent_launcher as L


def _list(names):
    def f(_argv):
        return 0, "".join(f"{n}\n" for n in names)

    return f


# --- best-effort skips ---------------------------------------------------------

def test_skips_when_client_is_not_claude():
    calls = []
    L._resolve_tab_account_v2("codex", resolve_fn=lambda: calls.append(1) or ["d"])
    assert calls == []


def test_skips_when_dotsync_absent():
    assert L._resolve_tab_account_v2("claude", resolve_fn=lambda: None) is None


def test_skips_when_list_fails():
    assert L._resolve_tab_account_v2(
        "claude", resolve_fn=lambda: ["d"], list_fn=lambda a: (2, "")
    ) is None


def test_list_exception_is_swallowed():
    def boom(_a):
        raise TimeoutError("slow")

    assert L._resolve_tab_account_v2("claude", resolve_fn=lambda: ["d"], list_fn=boom) is None


def test_zero_accounts_returns_none():
    assert L._resolve_tab_account_v2("claude", resolve_fn=lambda: ["d"], list_fn=_list([])) is None


# --- single account ------------------------------------------------------------

def test_single_account_auto_injects():
    tok_calls = []
    r = L._resolve_tab_account_v2(
        "claude",
        stream=io.StringIO(),
        resolve_fn=lambda: ["d"],
        list_fn=_list(["work"]),
        pick_fn=lambda a: (_ for _ in ()).throw(AssertionError("must not pick")),
        token_fn=lambda a, name: tok_calls.append(name) or "TKN-work",
    )
    assert r == L.TabAccount("work", "TKN-work")
    assert tok_calls == ["work"]


# --- multiple accounts ---------------------------------------------------------

def test_multi_account_pick_then_token():
    r = L._resolve_tab_account_v2(
        "claude",
        stream=io.StringIO(),
        resolve_fn=lambda: ["d"],
        list_fn=_list(["work", "personal"]),
        pick_fn=lambda a: "personal",
        token_fn=lambda a, name: f"TKN-{name}",
    )
    assert r == L.TabAccount("personal", "TKN-personal")


def test_multi_account_pick_cancelled_returns_none():
    r = L._resolve_tab_account_v2(
        "claude",
        stream=io.StringIO(),
        resolve_fn=lambda: ["d"],
        list_fn=_list(["work", "personal"]),
        pick_fn=lambda a: None,  # cancelled / non-tty
        token_fn=lambda a, name: "should-not-be-called",
    )
    assert r is None


def test_pick_exception_is_swallowed():
    def boom(_a):
        raise RuntimeError("picker died")

    r = L._resolve_tab_account_v2(
        "claude",
        stream=io.StringIO(),
        resolve_fn=lambda: ["d"],
        list_fn=_list(["a", "b"]),
        pick_fn=boom,
    )
    assert r is None


def test_token_fetch_failure_returns_none():
    r = L._resolve_tab_account_v2(
        "claude",
        stream=io.StringIO(),
        resolve_fn=lambda: ["d"],
        list_fn=_list(["work"]),
        token_fn=lambda a, name: "",  # env command found nothing
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
    for k in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    ):
        assert k not in env
    assert env["PATH"] == "/usr/bin"  # child essentials preserved
    assert env["SERENA_REAL_CLAUDE"] == "/x"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in base  # base not mutated


def test_emit_tab_identity_states_name_and_sets_tab_title():
    out = io.StringIO()
    L._emit_tab_identity(out, "changja00")
    text = out.getvalue()
    assert "changja00" in text
    assert "\x1b]0;claude: changja00\x07" in text  # OSC terminal-title escape


def test_parse_account_names():
    assert L._parse_account_names("work\npersonal\n\n  home  \n") == [
        "work",
        "personal",
        "home",
    ]
