"""NATS stream subscriber implementation."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, AsyncIterator

from hwtest_core.types.common import SourceId
from hwtest_core.types.streaming import MSG_TYPE_DATA, MSG_TYPE_SCHEMA, StreamData, StreamSchema

from hwtest_nats.config import NatsConfig
from hwtest_nats.connection import NatsConnection, NatsConnectionError

if TYPE_CHECKING:
    from nats.aio.msg import Msg

logger = logging.getLogger(__name__)


class NatsStreamSubscriber:
    """StreamSubscriber implementation using NATS JetStream.

    Subscribes to streaming telemetry data from NATS JetStream. Automatically
    receives and caches schema messages, then provides data messages via an
    async iterator.

    Example:
        subscriber = NatsStreamSubscriber(config)
        await subscriber.connect()
        await subscriber.subscribe("voltage_daq")

        # Wait for schema
        schema = await subscriber.get_schema(timeout=5.0)
        print(f"Receiving stream with {len(schema.fields)} fields")

        # Process data
        async for data in subscriber.data():
            for i, sample in enumerate(data.samples):
                ts = data.get_timestamp(i)
                print(f"{ts}: {sample}")

        await subscriber.unsubscribe()
        await subscriber.disconnect()
    """

    def __init__(
        self,
        config: NatsConfig,
        *,
        connection: NatsConnection | None = None,
    ) -> None:
        """Initialize the subscriber.

        Args:
            config: NATS configuration.
            connection: Optional shared connection. If not provided, a new
                        connection will be created and managed internally.
        """
        self._config = config
        self._connection = connection
        self._owns_connection = connection is None
        self._source_id: SourceId | None = None
        self._schema: StreamSchema | None = None
        self._schema_event = asyncio.Event()
        # JetStream subscription object (nats-py doesn't export proper types)
        self._subscription: Any = None
        self._data_queue: asyncio.Queue[StreamData] = asyncio.Queue()
        self._receive_task: asyncio.Task[None] | None = None

    @property
    def schema(self) -> StreamSchema | None:
        """The current schema, or None if not yet received."""
        return self._schema

    @property
    def is_connected(self) -> bool:
        """Return True if connected to NATS."""
        if self._connection is None:
            return False
        return self._connection.is_connected

    async def connect(self) -> None:
        """Connect to NATS.

        Raises:
            NatsConnectionError: If connection fails.
        """
        if self._owns_connection:
            self._connection = NatsConnection(self._config)
            await self._connection.connect()

    async def disconnect(self) -> None:
        """Disconnect from NATS."""
        await self.unsubscribe()

        if self._owns_connection and self._connection is not None:
            await self._connection.disconnect()
            self._connection = None

    async def subscribe(self, source_id: SourceId) -> None:
        """Subscribe to a stream source.

        Args:
            source_id: The identifier of the source to subscribe to.

        Raises:
            NatsConnectionError: If not connected to NATS.
            RuntimeError: If already subscribed to a source.
        """
        if self._connection is None or not self._connection.is_connected:
            raise NatsConnectionError("Not connected to NATS")

        if self._subscription is not None:
            raise RuntimeError("Already subscribed to a source")

        self._source_id = source_id
        self._schema = None
        self._schema_event.clear()

        # Subscribe to all messages for this source (schema and data)
        subject = f"{self._config.subject_prefix}.{source_id}.>"

        # Create push subscriber for JetStream
        js = self._connection.jetstream

        # Create push subscription with callback
        self._subscription = await js.subscribe(
            subject,
            cb=self._message_handler,
            durable=self._config.consumer_durable_name,
        )

        logger.info("Subscribed to source %s", source_id)

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
        if self._source_id is None:
            raise RuntimeError("Not subscribed to any source")

        if self._schema is not None:
            return self._schema

        try:
            await asyncio.wait_for(self._schema_event.wait(), timeout=timeout)
        except asyncio.TimeoutError as e:
            raise TimeoutError("Timed out waiting for schema") from e

        if self._schema is None:
            raise RuntimeError("Schema event was set but schema is None")

        return self._schema

    async def data(self) -> AsyncIterator[StreamData]:
        """Async iterator over data messages.

        Yields data messages as they are received. If no schema has been
        received yet, data messages are discarded until a schema arrives.

        Yields:
            StreamData messages matching the current schema.

        Note:
            Messages with mismatched schema_id are discarded with a warning.
        """
        while True:
            try:
                data = await self._data_queue.get()
                yield data
            except asyncio.CancelledError:
                break

    async def unsubscribe(self) -> None:
        """Unsubscribe from the current stream."""
        if self._subscription is not None:
            try:
                await self._subscription.unsubscribe()
            except Exception as e:  # pylint: disable=broad-except
                logger.warning("Error unsubscribing: %s", e)
            self._subscription = None

        if self._receive_task is not None:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        self._source_id = None
        self._schema = None
        self._schema_event.clear()

        # Clear the data queue
        while not self._data_queue.empty():
            try:
                self._data_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        logger.info("Unsubscribed from source")

    async def _message_handler(self, msg: Msg) -> None:
        """Handle incoming NATS messages."""
        data = msg.data
        if not data:
            return

        # Determine message type from first byte
        msg_type = data[0]

        if msg_type == MSG_TYPE_SCHEMA:
            await self._handle_schema_message(data)
        elif msg_type == MSG_TYPE_DATA:
            await self._handle_data_message(data)
        else:
            logger.warning("Unknown message type: %d", msg_type)

        # Acknowledge the message
        try:
            await msg.ack()
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Failed to ack message: %s", e)

    async def _handle_schema_message(self, data: bytes) -> None:
        """Handle a schema message."""
        try:
            schema = StreamSchema.from_bytes(data)
            self._schema = schema
            self._schema_event.set()
            logger.debug(
                "Received schema for %s with %d fields",
                schema.source_id,
                len(schema.fields),
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Failed to parse schema message: %s", e)

    async def _handle_data_message(self, data: bytes) -> None:
        """Handle a data message."""
        if self._schema is None:
            logger.debug("Discarding data message: no schema yet")
            return

        try:
            stream_data = StreamData.from_bytes(data, self._schema)
            await self._data_queue.put(stream_data)
            logger.debug(
                "Received %d samples, queue size: %d",
                stream_data.sample_count,
                self._data_queue.qsize(),
            )
        except ValueError as e:
            # Schema ID mismatch or parse error
            logger.warning("Discarding data message: %s", e)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Failed to parse data message: %s", e)

    async def __aenter__(self) -> NatsStreamSubscriber:
        """Enter async context."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exit async context."""
        await self.disconnect()
