"""Reverse proxy for shared Serena streamable HTTP MCP servers."""
from __future__ import annotations

import argparse
import http.client
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import ParseResult, urlparse, urlunparse


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

    @property
    def request_target(self) -> str:
        path = self.parsed_url.path or "/"
        return urlunparse(("", "", path, "", self.parsed_url.query, ""))


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
            connection = None
            try:
                connection = _open_upstream_connection(upstream)
                _send_upstream_request(
                    connection,
                    upstream,
                    method,
                    body,
                    self.headers.items(),
                )
                response = connection.getresponse()
                self._send_upstream_response(
                    response.status,
                    response.getheaders(),
                    response,
                )
            except (OSError, http.client.HTTPException):
                self.send_error(502, "Bad Gateway")
            finally:
                if connection is not None:
                    connection.close()

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


def _open_upstream_connection(upstream: _Upstream) -> http.client.HTTPConnection:
    return http.client.HTTPConnection(
        upstream.parsed_url.hostname,
        upstream.parsed_url.port,
        timeout=None,
    )


def _send_upstream_request(
    connection: http.client.HTTPConnection,
    upstream: _Upstream,
    method: str,
    body: bytes | None,
    headers: Iterable[tuple[str, str]],
) -> None:
    connection.putrequest(
        method,
        upstream.request_target,
        skip_host=True,
        skip_accept_encoding=True,
    )
    for name, value in _headers_for_upstream(headers):
        connection.putheader(name, value)
    connection.endheaders(body)


def _headers_for_upstream(
    headers: Iterable[tuple[str, str]],
) -> Iterable[tuple[str, str]]:
    header_items = tuple(headers)
    blocked_headers = _headers_listed_by_connection(header_items)
    for name, value in header_items:
        if name.lower() not in blocked_headers and name.lower() != "host":
            yield name, value


def _forwardable_headers(
    headers: Iterable[tuple[str, str]],
) -> Iterable[tuple[str, str]]:
    header_items = tuple(headers)
    blocked_headers = _headers_listed_by_connection(header_items)
    for name, value in header_items:
        if name.lower() not in blocked_headers:
            yield name, value


def _headers_listed_by_connection(headers: Iterable[tuple[str, str]]) -> set[str]:
    blocked_headers = set(_HOP_BY_HOP_HEADERS)
    for name, value in headers:
        if name.lower() == "connection":
            blocked_headers.update(
                header_name.strip().lower()
                for header_name in value.split(",")
                if header_name.strip()
            )
    return blocked_headers


def _read_response_chunk(response: _ReadableResponse) -> bytes:
    if hasattr(response, "read1"):
        return response.read1(64 * 1024)
    return response.read(64 * 1024)


if __name__ == "__main__":
    raise SystemExit(main())
