import threading
import time

from local_dev.serena_mcp_management.ui import SpinnerTicker


def test_spinner_ticker_calls_on_tick_with_increasing_frame():
    frames: list[int] = []
    event = threading.Event()

    def on_tick(frame: int) -> None:
        frames.append(frame)
        if len(frames) >= 3:
            event.set()

    ticker = SpinnerTicker(on_tick=on_tick, interval=0.01)
    ticker.start()
    assert event.wait(timeout=1.0)
    ticker.stop()
    assert frames[:3] == [1, 2, 3]


def test_spinner_ticker_stop_joins_thread():
    ticker = SpinnerTicker(on_tick=lambda _: None, interval=0.01)
    ticker.start()
    time.sleep(0.02)
    ticker.stop()
    assert ticker._thread is not None  # type: ignore[union-attr]
    assert not ticker._thread.is_alive()


def test_spinner_ticker_stop_without_start_is_safe():
    ticker = SpinnerTicker(on_tick=lambda _: None, interval=0.01)
    ticker.stop()  # no exception
