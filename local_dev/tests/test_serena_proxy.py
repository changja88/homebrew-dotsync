import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

from local_dev.serena_mcp_management.serena_mcp.proxy import handler_for


class UpstreamHandler(BaseHTTPRequestHandler):
    events: list[tuple[str, str, bytes]] = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.__class__.events.append(("POST", self.path, body))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def do_GET(self):
        self.__class__.events.append(("GET", self.path, b""))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b"event: message\ndata: ok\n\n")

    def do_DELETE(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.__class__.events.append(("DELETE", self.path, body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        return


def _start_upstream():
    UpstreamHandler.events = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _start_proxy(upstream_url: str):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(upstream_url))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_proxy_forwards_post_to_upstream():
    upstream = _start_upstream()
    upstream_url = f"http://127.0.0.1:{upstream.server_port}/mcp"
    proxy = _start_proxy(upstream_url)
    proxy_url = f"http://127.0.0.1:{proxy.server_port}/mcp"

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
    proxy.shutdown()
    upstream.shutdown()


def test_proxy_forwards_get_to_upstream():
    upstream = _start_upstream()
    upstream_url = f"http://127.0.0.1:{upstream.server_port}/mcp"
    proxy = _start_proxy(upstream_url)
    proxy_url = f"http://127.0.0.1:{proxy.server_port}/mcp"

    with urlopen(proxy_url, timeout=2) as response:
        assert response.status == 200
        assert response.read() == b"event: message\ndata: ok\n\n"

    assert UpstreamHandler.events == [("GET", "/mcp", b"")]
    proxy.shutdown()
    upstream.shutdown()


def test_proxy_suppresses_delete_without_touching_upstream():
    upstream = _start_upstream()
    upstream_url = f"http://127.0.0.1:{upstream.server_port}/mcp"
    proxy = _start_proxy(upstream_url)
    proxy_url = f"http://127.0.0.1:{proxy.server_port}/mcp"

    request = Request(proxy_url, method="DELETE")

    with urlopen(request, timeout=2) as response:
        assert response.status == 200
        assert response.read() == b""

    assert UpstreamHandler.events == []
    proxy.shutdown()
    upstream.shutdown()
