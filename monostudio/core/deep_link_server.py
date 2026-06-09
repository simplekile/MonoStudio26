"""Local HTTP bridge so Discord link buttons (https/http only) can open MONOS."""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from monostudio.core.deep_link import build_assign_deep_link

_log = logging.getLogger("monostudio.deep_link_server")

DEFAULT_PORT = 39247
_server: HTTPServer | None = None
_server_thread: threading.Thread | None = None
_on_assign_link: Callable[[str], None] | None = None


def assign_http_url(inbox_id: str, *, action: str = "open", port: int = DEFAULT_PORT) -> str:
    iid = (inbox_id or "").strip()
    act = (action or "open").strip().lower()
    if act not in ("open", "confirm"):
        act = "open"
    return f"http://localhost:{int(port)}/assign?inbox={iid}&action={act}"


class _AssignBridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        _log.debug("DeepLinkServer " + format, *args)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in ("/assign", "/assign/"):
            self.send_error(404)
            return
        qs = parse_qs(parsed.query, keep_blank_values=False)
        inbox = (qs.get("inbox") or qs.get("inbox_id") or [""])[0].strip()
        action = (qs.get("action") or ["open"])[0].strip().lower() or "open"
        if not inbox:
            self.send_error(400, "Missing inbox id")
            return
        deep_url = build_assign_deep_link(inbox, action=action)
        cb = _on_assign_link
        if cb is not None:
            try:
                cb(deep_url)
            except Exception:
                _log.debug("Deep link callback failed", exc_info=True)
        html = f"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="0;url={deep_url}">
<title>MONOS</title>
<style>body{{font-family:Inter,sans-serif;background:#121214;color:#fafafa;padding:2rem}}</style>
</head><body>
<p>Đang mở MONOS… / Opening MONOS…</p>
<p><a href="{deep_url}" style="color:#60a5fa">Mở MONOS / Open MONOS</a></p>
</body></html>"""
        payload = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_deep_link_server(
    on_assign_link: Callable[[str], None],
    *,
    port: int = DEFAULT_PORT,
) -> int | None:
    """Start localhost bridge; returns bound port or None if unavailable."""
    global _server, _server_thread, _on_assign_link
    stop_deep_link_server()
    _on_assign_link = on_assign_link
    try:
        httpd = HTTPServer(("127.0.0.1", int(port)), _AssignBridgeHandler)
    except OSError:
        _log.debug("Deep link server could not bind port %s", port)
        _on_assign_link = None
        return None
    _server = httpd
    _server_thread = threading.Thread(
        target=httpd.serve_forever,
        name="monos-deep-link",
        daemon=True,
    )
    _server_thread.start()
    bound = httpd.server_address[1]
    _log.debug("Deep link server listening on 127.0.0.1:%s", bound)
    return bound


def stop_deep_link_server() -> None:
    global _server, _server_thread, _on_assign_link
    if _server is not None:
        try:
            _server.shutdown()
        except OSError:
            pass
        _server = None
    _server_thread = None
    _on_assign_link = None


def active_deep_link_port() -> int | None:
    if _server is None:
        return None
    try:
        return int(_server.server_address[1])
    except (TypeError, ValueError, IndexError):
        return None
