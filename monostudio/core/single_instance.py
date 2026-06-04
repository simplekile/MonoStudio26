"""Ensure a single MONOS instance; second launch signals the running instance to raise."""

from __future__ import annotations

import logging
import sys
from typing import Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_log = logging.getLogger("monostudio.single_instance")

SERVER_NAME = "MonoStudio26.SingleInstance"
_MSG_RAISE = b"raise"
_CONNECT_MS = 800
_WRITE_MS = 800


class SingleInstanceGuard(QObject):
    """
    First instance owns QLocalServer; later instances connect, send raise, and exit.
    """

    def __init__(self, on_raise: Callable[[], None] | None = None) -> None:
        super().__init__()
        self._on_raise = on_raise
        self._server: QLocalServer | None = None
        self._is_primary = False
        self._pending_raise = False

    def set_on_raise(self, on_raise: Callable[[], None] | None) -> None:
        self._on_raise = on_raise
        if on_raise is not None and self._pending_raise:
            self._pending_raise = False
            QTimer.singleShot(0, on_raise)

    @property
    def is_primary(self) -> bool:
        return self._is_primary

    def try_acquire(self) -> bool:
        """Return True if this process should continue as the primary instance."""
        if self._signal_existing():
            return False

        QLocalServer.removeServer(SERVER_NAME)
        server = QLocalServer(self)
        if server.listen(SERVER_NAME):
            server.newConnection.connect(self._on_new_connection)
            self._server = server
            self._is_primary = True
            return True

        if self._signal_existing():
            return False

        _log.warning("SingleInstance listen failed; continuing without guard")
        self._is_primary = True
        return True

    def _signal_existing(self) -> bool:
        """Connect to running instance and ask it to raise. Return True if signaled."""
        sock = QLocalSocket(self)
        sock.connectToServer(SERVER_NAME)
        if not sock.waitForConnected(_CONNECT_MS):
            sock.abort()
            return False
        sock.write(_MSG_RAISE)
        sock.flush()
        sock.waitForBytesWritten(_WRITE_MS)
        sock.disconnectFromServer()
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
        if _MSG_RAISE not in data:
            return
        if self._on_raise is not None:
            QTimer.singleShot(0, self._on_raise)
        else:
            self._pending_raise = True


def acquire_single_instance(on_raise: Callable[[], None] | None = None) -> SingleInstanceGuard | None:
    """
    Attempt single-instance lock.
    Returns None if this process should exit (secondary instance signaled primary).
    """
    guard = SingleInstanceGuard(on_raise=on_raise)
    if guard.try_acquire():
        return guard
    return None
