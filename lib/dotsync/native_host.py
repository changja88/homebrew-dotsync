"""Parent-owned native transport for DotSync's loopback web application."""

from __future__ import annotations

import json
import re
import selectors
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, BinaryIO
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from dotsync.web.server import WebApplication


class NativeHostProtocolError(RuntimeError):
    """Raised when native framing or lifetime requirements are invalid."""


def run_ui_server(application: WebApplication, *, poll_interval: float):
    from dotsync.web.server import run_ui_server as start_ui_server

    return start_ui_server(application, poll_interval=poll_interval)


@dataclass(frozen=True)
class NativeHostHandshake:
    schema_version: int
    origin: str
    token: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or not _valid_native_origin(self.origin)
            or type(self.token) is not str
            or re.fullmatch(r"[A-Za-z0-9_-]{43}", self.token) is None
        ):
            raise NativeHostProtocolError("native handshake is invalid")

    def encode_line(self) -> bytes:
        data = json.dumps(
            asdict(self),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii") + b"\n"
        if len(data) > 4096:
            raise NativeHostProtocolError("native handshake exceeds 4096 bytes")
        return data


def _valid_native_origin(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and port is not None
        and 1 <= port <= 65_535
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None
    )


def run_native_host(
    application: WebApplication,
    *,
    control: BinaryIO,
    handshake: BinaryIO,
    poll_interval: float = 0.1,
) -> int:
    if application.idle_shutdown_enabled:
        raise NativeHostProtocolError("native host requires parent-owned lifetime")
    with run_ui_server(application, poll_interval=poll_interval) as server:
        line = NativeHostHandshake(
            schema_version=1,
            origin=server.origin,
            token=application.token,
        ).encode_line()
        handshake.write(line)
        handshake.flush()
        selector = selectors.DefaultSelector()
        try:
            selector.register(control, selectors.EVENT_READ)
            while True:
                if server.wait(timeout=poll_interval):
                    return 1
                for key, _ in selector.select(timeout=0):
                    value = key.fileobj.read(1)
                    return 0 if value == b"" else 2
        finally:
            selector.close()
