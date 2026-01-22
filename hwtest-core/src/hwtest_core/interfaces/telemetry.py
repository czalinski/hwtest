"""Telemetry publishing and subscribing interfaces."""

# pylint: disable=unnecessary-ellipsis  # Ellipsis required for Protocol method stubs

from __future__ import annotations

from typing import AsyncIterator, Iterable, Protocol

from hwtest_core.types.common import ChannelId, SourceId
from hwtest_core.types.telemetry import TelemetryMessage


class TelemetryPublisher(Protocol):
    """Interface for publishing telemetry data.

    Implementations are responsible for:
    - Connecting to the telemetry server
    - Publishing telemetry messages
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
        """Establish connection to the telemetry server."""
        ...

    async def disconnect(self) -> None:
        """Disconnect from the telemetry server."""
        ...

    @property
    def is_connected(self) -> bool:
        """Return True if connected to the server."""
        ...


class TelemetrySubscriber(Protocol):
    """Interface for subscribing to telemetry data.

    Implementations are responsible for:
    - Connecting to the telemetry server
    - Filtering messages by source and/or channel
    - Providing messages to the consumer
    """

    async def subscribe(
        self,
        sources: Iterable[SourceId] | None = None,
        channels: Iterable[ChannelId] | None = None,
    ) -> None:
        """Subscribe to telemetry data.

        Args:
            sources: Source IDs to subscribe to. None means all sources.
            channels: Channel IDs to subscribe to. None means all channels.

        Raises:
            TelemetryConnectionError: If not connected to the server.
        """
        ...

    async def unsubscribe(self) -> None:
        """Unsubscribe from telemetry data."""
        ...

    async def receive(self) -> TelemetryMessage:
        """Receive the next telemetry message.

        Blocks until a message is available.

        Returns:
            The next telemetry message.

        Raises:
            TelemetryConnectionError: If not connected to the server.
        """
        ...

    def messages(self) -> AsyncIterator[TelemetryMessage]:
        """Async iterator over incoming telemetry messages.

        Yields:
            TelemetryMessage objects as they are received.
        """
        ...

    async def connect(self) -> None:
        """Establish connection to the telemetry server."""
        ...

    async def disconnect(self) -> None:
        """Disconnect from the telemetry server."""
        ...

    @property
    def is_connected(self) -> bool:
        """Return True if connected to the server."""
        ...
