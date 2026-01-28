"""TCP server exposing a SCPI emulator for external VISA/telnet tools.

Wraps any ``ScpiTransport`` implementation and serves it over TCP, allowing
external tools (PyVISA, telnet, netcat) to interact with an emulator instance.
"""

from __future__ import annotations

import socketserver
import threading
from typing import Any

from hwtest_scpi import ScpiTransport


class _ScpiRequestHandler(socketserver.StreamRequestHandler):
    """Handle one TCP connection, forwarding lines to the emulator."""

    server: _ScpiTcpServer

    def handle(self) -> None:
        for raw_line in self.rfile:
            line = raw_line.decode("ascii").strip()
            if not line:
                continue
            transport = self.server.transport
            transport.write(line)
            if "?" in line:
                response = transport.read()
                self.wfile.write((response + "\n").encode("ascii"))
                self.wfile.flush()


class _ScpiTcpServer(socketserver.TCPServer):
    """TCPServer subclass that holds a reference to the transport."""

    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        transport: ScpiTransport,
        **kwargs: Any,
    ) -> None:
        self.transport = transport
        super().__init__(server_address, _ScpiRequestHandler, **kwargs)


class EmulatorServer:
    """TCP server wrapping any ``ScpiTransport`` for external access.

    Args:
        transport: The SCPI transport (typically an emulator) to serve.
        host: Bind address (default ``"127.0.0.1"``).
        port: Bind port (default ``5025``). Use ``0`` for an OS-assigned
            ephemeral port.
    """

    def __init__(
        self,
        transport: ScpiTransport,
        host: str = "127.0.0.1",
        port: int = 5025,
    ) -> None:
        self._server = _ScpiTcpServer((host, port), transport)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start serving in a daemon thread."""
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Shut down the server and wait for the thread to exit."""
        self._server.shutdown()
        if self._thread is not None:
            self._thread.join()
        self._server.server_close()

    @property
    def address(self) -> tuple[str, int]:
        """Return the actual bound ``(host, port)`` address."""
        addr = self._server.server_address
        return (str(addr[0]), int(addr[1]))
