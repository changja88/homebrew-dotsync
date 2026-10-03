from dotsync import claude_usage

FIVE = (12, "2026-10-03T08:29:59.854776+00:00")
WEEK = (3, "2026-10-10T03:59:59.854797+00:00")
PROBE_ARGS = [
    "-p", "--input-format", "stream-json", "--output-format", "stream-json",
    "--verbose", "--strict-mcp-config", "--disable-slash-commands",
]


def test_parse_reads_both_windows(fake_accounts_cli):
    out = claude_usage.parse(fake_accounts_cli.usage_answer(FIVE, WEEK))
    assert out == {
        "status": "ok",
        "five_hour": {"percent": 12, "resets_at": "2026-10-03T08:29:59Z"},
        "seven_day": {"percent": 3, "resets_at": "2026-10-10T03:59:59Z"},
    }


def test_parse_recognizes_a_folder_that_never_logged_in(fake_accounts_cli):
    assert claude_usage.parse(fake_accounts_cli.NOT_LOGGED_IN_ANSWER) == {
        "status": "login_required", "five_hour": None, "seven_day": None,
    }


def test_parse_rounds_fractional_use_and_keeps_a_missing_reset(fake_accounts_cli):
    out = claude_usage.parse(fake_accounts_cli.usage_answer((31.6, None), (99.5, WEEK[1])))
    assert out["five_hour"] == {"percent": 32, "resets_at": None}
    assert out["seven_day"]["percent"] == 100


def test_parse_converts_other_offsets_to_utc(fake_accounts_cli):
    out = claude_usage.parse(fake_accounts_cli.usage_answer((1, "2026-10-04T18:00:00+09:00"), WEEK))
    assert out["five_hour"]["resets_at"] == "2026-10-04T09:00:00Z"


def test_parse_keeps_a_window_claude_leaves_out(fake_accounts_cli):
    out = claude_usage.parse(fake_accounts_cli.usage_answer(None, WEEK))
    assert out["status"] == "ok"
    assert out["five_hour"] is None


def test_parse_skips_noise_before_the_answer(fake_accounts_cli):
    noise = 'warning: something\n{"type":"system","subtype":"init"}\n'
    out = claude_usage.parse(noise + fake_accounts_cli.usage_answer(FIVE, WEEK))
    assert out["status"] == "ok"


def test_parse_without_an_answer_is_none():
    assert claude_usage.parse("") is None
    assert claude_usage.parse("not json\n") is None


def test_parse_logged_in_without_rate_limits_is_an_error():
    line = (
        '{"type":"control_response","response":{"subtype":"success","request_id":"u1",'
        '"response":{"subscription_type":"max","rate_limits_available":false,"rate_limits":null}}}\n'
    )
    assert claude_usage.parse(line) == {
        "status": "error", "error": "usage not available", "five_hour": None, "seven_day": None,
    }


def test_parse_a_refused_request_is_an_error():
    line = '{"type":"control_response","response":{"subtype":"error","request_id":"u1","error":"nope"}}\n'
    assert claude_usage.parse(line)["status"] == "error"


def test_probe_of_a_folder_sets_claude_config_dir(fake_home, fake_accounts_cli):
    folder = fake_home / ".claude-accounts" / "bob"
    folder.mkdir(parents=True)
    fake_accounts_cli.reply_usage("bob", FIVE, WEEK)

    assert claude_usage.probe("/fake/bin/claude", folder, cwd=fake_home)["status"] == "ok"

    assert fake_accounts_cli.probes == ["bob"]
    assert fake_accounts_cli.calls[-1][1:] == PROBE_ARGS


def test_probe_of_the_seat_drops_an_inherited_claude_config_dir(fake_home, fake_accounts_cli, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/somewhere/else")
    fake_accounts_cli.reply_usage("seat", FIVE, WEEK)

    assert claude_usage.probe("/fake/bin/claude", None, cwd=fake_home)["status"] == "ok"
    assert fake_accounts_cli.probes == ["seat"]


def test_probe_that_times_out_is_an_error(fake_home, fake_accounts_cli):
    fake_accounts_cli.reply_timeout("seat")
    assert claude_usage.probe("/fake/bin/claude", None, cwd=fake_home) == {
        "status": "error", "error": "timeout", "five_hour": None, "seven_day": None,
    }


def test_probe_that_crashes_without_an_answer_is_an_error(fake_home, fake_accounts_cli):
    fake_accounts_cli.reply_raw("seat", "Segmentation fault\n", returncode=139)
    out = claude_usage.probe("/fake/bin/claude", None, cwd=fake_home)
    assert out["status"] == "error"
    assert out["error"] == "claude exited with 139"


def test_probe_runs_in_the_folder_it_is_given(fake_home, fake_accounts_cli):
    neutral = fake_home / "neutral"
    neutral.mkdir()
    fake_accounts_cli.reply_usage("seat", FIVE, WEEK)

    claude_usage.probe("/fake/bin/claude", None, cwd=neutral)

    assert fake_accounts_cli.probe_cwds["seat"] == neutral


def test_probe_reads_an_answer_next_to_bytes_that_are_not_utf8(fake_home, fake_accounts_cli):
    raw = b"\xff\xfe noise\n" + fake_accounts_cli.usage_answer(FIVE, WEEK).encode()
    fake_accounts_cli.reply_bytes("seat", raw)
    assert claude_usage.probe("/fake/bin/claude", None, cwd=fake_home)["status"] == "ok"


def test_probe_that_cannot_start_claude_is_an_error(fake_home, fake_accounts_cli):
    fake_accounts_cli.reply_raise("seat", FileNotFoundError(2, "No such file or directory"))
    out = claude_usage.probe("/fake/bin/claude", None, cwd=fake_home)
    assert out["status"] == "error"
    assert "No such file" in out["error"]
