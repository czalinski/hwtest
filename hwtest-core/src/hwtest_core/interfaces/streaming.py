"""Streaming data protocol interfaces."""

# pylint: disable=unnecessary-ellipsis  # Ellipsis required for Protocol method stubs

from __future__ import annotations

from typing import AsyncIterator, Protocol

from hwtest_core.types.common import SourceId
from hwtest_core.types.streaming import StreamData, StreamSchema


class StreamPublisher(Protocol):
    """Interface for publishing streaming data.

    Implementations are responsible for:
    - Connecting to the telemetry server
    - Publishing schema messages periodically (every 1 second)
    - Publishing data messages as samples are acquired
    """

    @property
    def schema(self) -> StreamSchema:
        """The schema for this stream."""
        ...

    async def publish(self, data: StreamData) -> None:
        """Publish a data message.

        Args:
            data: The data message to publish. Its schema_id must match
                  this publisher's schema.

        Raises:
            TelemetryConnectionError: If not connected to the server.
            ValueError: If data.schema_id doesn't match the publisher's schema.
        """
        ...

    async def start(self) -> None:
        """Start the publisher.

        Connects to the telemetry server and begins periodic schema
        broadcasting (every 1 second).
        """
        ...

    async def stop(self) -> None:
        """Stop the publisher.

        Stops schema broadcasting and disconnects from the server.
        """
        ...

    @property
    def is_running(self) -> bool:
        """Return True if the publisher is running."""
        ...


class StreamSubscriber(Protocol):
    """Interface for subscribing to streaming data.

    Implementations are responsible for:
    - Connecting to the telemetry server
    - Receiving and caching schema messages
    - Providing data messages to the consumer
    """

    async def subscribe(self, source_id: SourceId) -> None:
        """Subscribe to a stream source.

        Args:
            source_id: The identifier of the source to subscribe to.

        Raises:
            TelemetryConnectionError: If not connected to the server.
        """
        ...

    async def get_schema(self, timeout: float | None = None) -> StreamSchema:
        """Get the schema for the subscribed stream.

        Waits for a schema message if one hasn't been received yet.

        Args:
            timeout: Maximum time to wait for schema in seconds.
                     None means wait indefinitely.

        Returns:
            The stream schema.

        Raises:
            TimeoutError: If timeout expires before schema is received.
            RuntimeError: If not subscribed to any source.
        """
        ...

    @property
    def schema(self) -> StreamSchema | None:
        """The current schema, or None if not yet received."""
        ...

    def data(self) -> AsyncIterator[StreamData]:
        """Async iterator over data messages.

        Yields data messages as they are received. If no schema has been
        received yet, data messages are discarded until a schema arrives.

        Yields:
            StreamData messages matching the current schema.

        Note:
            Messages with mismatched schema_id are discarded with a warning.
        """
        ...

    async def unsubscribe(self) -> None:
        """Unsubscribe from the current stream."""
        ...

    async def connect(self) -> None:
        """Connect to the telemetry server."""
        ...

    async def disconnect(self) -> None:
        """Disconnect from the telemetry server."""
        ...

    @property
    def is_connected(self) -> bool:
        """Return True if connected to the server."""
        ...
