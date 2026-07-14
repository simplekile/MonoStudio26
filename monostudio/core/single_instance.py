"""Ensure a single MONOS instance; second launch signals the running instance to raise."""

from __future__ import annotations

import logging
import sys
import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_log = logging.getLogger("monostudio.single_instance")

_SERVER_NAME = "MonoStudio26.SingleInstance"
_MSG_RAISE = b"raise"
_MSG_LINK_PREFIX = b"link:"
_CONNECT_MS = 800
_WRITE_MS = 800
# Keep secondary alive briefly so AllowSetForegroundWindow still applies while primary raises.
_HANDOFF_HOLD_S = 0.35


class SingleInstanceGuard(QObject):
    """
    First instance owns QLocalServer; later instances connect, send raise, and exit.
    """

    def __init__(self, on_raise: Callable[[], None] | None = None) -> None:
        super().__init__()
        self._on_raise = on_raise
        self._on_deep_link: Callable[[str], None] | None = None
        self._server: QLocalServer | None = None
        self._is_primary = False
        self._pending_raise = False
        self._pending_deep_link: str | None = None

    def set_on_raise(self, on_raise: Callable[[], None] | None) -> None:
        self._on_raise = on_raise
        if on_raise is not None and self._pending_raise:
            self._pending_raise = False
            QTimer.singleShot(0, on_raise)

    @property
    def is_primary(self) -> bool:
        return self._is_primary

    def set_on_deep_link(self, on_deep_link: Callable[[str], None] | None) -> None:
        self._on_deep_link = on_deep_link
        if on_deep_link is not None and self._pending_deep_link:
            url = self._pending_deep_link
            self._pending_deep_link = None
            QTimer.singleShot(0, lambda: on_deep_link(url))

    def try_acquire(self, *, deep_link: str | None = None) -> bool:
        """Return True if this process should continue as the primary instance."""
        if self._signal_existing(deep_link):
            return False

        QLocalServer.removeServer(_SERVER_NAME)
        server = QLocalServer(self)
        if server.listen(_SERVER_NAME):
            server.newConnection.connect(self._on_new_connection)
            self._server = server
            self._is_primary = True
            return True

        if self._signal_existing(deep_link):
            return False

        _log.warning("SingleInstance listen failed; continuing without guard")
        self._is_primary = True
        return True

    def _signal_existing(self, deep_link: str | None = None) -> bool:
        """Connect to running instance and forward raise/deep link. Return True if signaled."""
        # Protocol-handler / second launch often owns focus briefly — grant FG to primary.
        try:
            from monostudio.core.window_focus import allow_set_foreground_window_any

            allow_set_foreground_window_any()
        except Exception:
            pass
        sock = QLocalSocket(self)
        sock.connectToServer(_SERVER_NAME)
        if not sock.waitForConnected(_CONNECT_MS):
            sock.abort()
            return False
        link = (deep_link or "").strip()
        if link:
            payload = _MSG_LINK_PREFIX + link.encode("utf-8")
        else:
            payload = _MSG_RAISE
        sock.write(payload)
        sock.flush()
        sock.waitForBytesWritten(_WRITE_MS)
        sock.disconnectFromServer()
        try:
            time.sleep(_HANDOFF_HOLD_S)
        except Exception:
            pass
        return True

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            socket.readyRead.connect(lambda s=socket: self._handle_client(s))

    def _handle_client(self, socket: QLocalSocket) -> None:
        try:
            data = bytes(socket.readAll())
        except Exception:
            data = b""
        socket.disconnectFromServer()
        if data.startswith(_MSG_LINK_PREFIX):
            url = data[len(_MSG_LINK_PREFIX) :].decode("utf-8", errors="replace").strip()
            if url and self._on_deep_link is not None:
                QTimer.singleShot(0, lambda u=url: self._on_deep_link(u))
            elif url:
                self._pending_deep_link = url
            return
        if _MSG_RAISE not in data:
            return
        if self._on_raise is not None:
            QTimer.singleShot(0, self._on_raise)
        else:
            self._pending_raise = True


def acquire_single_instance(
    on_raise: Callable[[], None] | None = None,
    *,
    deep_link: str | None = None,
) -> SingleInstanceGuard | None:
    """
    Attempt single-instance lock.
    Returns None if this process should exit (secondary instance signaled primary).
    """
    guard = SingleInstanceGuard(on_raise=on_raise)
    if guard.try_acquire(deep_link=deep_link):
        return guard
    return None
