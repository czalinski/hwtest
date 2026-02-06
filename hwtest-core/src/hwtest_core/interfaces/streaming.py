"""Binary streaming data publishing and subscribing interfaces.

This module defines protocols for high-throughput binary streaming data
transport. Use these interfaces for low-latency (<25ms software budget)
time-series telemetry from instruments to monitors and loggers.

The streaming protocol uses a compact binary format with:
- Schema messages (0x01): Broadcast every 1 second, describe data structure
- Data messages (0x02): Packed samples with implicit timestamps

For lower-throughput telemetry with full metadata per value, prefer the
JSON-based interfaces in telemetry.py.

Protocols:
    StreamPublisher: Publish streaming data with periodic schema broadcast.
    StreamSubscriber: Subscribe to and receive streaming data.
"""

# pylint: disable=unnecessary-ellipsis  # Ellipsis required for Protocol method stubs

from __future__ import annotations

from typing import AsyncIterator, Protocol

from hwtest_core.types.common import SourceId
from hwtest_core.types.streaming import StreamData, StreamSchema


class StreamPublisher(Protocol):
    """Protocol for publishing binary streaming data.

    Implementations handle connection management, periodic schema broadcasting,
    and efficient binary data serialization. Schema messages are broadcast
    every 1 second to allow late-joining subscribers to decode the data stream.

    Typical implementations include NATS JetStream for reliable delivery.

    Example:
        >>> async with publisher:
        ...     data = StreamData(schema_id=schema.schema_id, ...)
        ...     await publisher.publish(data)
    """

    @property
    def schema(self) -> StreamSchema:
        """Get the schema for this stream.

        Returns:
            The StreamSchema defining the data format for this publisher.
        """
        ...

    async def publish(self, data: StreamData) -> None:
        """Publish a streaming data message.

        The data message is serialized to binary format and published to
        the telemetry server. The schema_id in the data must match this
        publisher's schema.

        Args:
            data: The data message to publish containing one or more samples.

        Raises:
            TelemetryConnectionError: If not connected to the server.
            ValueError: If data.schema_id doesn't match the publisher's schema.
        """
        ...

    async def start(self) -> None:
        """Start the publisher and begin schema broadcasting.

        Connects to the telemetry server (if not already connected) and
        begins periodic schema broadcasting every 1 second to allow
        late-joining subscribers to decode the data stream.

        Raises:
            TelemetryConnectionError: If connection to the server fails.
        """
        ...

    async def stop(self) -> None:
        """Stop the publisher and schema broadcasting.

        Stops periodic schema broadcasting and disconnects from the server.
        Safe to call even if not running.
        """
        ...

    @property
    def is_running(self) -> bool:
        """Check if the publisher is running.

        Returns:
            True if the publisher is active and broadcasting schemas.
        """
        ...


class StreamSubscriber(Protocol):
    """Protocol for subscribing to and receiving binary streaming data.

    Implementations handle connection management, schema caching, and
    efficient binary data deserialization. Schema messages are received
    periodically (every 1 second) and cached to decode subsequent data.

    Typical implementations include NATS JetStream for reliable delivery.

    Example:
        >>> async with subscriber:
        ...     await subscriber.subscribe(SourceId("sensor"))
        ...     schema = await subscriber.get_schema(timeout=5.0)
        ...     async for data in subscriber.data():
        ...         process(data)
    """

    async def subscribe(self, source_id: SourceId) -> None:
        """Subscribe to a streaming data source.

        Begins receiving schema and data messages from the specified source.
        The schema is cached when received to decode subsequent data messages.

        Args:
            source_id: Identifier of the data source to subscribe to.

        Raises:
            TelemetryConnectionError: If not connected to the server.
        """
        ...

    async def get_schema(self, timeout: float | None = None) -> StreamSchema:
        """Get the schema for the subscribed stream.

        If a schema has already been received, returns immediately.
        Otherwise, waits for a schema message from the publisher.

        Args:
            timeout: Maximum time to wait for schema in seconds.
                     None means wait indefinitely.

        Returns:
            The StreamSchema describing the data format.

        Raises:
            TimeoutError: If timeout expires before schema is received.
            RuntimeError: If not subscribed to any source.
        """
        ...

    @property
    def schema(self) -> StreamSchema | None:
        """Get the cached schema if available.

        Returns:
            The cached StreamSchema, or None if no schema received yet.
        """
        ...

    def data(self) -> AsyncIterator[StreamData]:
        """Create an async iterator over incoming data messages.

        Yields data messages as they are received and deserialized.
        If no schema has been received yet, data messages are discarded
        until a schema arrives (since they cannot be decoded).

        Yields:
            StreamData objects containing sample batches.

        Note:
            Messages with mismatched schema_id are discarded with a warning,
            as they may be from an outdated or different schema version.
        """
        ...

    async def unsubscribe(self) -> None:
        """Unsubscribe from the current stream.

        Stops receiving messages from the subscribed source.
        Safe to call even if not subscribed.
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

        Also unsubscribes if currently subscribed.
        Safe to call even if not connected.
        """
        ...

    @property
    def is_connected(self) -> bool:
        """Check if connected to the server.

        Returns:
            True if currently connected.
        """
        ...
