"""SIGTERM as a request to stop at the next safe point, not an interruption."""

from __future__ import annotations

import signal
import threading


class StopSignal:
    """While entered, SIGTERM sets `requested` instead of killing the process.
    Handlers can only be installed from the main thread; elsewhere this is a
    no-op and `requested` stays False."""

    def __init__(self) -> None:
        self.requested = False
        self._previous = None
        self._installed = False

    def __enter__(self) -> "StopSignal":
        if threading.current_thread() is threading.main_thread():
            self._previous = signal.signal(signal.SIGTERM, self._on_term)
            self._installed = True
        return self

    def __exit__(self, *exc_info) -> None:
        if self._installed:
            signal.signal(signal.SIGTERM, self._previous)
            self._installed = False

    def _on_term(self, _signum, _frame) -> None:
        self.requested = True
