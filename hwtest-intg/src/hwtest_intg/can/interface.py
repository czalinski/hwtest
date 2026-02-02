"""CAN bus interface for test rack operations.

Provides a convenient wrapper around python-can for integration testing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# CAN message ID constants used in integration tests
UUT_HEARTBEAT_ID = 0x100
RACK_TEST_MSG_ID = 0x200
ECHO_ID_OFFSET = 0x10


@dataclass
class CanMessage:
    """A CAN message.

    Args:
        arbitration_id: CAN arbitration ID (11-bit standard or 29-bit extended).
        data: Message data (0-8 bytes for CAN, 0-64 bytes for CAN FD).
        is_extended_id: True for 29-bit extended ID, False for 11-bit standard.
        is_fd: True for CAN FD frame.
        timestamp: Message timestamp in seconds.
    """

    arbitration_id: int
    data: bytes
    is_extended_id: bool = False
    is_fd: bool = False
    timestamp: float = 0.0


@dataclass
class RackCanConfig:
    """Configuration for the rack CAN interface.

    Args:
        interface: CAN interface name (e.g., "can0").
        bitrate: CAN bitrate in bits/second.
        fd: Enable CAN FD mode.
    """

    interface: str = "can0"
    bitrate: int = 500000
    fd: bool = False


class RackCanInterface:
    """CAN bus interface for the test rack.

    This class wraps python-can to provide a convenient interface for
    integration tests. It supports both blocking and async operations.

    Example:
        >>> can = RackCanInterface(RackCanConfig(interface="can0"))
        >>> can.open()
        >>> try:
        ...     can.send(0x200, b"\\x01\\x02\\x03")
        ...     msg = can.receive(timeout=1.0)
        ...     if msg:
        ...         print(f"Received: {msg.arbitration_id:03X}")
        ... finally:
        ...     can.close()
    """

    def __init__(
        self,
        config: RackCanConfig | None = None,
        bus: Any | None = None,
    ) -> None:
        """Initialize the CAN interface.

        Args:
            config: CAN interface configuration.
            bus: Optional CAN bus object (for testing with mocks).
        """
        self._config = config or RackCanConfig()
        self._bus = bus
        self._opened = False

    @property
    def config(self) -> RackCanConfig:
        """Return the interface configuration."""
        return self._config

    @property
    def is_open(self) -> bool:
        """Return True if the interface is open."""
        return self._opened

    def open(self) -> None:
        """Open the CAN interface.

        Raises:
            RuntimeError: If the interface is already open.
            ImportError: If python-can is not installed.
        """
        if self._opened:
            raise RuntimeError("Interface already open")

        if self._bus is None:
            try:
                import can

                self._bus = can.Bus(
                    interface="socketcan",
                    channel=self._config.interface,
                    fd=self._config.fd,
                )
            except ImportError as exc:
                raise ImportError(
                    "python-can library is not installed. Install with: pip install python-can"
                ) from exc

        self._opened = True
        logger.info("CAN interface opened: %s", self._config.interface)

    def close(self) -> None:
        """Close the CAN interface."""
        if not self._opened:
            return

        if self._bus is not None:
            try:
                self._bus.shutdown()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            self._bus = None

        self._opened = False
        logger.info("CAN interface closed")

    def send(
        self,
        arbitration_id: int,
        data: bytes | list[int],
        is_extended_id: bool = False,
    ) -> None:
        """Send a CAN message.

        Args:
            arbitration_id: CAN arbitration ID.
            data: Message data (bytes or list of integers).
            is_extended_id: True for 29-bit extended ID.

        Raises:
            RuntimeError: If the interface is not open.
        """
        if not self._opened or self._bus is None:
            raise RuntimeError("Interface not open")

        if isinstance(data, list):
            data = bytes(data)

        try:
            import can

            msg = can.Message(
                arbitration_id=arbitration_id,
                data=data,
                is_extended_id=is_extended_id,
            )
            self._bus.send(msg)
            logger.debug("Sent CAN message: ID=0x%03X, data=%s", arbitration_id, data.hex())
        except Exception as exc:
            logger.error("Failed to send CAN message: %s", exc)
            raise

    def receive(self, timeout: float = 1.0) -> CanMessage | None:
        """Receive a CAN message (blocking).

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            Received message, or None if timeout.

        Raises:
            RuntimeError: If the interface is not open.
        """
        if not self._opened or self._bus is None:
            raise RuntimeError("Interface not open")

        try:
            msg = self._bus.recv(timeout=timeout)
            if msg is None:
                return None

            return CanMessage(
                arbitration_id=msg.arbitration_id,
                data=bytes(msg.data),
                is_extended_id=msg.is_extended_id,
                is_fd=msg.is_fd,
                timestamp=msg.timestamp,
            )
        except Exception as exc:
            logger.error("Failed to receive CAN message: %s", exc)
            return None

    async def receive_async(self, timeout: float = 1.0) -> CanMessage | None:
        """Receive a CAN message asynchronously.

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            Received message, or None if timeout.

        Raises:
            RuntimeError: If the interface is not open.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.receive, timeout)

    async def wait_for_heartbeat(
        self,
        heartbeat_id: int = UUT_HEARTBEAT_ID,
        timeout: float = 1.0,
    ) -> CanMessage | None:
        """Wait for a heartbeat message from the UUT.

        Args:
            heartbeat_id: Expected heartbeat arbitration ID.
            timeout: Maximum time to wait in seconds.

        Returns:
            Heartbeat message if received, None if timeout.
        """
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            msg = await self.receive_async(timeout=min(remaining, 0.1))
            if msg is not None and msg.arbitration_id == heartbeat_id:
                return msg

        return None

    async def echo_test(
        self,
        message_id: int = RACK_TEST_MSG_ID,
        data: bytes | None = None,
        expected_echo_id: int | None = None,
        timeout: float = 1.0,
    ) -> CanMessage | None:
        """Send a message and wait for the echo response.

        This tests the UUT's echo functionality by sending a message
        and verifying that the echoed message is received.

        Args:
            message_id: Arbitration ID for the test message.
            data: Message data (default: incrementing bytes).
            expected_echo_id: Expected echo ID (default: message_id + ECHO_ID_OFFSET).
            timeout: Maximum time to wait for echo.

        Returns:
            Echo message if received, None if timeout.
        """
        if data is None:
            data = bytes(range(8))
        if expected_echo_id is None:
            expected_echo_id = message_id + ECHO_ID_OFFSET

        # Send the test message
        self.send(message_id, data)

        # Wait for the echo
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            msg = await self.receive_async(timeout=min(remaining, 0.1))
            if msg is not None and msg.arbitration_id == expected_echo_id:
                if msg.data == data:
                    return msg

        return None

    async def collect_messages(
        self,
        duration: float,
        filter_id: int | None = None,
    ) -> list[CanMessage]:
        """Collect CAN messages for a specified duration.

        Args:
            duration: Time to collect messages in seconds.
            filter_id: Only collect messages with this ID (None for all).

        Returns:
            List of received messages.
        """
        messages: list[CanMessage] = []
        deadline = time.monotonic() + duration

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            msg = await self.receive_async(timeout=min(remaining, 0.05))
            if msg is not None:
                if filter_id is None or msg.arbitration_id == filter_id:
                    messages.append(msg)

        return messages

    def __enter__(self) -> "RackCanInterface":
        """Context manager entry."""
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()
