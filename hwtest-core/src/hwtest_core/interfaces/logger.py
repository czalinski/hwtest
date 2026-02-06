"""Logger interfaces for telemetry data persistence.

This module defines protocols for persisting streaming telemetry data to
various storage backends. Loggers receive binary streaming data and write
it to files, databases, or other persistence layers.

Two logging patterns are supported:
- Logger: NATS-integrated logging that subscribes to topics automatically
- StreamLogger: Direct logging without NATS, for testing and integration

Protocols:
    Logger: Subscribe to NATS topics and persist received data.
    StreamLogger: Log streaming data directly without NATS subscription.

Typical implementations:
- CsvStreamLogger: Writes one CSV file per topic with timestamps
- InfluxDbStreamLogger: Writes to InfluxDB time-series database
"""

# pylint: disable=unnecessary-ellipsis  # Ellipsis required for Protocol method stubs

from __future__ import annotations

from typing import Protocol

from hwtest_core.types.streaming import StreamData, StreamSchema


class Logger(Protocol):
    """Protocol for NATS-integrated telemetry logging.

    Loggers subscribe to NATS topics, receive streaming telemetry data,
    and persist it to storage. They are configured with topics to subscribe
    to and metadata tags for organizing the logged data.

    The logger handles the full lifecycle: connecting to NATS, subscribing
    to topics, receiving messages, and writing to the storage backend.

    Example:
        >>> async with logger:
        ...     await logger.start(
        ...         topics=["telemetry.voltage", "telemetry.current"],
        ...         tags={"test_run_id": "run_001", "dut_serial": "SN12345"}
        ...     )
        ...     # Logger now persists all messages from subscribed topics
        ...     await asyncio.sleep(test_duration)
        ...     await logger.stop()
    """

    async def start(self, topics: list[str], tags: dict[str, str]) -> None:
        """Start logging the specified topics with given tags.

        Subscribes to the specified NATS topics and begins persisting
        received messages. Tags are used to organize and identify the
        logged data.

        Args:
            topics: List of NATS subjects to subscribe to and log.
            tags: Metadata tags for organizing logged data. Common tags:
                - test_run_id: Unique identifier for this test run
                - test_case_id: Test case identifier
                - test_type: Category of test (HALT, HASS, functional)
                - rack_id: Test rack identifier
                - dut_serial: Device under test serial number

        Raises:
            TelemetryConnectionError: If NATS connection fails.
        """
        ...

    async def stop(self) -> None:
        """Stop logging and flush any buffered data.

        Unsubscribes from all topics, flushes any buffered data to
        storage, and releases resources. Safe to call even if not running.
        """
        ...

    @property
    def is_running(self) -> bool:
        """Check if the logger is actively logging.

        Returns:
            True if the logger is running and persisting data.
        """
        ...


class StreamLogger(Protocol):
    """Protocol for direct streaming data logging without NATS.

    This interface is used when telemetry data is provided directly
    rather than received via NATS subscription. It is useful for:
    - Unit testing loggers without NATS infrastructure
    - Integration scenarios with direct instrument connections
    - Custom data pipelines that bypass NATS

    Schemas must be registered before logging data for a topic.

    Example:
        >>> schema = StreamSchema(source_id=SourceId("sensor"), fields=(...))
        >>> logger.register_schema("voltage", schema)
        >>> await logger.start(tags={"test_run_id": "run_001"})
        >>> await logger.log("voltage", data)
        >>> await logger.stop()
    """

    def register_schema(self, topic: str, schema: StreamSchema) -> None:
        """Register a schema for a topic.

        Must be called before logging data for that topic. The schema
        is used to interpret the binary data and extract field names
        and units for the storage backend.

        Args:
            topic: Topic identifier (e.g., channel alias like "voltage").
            schema: The StreamSchema defining the data format.
        """
        ...

    async def log(self, topic: str, data: StreamData) -> None:
        """Log a batch of streaming data.

        Persists the data batch to the storage backend. The data's
        schema_id must match the registered schema for the topic.

        Args:
            topic: The topic this data belongs to.
            data: The StreamData batch containing samples to log.

        Raises:
            ValueError: If no schema is registered for the topic.
            ValueError: If data.schema_id doesn't match the registered schema.
        """
        ...

    async def start(self, tags: dict[str, str]) -> None:
        """Start the logger with the given metadata tags.

        Initializes the storage backend and prepares for logging.
        Tags are used to organize and identify the logged data.

        Args:
            tags: Metadata tags for organizing logged data. Common tags:
                - test_run_id: Unique identifier for this test run
                - test_case_id: Test case identifier
                - test_type: Category of test (HALT, HASS, functional)
        """
        ...

    async def stop(self) -> None:
        """Stop logging and flush any buffered data.

        Flushes any buffered data to storage and releases resources.
        Safe to call even if not running.
        """
        ...

    @property
    def is_running(self) -> bool:
        """Check if the logger is actively logging.

        Returns:
            True if the logger is running and ready to persist data.
        """
        ...
