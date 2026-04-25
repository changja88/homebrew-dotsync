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
