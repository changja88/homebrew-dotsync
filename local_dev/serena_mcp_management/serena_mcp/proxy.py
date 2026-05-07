"""Reverse proxy for shared Serena streamable HTTP MCP servers."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import ParseResult, urlparse, urlunparse
from urllib.request import Request, urlopen


_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


@dataclass(frozen=True, slots=True)
class _Upstream:
    parsed_url: ParseResult

    @classmethod
    def from_url(cls, upstream_url: str) -> _Upstream:
        parsed = urlparse(upstream_url)
        if parsed.scheme != "http":
            raise ValueError("upstream URL must use http")
        if not parsed.hostname:
            raise ValueError("upstream URL must include a hostname")
        if parsed.port is None:
            raise ValueError("upstream URL must include a port")
        return cls(parsed)

    @property
    def url(self) -> str:
        path = self.parsed_url.path or "/"
        return urlunparse((
            self.parsed_url.scheme,
            self.parsed_url.netloc,
            path,
            "",
            self.parsed_url.query,
            "",
        ))


class _ReadableResponse(Protocol):
    def read(self, size: int = -1) -> bytes:
        ...


def handler_for(upstream_url: str) -> type[BaseHTTPRequestHandler]:
    """Return an HTTP handler class that proxies safe MCP methods upstream."""

    upstream = _Upstream.from_url(upstream_url)

    class SerenaProxyHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self._forward("POST", body)

        def do_GET(self) -> None:
            self._forward("GET", None)

        def do_DELETE(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _forward(self, method: str, body: bytes | None) -> None:
            request = Request(
                upstream.url,
                data=body,
                headers=dict(_forwardable_headers(self.headers.items())),
                method=method,
            )
            try:
                with urlopen(request, timeout=30) as response:
                    self._send_upstream_response(response.status, response.headers.items(), response)
            except HTTPError as exc:
                self._send_upstream_response(exc.code, exc.headers.items(), exc)
            except URLError:
                self.send_error(502, "Bad Gateway")

        def _send_upstream_response(
            self,
            status: int,
            headers: Iterable[tuple[str, str]],
            response: _ReadableResponse,
        ) -> None:
            self.send_response(status)
            for name, value in _forwardable_headers(headers):
                self.send_header(name, value)
            self.end_headers()
            while chunk := _read_response_chunk(response):
                self.wfile.write(chunk)
                self.wfile.flush()

    return SerenaProxyHandler


def serve_forever(host: str, port: int, upstream_url: str) -> None:
    """Serve the Serena MCP proxy until the process is interrupted."""

    server = ThreadingHTTPServer((host, port), handler_for(upstream_url))
    with server:
        server.serve_forever()


def main() -> int:
    """Run the Serena MCP proxy command-line interface."""

    parser = argparse.ArgumentParser(description="Proxy Serena streamable HTTP MCP safely.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--upstream-url", required=True)
    parser.add_argument("--log-path")
    args = parser.parse_args()

    log_handle = None
    if args.log_path:
        log_path = Path(args.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("a")

    try:
        serve_forever(args.host, args.port, args.upstream_url)
    except KeyboardInterrupt:
        return 0
    except ValueError as exc:
        print(str(exc), file=log_handle or sys.stderr)
        return 2
    finally:
        if log_handle is not None:
            log_handle.close()
    return 0


def _forwardable_headers(headers: Iterable[tuple[str, str]]) -> Iterable[tuple[str, str]]:
    for name, value in headers:
        if name.lower() not in _HOP_BY_HOP_HEADERS:
            yield name, value


def _read_response_chunk(response: _ReadableResponse) -> bytes:
    if hasattr(response, "read1"):
        return response.read1(64 * 1024)
    return response.read(64 * 1024)


if __name__ == "__main__":
    raise SystemExit(main())
