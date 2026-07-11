"""Per-tab Claude profile selection and login isolation.

Invariants:
- The launcher owns profile discovery and selection; it never shells out to
  dotsync and never reads or injects setup-token values.
- A selected profile sets only ``CLAUDE_CONFIG_DIR`` after removing competing
  credential environment variables. Claude Code owns ``/login`` credentials.
- Durable settings and plugins are shared with ``~/.claude`` while
  ``.claude.json`` identity fields stay per profile.
- A selected profile that cannot be prepared fails closed instead of silently
  falling back to the machine-global login.
"""
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_dev.serena_mcp_management import serena_agent_launcher as L


# --- profile discovery and selection -----------------------------------------


def test_skips_when_client_is_not_claude():
    calls = []
    result = L._resolve_tab_profile(
        "codex", list_fn=lambda root: calls.append(root) or []
    )
    assert result is None
    assert calls == []


def test_skips_and_warns_when_claude_config_dir_is_preset(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/custom/claude-config")
    out = io.StringIO()
    calls = []

    result = L._resolve_tab_profile(
        "claude",
        stream=out,
        profile_root=tmp_path,
        list_fn=lambda root: calls.append(root) or ["work"],
    )

    assert result is None
    assert calls == []
    assert "CLAUDE_CONFIG_DIR" in out.getvalue()


def test_profile_names_come_from_real_profile_directories(tmp_path):
    root = tmp_path / "profiles"
    root.mkdir()
    (root / "zeta").mkdir()
    (root / "alpha").mkdir()
    (root / "not-a-profile.txt").write_text("x")
    (root / "bad name").mkdir()
    (root / "linked").symlink_to(root / "alpha", target_is_directory=True)

    assert L._tab_profile_names(root) == ["alpha", "zeta"]


def test_zero_profiles_is_plain_launch(tmp_path):
    assert L._resolve_tab_profile(
        "claude", profile_root=tmp_path, list_fn=lambda root: []
    ) is None


def test_single_profile_is_selected_without_picker(tmp_path):
    pick_calls = []
    result = L._resolve_tab_profile(
        "claude",
        profile_root=tmp_path,
        list_fn=lambda root: ["work"],
        pick_fn=lambda names: pick_calls.append(names) or "unexpected",
    )

    assert result == L.TabProfile("work")
    assert pick_calls == []


def test_multiple_profiles_use_launcher_picker(tmp_path):
    result = L._resolve_tab_profile(
        "claude",
        profile_root=tmp_path,
        list_fn=lambda root: ["personal", "work"],
        pick_fn=lambda names: "personal",
    )

    assert result == L.TabProfile("personal")


def test_cancelled_profile_picker_aborts_instead_of_using_global_login(tmp_path):
    out = io.StringIO()
    with pytest.raises(L.TabProfileError):
        L._resolve_tab_profile(
            "claude",
            stream=out,
            profile_root=tmp_path,
            list_fn=lambda root: ["personal", "work"],
            pick_fn=lambda names: None,
        )

    assert "aborting" in out.getvalue().lower()


def test_auth_login_can_create_first_profile(tmp_path):
    result = L._resolve_tab_profile(
        "claude",
        profile_root=tmp_path,
        list_fn=lambda root: [],
        new_name_fn=lambda: "work",
        allow_create=True,
    )

    assert result == L.TabProfile("work")


def test_auth_login_picker_can_add_another_profile(tmp_path):
    result = L._resolve_tab_profile(
        "claude",
        profile_root=tmp_path,
        list_fn=lambda root: ["work"],
        pick_fn=lambda names: L.ADD_PROFILE_CHOICE,
        new_name_fn=lambda: "personal",
        allow_create=True,
    )

    assert result == L.TabProfile("personal")


def test_invalid_new_profile_name_is_rejected(tmp_path):
    out = io.StringIO()
    result = L._resolve_tab_profile(
        "claude",
        stream=out,
        profile_root=tmp_path,
        list_fn=lambda root: [],
        new_name_fn=lambda: "../escape",
        allow_create=True,
    )

    assert result is None
    assert "invalid" in out.getvalue().lower()


# --- per-account CLAUDE_CONFIG_DIR -------------------------------------------


def _mk_home(tmp_path, dirs=(), files=(), state=...):
    home_claude = tmp_path / "home-claude"
    home_claude.mkdir()
    for directory in dirs:
        (home_claude / directory).mkdir()
    for filename in files:
        (home_claude / filename).write_text(f"<{filename}>")
    state_file = tmp_path / "home-state.json"
    if state is ...:
        state = {"theme": "dark", "oauthAccount": {"emailAddress": "a@b"}}
    if state is not None:
        text = state if isinstance(state, str) else json.dumps(state)
        state_file.write_text(text)
    return home_claude, state_file


def test_profile_shares_durable_assets_and_seeds_non_identity_state(tmp_path):
    home_claude, state_file = _mk_home(
        tmp_path,
        dirs=("plugins", "projects", "rules", "themes", "output-styles"),
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
    for entry in (
        "plugins",
        "projects",
        "rules",
        "themes",
        "output-styles",
        "settings.json",
        "CLAUDE.md",
    ):
        link = profile / entry
        assert link.is_symlink()
        assert link.resolve() == (home_claude / entry).resolve()
    seeded = json.loads((profile / ".claude.json").read_text())
    assert seeded["theme"] == "dark"
    assert seeded["projects"] == {"/p": {}}
    assert "oauthAccount" not in seeded
    assert "userID" not in seeded


def test_profile_skips_missing_shared_sources(tmp_path):
    home_claude, state_file = _mk_home(tmp_path, files=("settings.json",))
    profile = L._ensure_tab_profile(
        "work",
        profile_root=tmp_path / "profiles",
        home_claude_dir=home_claude,
        home_state_file=state_file,
    )

    assert (profile / "settings.json").is_symlink()
    assert not (profile / "plugins").is_symlink()


def test_profile_is_idempotent_and_heals_new_shared_sources(tmp_path):
    home_claude, state_file = _mk_home(tmp_path, dirs=("plugins",))
    root = tmp_path / "profiles"
    profile = L._ensure_tab_profile(
        "work",
        profile_root=root,
        home_claude_dir=home_claude,
        home_state_file=state_file,
    )
    (profile / ".claude.json").write_text(
        '{"theme": "light", "oauthAccount": {"emailAddress": "work@login"}}'
    )
    (home_claude / "skills").mkdir()

    again = L._ensure_tab_profile(
        "work",
        profile_root=root,
        home_claude_dir=home_claude,
        home_state_file=state_file,
    )

    assert again == profile
    state = json.loads((profile / ".claude.json").read_text())
    assert state["oauthAccount"]["emailAddress"] == "work@login"
    assert (profile / "skills").is_symlink()


def test_profile_without_home_state_still_works(tmp_path):
    home_claude, _ = _mk_home(tmp_path, files=("settings.json",), state=None)
    profile = L._ensure_tab_profile(
        "work",
        profile_root=tmp_path / "profiles",
        home_claude_dir=home_claude,
        home_state_file=tmp_path / "no-such-state.json",
    )
    assert not (profile / ".claude.json").exists()


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


# --- child environment and launch --------------------------------------------


def test_child_env_sets_profile_and_scrubs_all_competing_credentials(tmp_path):
    base = {
        "PATH": "/usr/bin",
        "SERENA_REAL_CLAUDE": "/x",
        "CLAUDE_CODE_OAUTH_TOKEN": "legacy-setup-token",
        "ANTHROPIC_API_KEY": "sk-ant-api03-leak",
        "ANTHROPIC_AUTH_TOKEN": "bearer",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "CLAUDE_CODE_USE_VERTEX": "1",
        "CLAUDE_CODE_USE_FOUNDRY": "1",
    }
    profile = tmp_path / "profiles" / "work"

    env = L._child_env_for_profile(base, profile)

    assert env["CLAUDE_CONFIG_DIR"] == str(profile)
    for key in (
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    ):
        assert key not in env
    assert env["PATH"] == "/usr/bin"
    assert env["SERENA_REAL_CLAUDE"] == "/x"
    assert base["CLAUDE_CODE_OAUTH_TOKEN"] == "legacy-setup-token"


def test_tab_launch_env_none_profile_is_plain_launch():
    out = io.StringIO()
    assert L._tab_launch_env(None, out) is None
    assert out.getvalue() == ""


def test_tab_launch_env_builds_profile_and_states_identity(tmp_path):
    out = io.StringIO()
    profile = tmp_path / "p" / "work"

    env = L._tab_launch_env(
        L.TabProfile("work"), out, ensure_fn=lambda name: profile
    )

    assert env["CLAUDE_CONFIG_DIR"] == str(profile)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    text = out.getvalue()
    assert "work" in text
    assert "\x1b]0;claude: work\x07" in text


def test_tab_launch_env_profile_failure_fails_closed():
    out = io.StringIO()

    def boom(_name):
        raise OSError("disk full")

    with pytest.raises(L.TabProfileError):
        L._tab_launch_env(L.TabProfile("work"), out, ensure_fn=boom)

    text = out.getvalue()
    assert "aborting" in text.lower()
    assert "machine-global" not in text
    assert "\x1b]0;" not in text


def test_profile_only_auth_command_uses_picker_and_selected_env(monkeypatch, tmp_path):
    selected = L.TabProfile("work")
    profile = tmp_path / "profiles" / "work"
    calls = []
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setattr(
        L,
        "_resolve_tab_profile",
        lambda client, **kwargs: selected,
    )
    monkeypatch.setattr(
        L,
        "_tab_launch_env",
        lambda selection, stream: {"CLAUDE_CONFIG_DIR": str(profile)},
    )
    monkeypatch.setattr(L, "find_real_binary", lambda client: "/real/claude")
    monkeypatch.setattr(
        L.subprocess,
        "run",
        lambda cmd, env=None: calls.append((cmd, env)) or SimpleNamespace(returncode=0),
    )

    assert L._run_claude_profile_command(["auth", "login"], stream=io.StringIO()) == 0
    assert calls == [
        (["/real/claude", "auth", "login"], {"CLAUDE_CONFIG_DIR": str(profile)})
    ]


# --- presentation ------------------------------------------------------------


def test_emit_tab_identity_states_name_and_sets_tab_title():
    out = io.StringIO()
    L._emit_tab_identity(out, "changja00")
    text = out.getvalue()
    assert "changja00" in text
    assert "\x1b]0;claude: changja00\x07" in text
