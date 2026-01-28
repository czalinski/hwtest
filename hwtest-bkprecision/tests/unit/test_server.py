"""Tests for the EmulatorServer TCP server."""

from __future__ import annotations

import socket

from hwtest_bkprecision.emulator import make_9115_emulator
from hwtest_bkprecision.server import EmulatorServer


def _send_query(sock: socket.socket, query: str) -> str:
    """Send a query over TCP and return the response line."""
    sock.sendall((query + "\n").encode("ascii"))
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data.decode("ascii").strip()


def _send_command(sock: socket.socket, cmd: str) -> None:
    """Send a command over TCP (no response expected)."""
    sock.sendall((cmd + "\n").encode("ascii"))


class TestEmulatorServer:
    """Tests for EmulatorServer TCP serving."""

    def test_query_round_trip(self) -> None:
        emu = make_9115_emulator()
        server = EmulatorServer(emu, port=0)
        server.start()
        try:
            host, port = server.address
            with socket.create_connection((host, port), timeout=5) as sock:
                response = _send_query(sock, "*IDN?")
                assert "9115" in response
                assert "B&K Precision" in response
        finally:
            server.stop()

    def test_command_with_no_response(self) -> None:
        emu = make_9115_emulator()
        server = EmulatorServer(emu, port=0)
        server.start()
        try:
            host, port = server.address
            with socket.create_connection((host, port), timeout=5) as sock:
                _send_command(sock, "VOLT 12.0")
                response = _send_query(sock, "VOLT?")
                assert "12.0000" in response
        finally:
            server.stop()

    def test_multiple_queries(self) -> None:
        emu = make_9115_emulator()
        server = EmulatorServer(emu, port=0)
        server.start()
        try:
            host, port = server.address
            with socket.create_connection((host, port), timeout=5) as sock:
                r1 = _send_query(sock, "*IDN?")
                r2 = _send_query(sock, "*OPC?")
                r3 = _send_query(sock, "VOLT?")
                assert "9115" in r1
                assert r2 == "1"
                assert "0.0000" in r3
        finally:
            server.stop()

    def test_start_stop_lifecycle(self) -> None:
        emu = make_9115_emulator()
        server = EmulatorServer(emu, port=0)
        server.start()
        host, port = server.address
        # Verify server is reachable
        with socket.create_connection((host, port), timeout=5) as sock:
            _send_query(sock, "*IDN?")
        server.stop()
        # After stop, connections should be refused
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            result = sock.connect_ex((host, port))
            assert result != 0

    def test_address_property(self) -> None:
        emu = make_9115_emulator()
        server = EmulatorServer(emu, host="127.0.0.1", port=0)
        server.start()
        try:
            host, port = server.address
            assert host == "127.0.0.1"
            assert isinstance(port, int)
            assert port > 0
        finally:
            server.stop()
