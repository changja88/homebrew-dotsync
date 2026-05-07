import http.client
import json
import queue
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

from local_dev.serena_mcp_management.serena_mcp import proxy


class UpstreamHandler(BaseHTTPRequestHandler):
    events: list[tuple[str, str, bytes]] = []
    headers_seen: list[dict[str, str]] = []
    release_get: threading.Event | None = None

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.__class__.events.append(("POST", self.path, body))
        self.__class__.headers_seen.append(dict(self.headers.items()))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def do_GET(self):
        self.__class__.events.append(("GET", self.path, b""))
        self.__class__.headers_seen.append(dict(self.headers.items()))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b"event: message\ndata: ok\n\n")
        self.wfile.flush()
        if self.__class__.release_get is not None:
            self.__class__.release_get.wait(timeout=5)

    def do_DELETE(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.__class__.events.append(("DELETE", self.path, body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        return


def _start_upstream():
    UpstreamHandler.events = []
    UpstreamHandler.headers_seen = []
    UpstreamHandler.release_get = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _start_proxy(upstream_url: str):
    server = ThreadingHTTPServer(("127.0.0.1", 0), proxy.handler_for(upstream_url))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@contextmanager
def running_upstream():
    server, thread = _start_upstream()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def running_proxy(upstream_url: str):
    server, thread = _start_proxy(upstream_url)
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _read_queue_result(
    results: queue.Queue[bytes | BaseException],
    timeout: float,
) -> bytes:
    result = results.get(timeout=timeout)
    if isinstance(result, BaseException):
        raise result
    return result


def test_proxy_forwards_post_to_upstream():
    with running_upstream() as upstream:
        upstream_url = f"http://127.0.0.1:{upstream.server_port}/mcp"
        with running_proxy(upstream_url) as proxy_server:
            proxy_url = f"http://127.0.0.1:{proxy_server.server_port}/mcp"

            request = Request(
                proxy_url,
                data=json.dumps({"jsonrpc": "2.0", "id": 1}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urlopen(request, timeout=2) as response:
                assert response.status == 200
                assert response.read() == b'{"ok": true}'

            assert UpstreamHandler.events == [
                ("POST", "/mcp", b'{"jsonrpc": "2.0", "id": 1}')
            ]


def test_proxy_filters_hop_by_hop_headers_named_by_connection():
    with running_upstream() as upstream:
        upstream_url = f"http://127.0.0.1:{upstream.server_port}/mcp"
        with running_proxy(upstream_url) as proxy_server:
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                proxy_server.server_port,
                timeout=2,
            )
            connection.putrequest("POST", "/mcp", skip_host=True)
            connection.putheader("Connection", "X-Debug-Hop")
            connection.putheader("Host", "client-supplied.example")
            connection.putheader("X-Debug-Hop", "secret")
            connection.putheader("Content-Length", "2")

            try:
                connection.endheaders(b"{}")
                response = connection.getresponse()
                assert response.status == 200
            finally:
                connection.close()

            forwarded_headers = {
                name.lower(): value
                for name, value in UpstreamHandler.headers_seen[-1].items()
            }
            assert "connection" not in forwarded_headers
            assert "x-debug-hop" not in forwarded_headers
            assert forwarded_headers["host"] == f"127.0.0.1:{upstream.server_port}"


def test_proxy_opens_upstream_connection_without_read_timeout(monkeypatch):
    timeouts: list[object] = []

    class FakeHTTPConnection:
        def __init__(self, host, port, timeout=None):
            timeouts.append(timeout)

    monkeypatch.setattr(proxy.http.client, "HTTPConnection", FakeHTTPConnection)

    proxy._open_upstream_connection(
        proxy._Upstream.from_url("http://127.0.0.1:12345/mcp")
    )

    assert timeouts == [None]


def test_proxy_does_not_send_error_after_response_streaming_starts(monkeypatch):
    handler_class = proxy.handler_for("http://127.0.0.1:12345/mcp")
    handler = object.__new__(handler_class)
    events: list[tuple[str, object]] = []
    send_error_calls: list[tuple[int, str]] = []

    class StreamingFailureResponse:
        status = 200

        def getheaders(self):
            return [("Content-Type", "text/event-stream")]

        def read(self, size=-1):
            raise OSError("stream read failed after headers")

    class FakeConnection:
        def putrequest(self, *args, **kwargs):
            return None

        def putheader(self, *args):
            return None

        def endheaders(self, body=None):
            return None

        def getresponse(self):
            return StreamingFailureResponse()

        def close(self):
            events.append(("close", None))

    handler.headers = {}
    handler.send_response = lambda status: events.append(("send_response", status))
    handler.send_header = lambda name, value: events.append(("send_header", name))
    handler.end_headers = lambda: events.append(("end_headers", None))
    handler.send_error = lambda status, message: send_error_calls.append(
        (status, message)
    )
    monkeypatch.setattr(
        proxy,
        "_open_upstream_connection",
        lambda upstream: FakeConnection(),
    )

    handler._forward("GET", None)

    assert ("end_headers", None) in events
    assert send_error_calls == []
    assert ("close", None) in events


def test_proxy_forwards_get_to_upstream():
    with running_upstream() as upstream:
        upstream_url = f"http://127.0.0.1:{upstream.server_port}/mcp"
        with running_proxy(upstream_url) as proxy_server:
            proxy_url = f"http://127.0.0.1:{proxy_server.server_port}/mcp"

            with urlopen(proxy_url, timeout=2) as response:
                assert response.status == 200
                assert response.read() == b"event: message\ndata: ok\n\n"

            assert UpstreamHandler.events == [("GET", "/mcp", b"")]


def test_proxy_streams_get_bytes_before_upstream_closes():
    first_event = b"event: message\ndata: ok\n\n"
    release_get = threading.Event()
    with running_upstream() as upstream:
        UpstreamHandler.release_get = release_get
        upstream_url = f"http://127.0.0.1:{upstream.server_port}/mcp"
        with running_proxy(upstream_url) as proxy_server:
            proxy_url = f"http://127.0.0.1:{proxy_server.server_port}/mcp"
            results: queue.Queue[bytes | BaseException] = queue.Queue()

            def read_first_event():
                try:
                    with urlopen(proxy_url, timeout=2) as response:
                        results.put(response.read(len(first_event)))
                except BaseException as exc:
                    results.put(exc)

            client_thread = threading.Thread(target=read_first_event, daemon=True)
            client_thread.start()

            try:
                assert _read_queue_result(results, timeout=0.5) == first_event
            finally:
                release_get.set()
                client_thread.join(timeout=2)

            assert UpstreamHandler.events == [("GET", "/mcp", b"")]


def test_proxy_suppresses_delete_without_touching_upstream():
    with running_upstream() as upstream:
        upstream_url = f"http://127.0.0.1:{upstream.server_port}/mcp"
        with running_proxy(upstream_url) as proxy_server:
            proxy_url = f"http://127.0.0.1:{proxy_server.server_port}/mcp"

            request = Request(proxy_url, method="DELETE")

            with urlopen(request, timeout=2) as response:
                assert response.status == 200
                assert response.read() == b""

            assert UpstreamHandler.events == []
