from dotsync import ui


def test_step_outputs_cyan_arrow():
    out = ui.format_step("동기화 시작")
    assert "▶" in out
    assert "동기화 시작" in out


def test_ok_outputs_green_check():
    out = ui.format_ok("settings.json")
    assert "✓" in out
    assert "settings.json" in out


def test_warn_outputs_yellow():
    out = ui.format_warn("BTT 미실행")
    assert "BTT 미실행" in out


def test_error_outputs_red_x():
    out = ui.format_error("실패")
    assert "✗" in out
    assert "실패" in out


def test_done_outputs_green_check():
    out = ui.format_done("완료")
    assert "✔" in out


def test_no_color_disables_ansi(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    out = ui.format_step("test")
    assert "\033[" not in out


def test_format_banner_includes_title_and_box():
    out = ui.format_banner("dotsync to")
    assert "dotsync to" in out
    assert "╭" in out and "╰" in out
    assert "│" in out


def test_format_banner_with_subtitle():
    out = ui.format_banner("dotsync to", "4 apps · /Users/x/cfg")
    assert "4 apps" in out
    assert "/Users/x/cfg" in out


def test_format_section_with_progress():
    out = ui.format_section("claude", index=1, total=4, sub="claude code")
    assert "[1/4]" in out
    assert "claude" in out
    assert "claude code" in out


def test_format_section_without_progress():
    out = ui.format_section("zsh")
    assert "zsh" in out
    assert "[" not in out


def test_format_dim_includes_message():
    out = ui.format_dim("backup → /tmp/x")
    assert "backup → /tmp/x" in out


def test_format_kv_outputs_key_and_value():
    out = ui.format_kv("apps", "zsh, claude")
    assert "apps" in out
    assert "zsh, claude" in out


def test_format_summary_shows_count_and_duration():
    out = ui.format_summary(ok=4, warn=0, error=0, duration_ms=1400)
    assert "4" in out
    assert "1.4s" in out
    assert "╭" in out and "╰" in out


def test_format_summary_lists_synced_apps(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    out = ui.format_summary(
        ok=3, warn=0, error=0, duration_ms=2300,
        synced=["claude", "ghostty", "zsh"],
    )
    assert "synced" in out
    assert "claude" in out and "ghostty" in out and "zsh" in out


def test_format_summary_fits_all_supported_apps(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    out = ui.format_summary(
        ok=5, warn=0, error=0, duration_ms=2300,
        synced=["claude", "codex", "ghostty", "bettertouchtool", "zsh"],
    )
    lines = out.splitlines()
    assert max(len(line) for line in lines) == len(lines[0])


def test_format_summary_separates_applied_and_unchanged(monkeypatch):
    """For `dotsync to`, the summary distinguishes apps that actually
    changed (applied) from apps that were already in sync (unchanged)."""
    monkeypatch.setenv("NO_COLOR", "1")
    out = ui.format_summary(
        ok=4, warn=0, error=0, duration_ms=3100,
        applied=["ghostty", "bettertouchtool"],
        unchanged=["claude", "zsh"],
    )
    assert "applied" in out
    assert "ghostty" in out and "bettertouchtool" in out
    assert "unchanged" in out
    assert "claude" in out and "zsh" in out


def test_format_summary_lists_failed_apps(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    out = ui.format_summary(
        ok=2, warn=0, error=1, duration_ms=1100,
        synced=["claude", "zsh"],
        failed=["bettertouchtool"],
    )
    assert "failed" in out
    assert "bettertouchtool" in out


def test_format_divider_with_label():
    out = ui.format_divider("restore")
    assert "restore" in out
    assert "─" in out


def test_format_divider_without_label():
    out = ui.format_divider()
    assert "─" in out


def test_format_ask_includes_question_and_pointer():
    out = ui.format_ask("sync folder")
    assert "sync folder" in out
    assert "?" in out
    assert "›" in out


def test_format_ask_with_default_renders_brackets():
    out = ui.format_ask("sync folder", "/tmp/default")
    assert "/tmp/default" in out
    assert "[" in out and "]" in out


def test_format_ask_warn_accent_uses_yellow(monkeypatch):
    """Destructive prompts (e.g. apply changes) use a yellow accent so
    they read as a warning, not a routine question."""
    monkeypatch.setattr("dotsync.ui._color_enabled", lambda: True)
    out = ui.format_ask("Apply these changes?", default="y/N", accent="warn")
    assert "Apply these changes?" in out
    assert ui.YELLOW in out


def test_format_ask_default_accent_uses_primary(monkeypatch):
    monkeypatch.setattr("dotsync.ui._color_enabled", lambda: True)
    out = ui.format_ask("sync folder", default="x")
    assert ui.PRIMARY in out
    assert ui.YELLOW not in out


def test_format_status_line_clean(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    from dotsync.ui import format_status_line
    line = format_status_line("zsh", state="clean", details="", direction="")
    assert "✓" in line
    assert "zsh" in line
    assert "clean" in line


def test_format_status_line_dirty_with_direction(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    from dotsync.ui import format_status_line
    line = format_status_line("zsh", state="dirty", details=".zshrc", direction="local-newer")
    assert "⚠" in line
    assert "dirty" in line
    assert ".zshrc" in line
    assert "local-newer" in line  # hint visible in NO_COLOR


def test_format_status_line_missing(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    from dotsync.ui import format_status_line
    line = format_status_line("ghostty", state="missing", details="config", direction="")
    assert "✗" in line
    assert "missing" in line


def test_format_status_line_unknown(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    from dotsync.ui import format_status_line
    line = format_status_line("bettertouchtool", state="unknown", details="BTT not running", direction="")
    assert "·" in line or "?" in line  # any dim marker
    assert "unknown" in line


def test_format_plan_change_shows_kind_label_and_details(monkeypatch):
    from dotsync.plan import Change
    from dotsync import ui

    monkeypatch.setenv("NO_COLOR", "1")

    out = ui.format_plan_change(Change("rules/", "update", details="1 create, 1 remove"))

    assert "update" in out
    assert "rules/" in out
    assert "1 create, 1 remove" in out


def test_format_plan_change_shows_missing_source_as_error(monkeypatch):
    from dotsync.plan import Change
    from dotsync import ui

    monkeypatch.setenv("NO_COLOR", "1")

    out = ui.format_plan_change(Change("config.toml", "missing-source"))

    assert ui.GLYPH_ERROR in out
    assert "missing-source" in out
    assert "config.toml" in out


def test_format_summary_lists_changed_apps(monkeypatch):
    from dotsync import ui

    monkeypatch.setenv("NO_COLOR", "1")

    out = ui.format_summary(
        ok=2,
        error=0,
        duration_ms=100,
        changed=["codex"],
        unchanged=["zsh"],
    )

    assert "changed" in out
    assert "codex" in out
    assert "unchanged" in out
    assert "zsh" in out
