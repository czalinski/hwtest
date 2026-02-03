"""NATS stream publisher implementation."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from hwtest_core.types.streaming import StreamData, StreamSchema

from hwtest_nats.config import NatsConfig
from hwtest_nats.connection import NatsConnection, NatsConnectionError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class NatsStreamPublisher:
    """StreamPublisher implementation using NATS JetStream.

    Publishes streaming telemetry data to NATS JetStream. Schema messages
    are broadcast periodically (default every 1 second) to allow subscribers
    to discover and decode the stream format.

    Example:
        schema = StreamSchema(
            source_id="voltage_daq",
            fields=(
                StreamField("voltage", DataType.FLOAT64, "V"),
            ),
        )
        publisher = NatsStreamPublisher(config, schema)
        await publisher.start()

        # Publish data
        data = StreamData(
            schema_id=schema.schema_id,
            timestamp_ns=time.time_ns(),
            period_ns=1_000_000,  # 1ms period
            samples=((3.3,), (3.31,), (3.29,)),
        )
        await publisher.publish(data)

        await publisher.stop()
    """

    def __init__(
        self,
        config: NatsConfig,
        schema: StreamSchema,
        *,
        connection: NatsConnection | None = None,
    ) -> None:
        """Initialize the publisher.

        Args:
            config: NATS configuration.
            schema: The schema for this stream.
            connection: Optional shared connection. If not provided, a new
                        connection will be created and managed internally.
        """
        self._config = config
        self._schema = schema
        self._connection = connection
        self._owns_connection = connection is None
        self._running = False
        self._schema_task: asyncio.Task[None] | None = None

    @property
    def schema(self) -> StreamSchema:
        """The schema for this stream."""
        return self._schema

    @property
    def is_running(self) -> bool:
        """Return True if the publisher is running."""
        return self._running

    async def start(self) -> None:
        """Start the publisher.

        Connects to NATS (if not using shared connection), ensures the
        JetStream stream exists, and begins periodic schema broadcasting.
        """
        if self._running:
            return

        # Connect if we own the connection
        if self._owns_connection:
            self._connection = NatsConnection(self._config)
            await self._connection.connect()

        if self._connection is None:
            raise NatsConnectionError("No connection available")

        # Ensure stream exists
        await self._connection.ensure_stream()

        self._running = True

        # Start schema broadcast task
        self._schema_task = asyncio.create_task(self._schema_broadcast_loop())
        logger.info("Started publisher for source %s", self._schema.source_id)

    async def stop(self) -> None:
        """Stop the publisher.

        Stops schema broadcasting and disconnects from NATS (if connection
        is owned by this publisher).
        """
        if not self._running:
            return

        self._running = False

        # Cancel schema task
        if self._schema_task is not None:
            self._schema_task.cancel()
            try:
                await self._schema_task
            except asyncio.CancelledError:
                pass
            self._schema_task = None

        # Disconnect if we own the connection
        if self._owns_connection and self._connection is not None:
            await self._connection.disconnect()
            self._connection = None

        logger.info("Stopped publisher for source %s", self._schema.source_id)

    async def publish(self, data: StreamData) -> None:
        """Publish a data message.

        Args:
            data: The data message to publish. Its schema_id must match
                  this publisher's schema.

        Raises:
            NatsConnectionError: If not connected to NATS.
            ValueError: If data.schema_id doesn't match the publisher's schema.
        """
        if not self._running:
            raise NatsConnectionError("Publisher is not running")

        if data.schema_id != self._schema.schema_id:
            raise ValueError(
                f"Schema ID mismatch: data has {data.schema_id:#x}, "
                f"expected {self._schema.schema_id:#x}"
            )

        if self._connection is None:
            raise NatsConnectionError("Not connected to NATS")

        subject = self._config.get_data_subject(self._schema.source_id)
        payload = data.to_bytes(self._schema)

        await self._connection.jetstream.publish(subject, payload)
        logger.debug("Published %d samples to %s", data.sample_count, subject)

    async def _publish_schema(self) -> None:
        """Publish a schema message."""
        if self._connection is None:
            return

        subject = self._config.get_schema_subject(self._schema.source_id)
        payload = self._schema.to_bytes()

        await self._connection.jetstream.publish(subject, payload)
        logger.debug("Published schema to %s", subject)

    async def _schema_broadcast_loop(self) -> None:
        """Periodically broadcast schema messages."""
        interval = self._config.schema_publish_interval
        try:
            while self._running:
                try:
                    await self._publish_schema()
                except NatsConnectionError as e:
                    logger.warning("Failed to publish schema: %s", e)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def __aenter__(self) -> NatsStreamPublisher:
        """Enter async context."""
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exit async context."""
        await self.stop()
