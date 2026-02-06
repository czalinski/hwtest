"""Telemetry publishing and subscribing interfaces.

This module defines protocols for JSON-based telemetry message transport.
Use these interfaces for lower-throughput, higher-fidelity telemetry
that includes full metadata (units, quality, timestamps) per value.

For high-throughput time-series data, prefer the binary streaming
interfaces in streaming.py.

Protocols:
    TelemetryPublisher: Publish telemetry messages to a server.
    TelemetrySubscriber: Subscribe to and receive telemetry messages.
"""

# pylint: disable=unnecessary-ellipsis  # Ellipsis required for Protocol method stubs

from __future__ import annotations

from typing import AsyncIterator, Iterable, Protocol

from hwtest_core.types.common import ChannelId, SourceId
from hwtest_core.types.telemetry import TelemetryMessage


class TelemetryPublisher(Protocol):
    """Protocol for publishing telemetry data to a message broker.

    Implementations handle connection management and message serialization.
    Messages are published asynchronously and may be buffered for efficiency.

    Typical implementations include NATS, MQTT, or WebSocket backends.
    """

    async def publish(self, message: TelemetryMessage) -> None:
        """Publish a telemetry message.

        Args:
            message: The telemetry message to publish.

        Raises:
            TelemetryConnectionError: If not connected to the server.
        """
        ...

    async def connect(self) -> None:
        """Establish connection to the telemetry server.

        Raises:
            TelemetryConnectionError: If connection fails.
        """
        ...

    async def disconnect(self) -> None:
        """Disconnect from the telemetry server.

        Safe to call even if not connected.
        """
        ...

    @property
    def is_connected(self) -> bool:
        """Check if connected to the server.

        Returns:
            True if currently connected and ready to publish.
        """
        ...


class TelemetrySubscriber(Protocol):
    """Protocol for subscribing to and receiving telemetry data.

    Implementations handle connection management, subscription filtering,
    and message deserialization. Messages can be received one at a time
    or via an async iterator.

    Typical implementations include NATS, MQTT, or WebSocket backends.
    """

    async def subscribe(
        self,
        sources: Iterable[SourceId] | None = None,
        channels: Iterable[ChannelId] | None = None,
    ) -> None:
        """Subscribe to telemetry data with optional filtering.

        Args:
            sources: Source IDs to subscribe to. None means all sources.
            channels: Channel IDs to filter. None means all channels.
                     Channel filtering may be applied client-side.

        Raises:
            TelemetryConnectionError: If not connected to the server.
        """
        ...

    async def unsubscribe(self) -> None:
        """Unsubscribe from telemetry data.

        Stops receiving messages. Safe to call even if not subscribed.
        """
        ...

    async def receive(self) -> TelemetryMessage:
        """Receive the next telemetry message.

        Blocks until a message is available or connection is lost.

        Returns:
            The next telemetry message matching the subscription filter.

        Raises:
            TelemetryConnectionError: If not connected or connection lost.
        """
        ...

    def messages(self) -> AsyncIterator[TelemetryMessage]:
        """Create an async iterator over incoming telemetry messages.

        Yields:
            TelemetryMessage objects as they are received.

        Note:
            The iterator terminates when unsubscribe() is called or
            the connection is lost.
        """
        ...

    async def connect(self) -> None:
        """Establish connection to the telemetry server.

        Raises:
            TelemetryConnectionError: If connection fails.
        """
        ...

    async def disconnect(self) -> None:
        """Disconnect from the telemetry server.

        Also unsubscribes if currently subscribed. Safe to call even
        if not connected.
        """
        ...

    @property
    def is_connected(self) -> bool:
        """Check if connected to the server.

        Returns:
            True if currently connected.
        """
        ...
