"""Logger interface for telemetry data persistence."""

# pylint: disable=unnecessary-ellipsis  # Ellipsis required for Protocol method stubs

from __future__ import annotations

from typing import Protocol

from hwtest_core.types.streaming import StreamData, StreamSchema


class Logger(Protocol):
    """Interface for telemetry loggers.

    Loggers receive streaming telemetry data and persist it to storage.
    They are configured with topics to subscribe to and metadata tags
    for organizing the logged data.

    Implementations include:
    - CsvLogger: Writes one CSV file per topic
    - InfluxDbLogger: Writes to InfluxDB time-series database
    """

    async def start(self, topics: list[str], tags: dict[str, str]) -> None:
        """Start logging the specified topics with given tags.

        Args:
            topics: List of NATS subjects to subscribe to and log.
            tags: Metadata tags for organizing logged data. Common tags:
                - test_run_id: Unique identifier for this test run
                - test_case_id: Test case identifier
                - test_type: Category of test (HALT, HASS, functional)
                - rack_id: Test rack identifier
                - dut_serial: Device under test serial number
        """
        ...

    async def stop(self) -> None:
        """Stop logging and flush any buffered data."""
        ...

    @property
    def is_running(self) -> bool:
        """Return True if the logger is actively logging."""
        ...


class StreamLogger(Protocol):
    """Interface for logging streaming data directly (without NATS).

    This interface is used when telemetry data is received directly
    rather than via NATS subscription. Useful for testing and
    integration scenarios.
    """

    def register_schema(self, topic: str, schema: StreamSchema) -> None:
        """Register a schema for a topic.

        Must be called before logging data for that topic.

        Args:
            topic: The topic identifier (e.g., channel alias).
            schema: The schema defining the data format.
        """
        ...

    async def log(self, topic: str, data: StreamData) -> None:
        """Log a batch of streaming data.

        Args:
            topic: The topic this data belongs to.
            data: The streaming data batch to log.

        Raises:
            ValueError: If no schema registered for the topic.
            ValueError: If data.schema_id doesn't match registered schema.
        """
        ...

    async def start(self, tags: dict[str, str]) -> None:
        """Start the logger with the given tags.

        Args:
            tags: Metadata tags for organizing logged data.
        """
        ...

    async def stop(self) -> None:
        """Stop logging and flush any buffered data."""
        ...

    @property
    def is_running(self) -> bool:
        """Return True if the logger is actively logging."""
        ...
