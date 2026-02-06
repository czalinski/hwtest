"""TCP server exposing a SCPI emulator for external VISA/telnet tools.

Wraps any ``ScpiTransport`` implementation and serves it over TCP, allowing
external tools (PyVISA, telnet, netcat) to interact with an emulator instance.

This is useful for integration testing where external tools need to connect
to an emulated instrument, or for development when real hardware is not
available.

Example:
    Start an emulator server on an ephemeral port::

        from hwtest_bkprecision import make_9115_emulator, EmulatorServer

        emulator = make_9115_emulator()
        server = EmulatorServer(emulator, port=0)
        server.start()

        host, port = server.address
        print(f"Connect via: TCPIP::{host}::{port}::SOCKET")

        # Use PyVISA, telnet, or netcat to connect
        # telnet localhost {port}
        # > *IDN?
        # < B&K Precision,9115,SN000001,V1.00-V1.00

        server.stop()
"""

from __future__ import annotations

import socketserver
import threading
from typing import Any

from hwtest_scpi import ScpiTransport


class _ScpiRequestHandler(socketserver.StreamRequestHandler):
    """Handle one TCP connection, forwarding lines to the emulator.

    Reads lines from the client, passes them to the transport for processing,
    and sends back responses for queries. Each line is treated as a single
    SCPI command or query.

    Attributes:
        server: Reference to the parent _ScpiTcpServer for accessing the transport.
    """

    server: _ScpiTcpServer

    def handle(self) -> None:
        """Process incoming lines from the client connection.

        Reads lines until the connection is closed. For each non-empty line,
        writes it to the transport and, if it contains a '?', reads and sends
        the response back to the client.
        """
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
    """TCPServer subclass that holds a reference to the transport.

    Extends TCPServer to store the ScpiTransport instance so that request
    handlers can access it for processing SCPI commands.

    Attributes:
        allow_reuse_address: Set to True to allow quick server restart.
        transport: The SCPI transport (emulator) to serve.
    """

    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        transport: ScpiTransport,
        **kwargs: Any,
    ) -> None:
        """Initialize the TCP server with a SCPI transport.

        Args:
            server_address: Tuple of (host, port) to bind to.
            transport: The SCPI transport to serve.
            **kwargs: Additional arguments passed to TCPServer.
        """
        self.transport = transport
        super().__init__(server_address, _ScpiRequestHandler, **kwargs)


class EmulatorServer:
    """TCP server wrapping any ``ScpiTransport`` for external access.

    Runs a TCP server in a background daemon thread, allowing external tools
    (PyVISA, telnet, netcat) to connect and interact with an emulator. The
    server handles one client connection at a time.

    Attributes:
        _server: The underlying TCP server instance.
        _thread: Background thread running the server, or None if not started.

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
        """Start serving in a daemon thread.

        The server runs in a background daemon thread, accepting connections
        and processing SCPI commands. The thread is marked as a daemon so
        it will be terminated when the main program exits.
        """
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Shut down the server and wait for the thread to exit.

        Signals the server to stop accepting connections, waits for the
        background thread to terminate, and closes the server socket.
        """
        self._server.shutdown()
        if self._thread is not None:
            self._thread.join()
        self._server.server_close()

    @property
    def address(self) -> tuple[str, int]:
        """Return the actual bound ``(host, port)`` address.

        Useful when binding to port 0 to get an ephemeral port assigned
        by the operating system.

        Returns:
            Tuple of (host, port) where the server is listening.
        """
        addr = self._server.server_address
        return (str(addr[0]), int(addr[1]))
