"""The launcher's per-tab Claude-account resolution + injection steps.

Invariants:
- Fully best-effort — never blocks/slows the `claude` launch.
- A resolved TabAccount(name, token) is injected as CLAUDE_CODE_OAUTH_TOKEN
  **plus a per-account CLAUDE_CONFIG_DIR profile**. Claude Code ignores the
  token whenever the config dir already holds login credentials (the machine-
  global keychain login wins), so the profile dir — which never holds a login —
  is what actually makes the token effective per tab.
- The profile shares durable user assets (settings, plugins, projects, …) with
  ~/.claude via symlinks and seeds .claude.json minus identity keys, so a tab
  behaves like the user's normal setup, just under a different account.
- No silent identity fallback: when the user explicitly picked an account but
  the profile can't be built, the launcher warns and injects nothing — it never
  lets the tab silently run (and bill) as the machine-global login while
  claiming otherwise.
"""
import io
import json

import pytest

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


def test_skips_and_warns_when_claude_config_dir_preset(monkeypatch):
    """A user-set CLAUDE_CONFIG_DIR is an advanced setup the launcher must not
    clobber — per-tab injection would silently repoint their config."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/custom/claude-config")
    out = io.StringIO()
    calls = []
    r = L._resolve_tab_account_v2(
        "claude",
        stream=out,
        resolve_fn=lambda: ["d"],
        list_fn=lambda a: calls.append(1) or (0, "work\n"),
    )
    assert r is None
    assert calls == []  # never even lists — no misleading picker either
    assert "CLAUDE_CONFIG_DIR" in out.getvalue()


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


# --- per-account profile (CLAUDE_CONFIG_DIR) ------------------------------------

def _mk_home(tmp_path, dirs=(), files=(), state=...):
    """A fake ~/.claude (+ ~/.claude.json) to build profiles from."""
    home_claude = tmp_path / "home-claude"
    home_claude.mkdir()
    for d in dirs:
        (home_claude / d).mkdir()
    for f in files:
        (home_claude / f).write_text(f"<{f}>")
    state_file = tmp_path / "home-state.json"
    if state is ...:
        state = {"theme": "dark", "oauthAccount": {"emailAddress": "a@b"}}
    if state is not None:
        text = state if isinstance(state, str) else json.dumps(state)
        state_file.write_text(text)
    return home_claude, state_file


def test_profile_shares_durable_assets_and_seeds_state(tmp_path):
    home_claude, state_file = _mk_home(
        tmp_path,
        dirs=("plugins", "projects"),
        files=("settings.json", "CLAUDE.md"),
        state={
            "theme": "dark",
            "projects": {"/p": {}},
            "oauthAccount": {"emailAddress": "global@login"},
            "userID": "hash-of-global-login",
        },
    )
    profile = L._ensure_tab_profile(
        "work",
        profile_root=tmp_path / "profiles",
        home_claude_dir=home_claude,
        home_state_file=state_file,
    )
    assert profile == tmp_path / "profiles" / "work"
    for entry in ("plugins", "projects", "settings.json", "CLAUDE.md"):
        link = profile / entry
        assert link.is_symlink() and link.resolve() == (home_claude / entry).resolve()
    seeded = json.loads((profile / ".claude.json").read_text())
    assert seeded["theme"] == "dark"
    assert seeded["projects"] == {"/p": {}}  # onboarding/trust continuity
    assert "oauthAccount" not in seeded  # identity must come from the token
    assert "userID" not in seeded


def test_profile_skips_missing_sources(tmp_path):
    home_claude, state_file = _mk_home(tmp_path, files=("settings.json",))
    profile = L._ensure_tab_profile(
        "work",
        profile_root=tmp_path / "profiles",
        home_claude_dir=home_claude,
        home_state_file=state_file,
    )
    assert (profile / "settings.json").is_symlink()
    assert not (profile / "plugins").is_symlink()  # no dangling links


def test_profile_is_idempotent_and_heals_new_sources(tmp_path):
    home_claude, state_file = _mk_home(tmp_path, dirs=("plugins",))
    root = tmp_path / "profiles"
    profile = L._ensure_tab_profile(
        "work", profile_root=root, home_claude_dir=home_claude,
        home_state_file=state_file,
    )
    # The tab's own evolving state is never clobbered by a relaunch…
    (profile / ".claude.json").write_text('{"theme": "light"}')
    # …and durable assets added to ~/.claude later appear on the next launch.
    (home_claude / "skills").mkdir()
    again = L._ensure_tab_profile(
        "work", profile_root=root, home_claude_dir=home_claude,
        home_state_file=state_file,
    )
    assert again == profile
    assert json.loads((profile / ".claude.json").read_text()) == {"theme": "light"}
    assert (profile / "skills").is_symlink()


def test_profile_without_home_state_still_works(tmp_path):
    home_claude, _ = _mk_home(tmp_path, files=("settings.json",), state=None)
    profile = L._ensure_tab_profile(
        "work",
        profile_root=tmp_path / "profiles",
        home_claude_dir=home_claude,
        home_state_file=tmp_path / "no-such-state.json",
    )
    assert not (profile / ".claude.json").exists()  # fresh onboarding fallback


def test_profile_with_corrupt_home_state_skips_seed(tmp_path):
    home_claude, state_file = _mk_home(tmp_path, state="{not json")
    profile = L._ensure_tab_profile(
        "work",
        profile_root=tmp_path / "profiles",
        home_claude_dir=home_claude,
        home_state_file=state_file,
    )
    assert not (profile / ".claude.json").exists()


def test_profile_creation_failure_raises_oserror(tmp_path):
    home_claude, state_file = _mk_home(tmp_path)
    blocker = tmp_path / "profiles"
    blocker.write_text("a file where the profile root must go")
    with pytest.raises(OSError):
        L._ensure_tab_profile(
            "work",
            profile_root=blocker,
            home_claude_dir=home_claude,
            home_state_file=state_file,
        )


# --- child env -------------------------------------------------------------------

def test_child_env_sets_token_and_config_dir_and_scrubs_overriding_vars(tmp_path):
    base = {
        "PATH": "/usr/bin",
        "SERENA_REAL_CLAUDE": "/x",
        "ANTHROPIC_API_KEY": "sk-ant-api03-leak",
        "ANTHROPIC_AUTH_TOKEN": "bearer",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "CLAUDE_CODE_USE_VERTEX": "1",
        "CLAUDE_CODE_USE_FOUNDRY": "1",
    }
    profile = tmp_path / "profiles" / "work"
    env = L._child_env_for_tab(base, "TKN", profile)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "TKN"
    assert env["CLAUDE_CONFIG_DIR"] == str(profile)
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


# --- launch-time assembly ----------------------------------------------------------

def test_tab_launch_env_none_account_is_plain_launch():
    out = io.StringIO()
    assert L._tab_launch_env(None, out) is None
    assert out.getvalue() == ""


def test_tab_launch_env_builds_profile_and_states_identity(tmp_path):
    out = io.StringIO()
    profile = tmp_path / "p" / "work"
    env = L._tab_launch_env(
        L.TabAccount("work", "TKN"), out, ensure_fn=lambda name: profile
    )
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "TKN"
    assert env["CLAUDE_CONFIG_DIR"] == str(profile)
    text = out.getvalue()
    assert "work" in text
    assert "\x1b]0;claude: work\x07" in text  # terminal tab title


def test_tab_launch_env_profile_failure_warns_and_injects_nothing():
    """Explicit pick + broken profile ⇒ loud warning, NO identity claim, no
    injection — the tab knowingly runs as the machine-global login."""
    out = io.StringIO()

    def boom(_name):
        raise OSError("disk full")

    env = L._tab_launch_env(L.TabAccount("work", "TKN"), out, ensure_fn=boom)
    assert env is None
    text = out.getvalue()
    assert "machine-global" in text
    assert "\x1b]0;" not in text  # never claim an identity we didn't deliver


# --- pure helpers --------------------------------------------------------------

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
