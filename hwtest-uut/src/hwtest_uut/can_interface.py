"""CAN bus interface for UUT simulator.

Provides a simple interface to SocketCAN for sending and receiving CAN messages.
Supports both standard CAN and CAN FD frames.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class CanMessage:
    """A CAN message.

    Args:
        arbitration_id: CAN arbitration ID (11-bit standard or 29-bit extended).
        data: Message data (0-8 bytes for CAN, 0-64 bytes for CAN FD).
        is_extended_id: True for 29-bit extended ID, False for 11-bit standard.
        is_fd: True for CAN FD frame.
        bitrate_switch: True if CAN FD bitrate switch is enabled.
        timestamp: Message timestamp (set by receiver).
    """

    arbitration_id: int
    data: bytes = field(default_factory=bytes)
    is_extended_id: bool = False
    is_fd: bool = False
    bitrate_switch: bool = False
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        """Validate message data."""
        if isinstance(self.data, (list, tuple)):
            self.data = bytes(self.data)
        max_len = 64 if self.is_fd else 8
        if len(self.data) > max_len:
            raise ValueError(f"data length must be <= {max_len}, got {len(self.data)}")


@dataclass(frozen=True)
class CanConfig:
    """Configuration for the CAN interface.

    Args:
        interface: CAN interface name (e.g., "can0").
        bitrate: CAN bitrate in bits/second.
        fd: Enable CAN FD mode.
        data_bitrate: CAN FD data phase bitrate (if fd=True).
    """

    interface: str = "can0"
    bitrate: int = 500000
    fd: bool = False
    data_bitrate: int = 2000000


# Type alias for message callback
MessageCallback = Callable[[CanMessage], None]


class CanInterface:
    """CAN bus interface using python-can.

    Provides async send/receive of CAN messages via SocketCAN.

    Args:
        config: CAN interface configuration.
        bus: Optional CAN bus object (for testing).
    """

    def __init__(
        self,
        config: CanConfig | None = None,
        bus: Any | None = None,
    ) -> None:
        self._config = config or CanConfig()
        self._bus = bus
        self._opened = False
        self._receive_task: asyncio.Task[None] | None = None
        self._callbacks: list[MessageCallback] = []
        self._running = False

    @property
    def config(self) -> CanConfig:
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
            ImportError: If python-can is not available.
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
            except Exception as exc:
                raise RuntimeError(f"Failed to open CAN interface: {exc}") from exc

        self._opened = True

    def close(self) -> None:
        """Close the CAN interface."""
        if not self._opened:
            return

        self._running = False

        if self._receive_task is not None:
            self._receive_task.cancel()
            self._receive_task = None

        if self._bus is not None:
            try:
                self._bus.shutdown()
            except Exception:  # pylint: disable=broad-exception-caught
                pass

        self._opened = False

    def send(self, message: CanMessage) -> None:
        """Send a CAN message.

        Args:
            message: The message to send.

        Raises:
            RuntimeError: If the interface is not open.
        """
        if not self._opened:
            raise RuntimeError("Interface not open")

        assert self._bus is not None
        try:
            # Try to use python-can Message class if available
            try:
                import can

                msg = can.Message(
                    arbitration_id=message.arbitration_id,
                    data=message.data,
                    is_extended_id=message.is_extended_id,
                    is_fd=message.is_fd,
                    bitrate_switch=message.bitrate_switch,
                )
            except ImportError:
                # Use our own message type for testing with mock bus
                msg = message

            self._bus.send(msg)
        except Exception as exc:
            logger.error("Failed to send CAN message: %s", exc)
            raise

    def send_data(
        self,
        arbitration_id: int,
        data: bytes | list[int],
        is_extended_id: bool = False,
    ) -> None:
        """Send a CAN message with the given ID and data.

        Args:
            arbitration_id: CAN arbitration ID.
            data: Message data.
            is_extended_id: True for 29-bit extended ID.

        Raises:
            RuntimeError: If the interface is not open.
        """
        if isinstance(data, list):
            data = bytes(data)
        message = CanMessage(
            arbitration_id=arbitration_id,
            data=data,
            is_extended_id=is_extended_id,
        )
        self.send(message)

    def receive(self, timeout: float = 1.0) -> CanMessage | None:
        """Receive a CAN message (blocking).

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            Received message, or None if timeout.

        Raises:
            RuntimeError: If the interface is not open.
        """
        if not self._opened:
            raise RuntimeError("Interface not open")

        assert self._bus is not None
        try:
            msg = self._bus.recv(timeout=timeout)
            if msg is None:
                return None

            return CanMessage(
                arbitration_id=msg.arbitration_id,
                data=bytes(msg.data),
                is_extended_id=msg.is_extended_id,
                is_fd=msg.is_fd,
                bitrate_switch=msg.bitrate_switch,
                timestamp=msg.timestamp,
            )
        except Exception as exc:
            logger.error("Failed to receive CAN message: %s", exc)
            return None

    def add_callback(self, callback: MessageCallback) -> None:
        """Add a callback for received messages.

        The callback will be called for each received message when
        start_receiving() is active.

        Args:
            callback: Function to call with each received message.
        """
        self._callbacks.append(callback)

    def remove_callback(self, callback: MessageCallback) -> None:
        """Remove a message callback.

        Args:
            callback: The callback to remove.
        """
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    async def start_receiving(self) -> None:
        """Start the async receive loop.

        Messages will be passed to registered callbacks.

        Raises:
            RuntimeError: If the interface is not open.
        """
        if not self._opened:
            raise RuntimeError("Interface not open")

        if self._running:
            return

        self._running = True
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def stop_receiving(self) -> None:
        """Stop the async receive loop."""
        self._running = False

        if self._receive_task is not None:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

    async def _receive_loop(self) -> None:
        """Background task for receiving messages."""
        assert self._bus is not None
        loop = asyncio.get_running_loop()

        while self._running:
            try:
                # Use run_in_executor for blocking receive
                msg = await loop.run_in_executor(None, self._bus.recv, 0.1)

                if msg is not None:
                    can_msg = CanMessage(
                        arbitration_id=msg.arbitration_id,
                        data=bytes(msg.data),
                        is_extended_id=msg.is_extended_id,
                        is_fd=msg.is_fd,
                        bitrate_switch=msg.bitrate_switch,
                        timestamp=msg.timestamp,
                    )

                    for callback in self._callbacks:
                        try:
                            callback(can_msg)
                        except Exception:  # pylint: disable=broad-exception-caught
                            logger.exception("Error in CAN message callback")

            except asyncio.CancelledError:
                break
            except Exception:  # pylint: disable=broad-exception-caught
                if self._running:
                    logger.exception("Error in CAN receive loop")
                    await asyncio.sleep(0.1)
