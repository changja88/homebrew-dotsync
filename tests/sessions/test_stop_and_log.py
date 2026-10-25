import json
import os
import signal
import stat

from dotsync import cli_sessions
from dotsync.sessions import log, roots
from dotsync.sessions.stop import StopSignal


def test_stop_signal_sets_requested():
    before = signal.getsignal(signal.SIGTERM)
    with StopSignal() as stop:
        assert stop.requested is False
        os.kill(os.getpid(), signal.SIGTERM)
        assert stop.requested is True
    assert signal.getsignal(signal.SIGTERM) is before


def test_stop_signal_is_a_noop_off_the_main_thread():
    import threading

    seen = []

    def work():
        with StopSignal() as stop:
            seen.append(stop.requested)

    t = threading.Thread(target=work)
    t.start()
    t.join()
    assert seen == [False]


def test_log_write_shape_and_modes():
    path = log.write("delete claude", "claude", "2026-10-26T01:02:03Z", {"deleted": ["a"]})

    assert path.parent == roots.log_dir()
    assert path.name.startswith("sessions-") and path.name.endswith(f"-{os.getpid()}.json")
    doc = json.loads(path.read_text())
    assert list(doc) == ["command", "tool", "started_at", "finished_at", "result"]
    assert doc["command"] == "delete claude"
    assert doc["tool"] == "claude"
    assert doc["started_at"] == "2026-10-26T01:02:03Z"
    assert doc["result"] == {"deleted": ["a"]}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_progress_line_goes_to_stderr(capsys):
    cli_sessions.progress(3, 10)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "dotsync-progress 3/10\n"
