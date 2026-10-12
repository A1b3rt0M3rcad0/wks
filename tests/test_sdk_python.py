"""SDK conformance over HTTP with disconnects, cursors and streamed integrity."""

import hashlib
import io
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from wks_sdk import Client, WksError


@contextmanager
def server(handler):
    class Adapter(BaseHTTPRequestHandler):
        do_GET = do_POST = do_PUT = handler

        def log_message(self, *args):
            pass

    endpoint = ThreadingHTTPServer(("127.0.0.1", 0), Adapter)
    thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
    thread.start()
    try:
        yield Client(f"http://127.0.0.1:{endpoint.server_port}", "private-secret")
    finally:
        endpoint.shutdown()
        endpoint.server_close()
        thread.join()


def respond(request, value, code=200):
    raw = json.dumps(value).encode()
    request.send_response(code)
    request.send_header("Content-Length", str(len(raw)))
    request.end_headers()
    request.wfile.write(raw)


def test_sdk_unknown_write_exposes_operation_key_without_secret_or_retry():
    calls = []

    def handler(request):
        calls.append(request.path)
        request.connection.close()

    with server(handler) as client:
        with pytest.raises(WksError) as caught:
            client.create_text("namespace", "title", "content", key="receipt-key")
        assert caught.value.write_outcome == "unknown"
        assert caught.value.operation_key == "receipt-key"
        assert len(calls) == 1
        assert "private-secret" not in str(caught.value) + repr(client)


def test_sdk_paginates_and_checks_private_headers():
    calls = []

    def handler(request):
        assert request.headers["Authorization"] == "Bearer private-secret"
        assert request.headers["X-WKS-Grant"] == "delegation"
        calls.append(request.path)
        respond(
            request,
            {
                "blocks": [{"id": str(len(calls))}],
                "next_cursor": "next+/=" if len(calls) == 1 else None,
            },
        )

    with server(handler) as client:
        client.grant = "delegation"
        pages = list(client.read("representation"))
        assert len(pages) == 2
        assert "cursor=next%2B%2F%3D" in calls[1]


def test_sdk_download_rejects_corruption_and_redirects():
    payload = b"full original content"

    def handler(request):
        if request.path == "/redirect":
            request.send_response(302)
            request.send_header("Location", "https://example.invalid")
            request.end_headers()
            return
        request.send_response(200)
        request.send_header("Content-Length", str(len(payload)))
        request.end_headers()
        request.wfile.write(payload)

    with server(handler) as client:
        destination = io.BytesIO()
        assert client.download(
            "/original", destination, sha256=hashlib.sha256(payload).hexdigest()
        ) == len(payload)
        assert destination.getvalue() == payload
        with pytest.raises(WksError):
            client.download("/original", io.BytesIO(), sha256="0" * 64)
        with pytest.raises(WksError):
            client.download("/redirect", io.BytesIO())


def test_sdk_partial_success_response_keeps_unknown_write_outcome():
    def handler(request):
        request.send_response(200)
        request.send_header("Content-Length", "500")
        request.end_headers()
        request.wfile.write(b'{"source_id":"partial')
        request.close_connection = True

    with server(handler) as client:
        with pytest.raises(WksError) as caught:
            client.create_text("namespace", "title", "text", key="recover-by-receipt")
        assert caught.value.write_outcome == "unknown"
        assert caught.value.operation_key == "recover-by-receipt"
