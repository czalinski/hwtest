"""InfluxDB logger implementation for streaming telemetry data."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from hwtest_core.types.streaming import StreamData, StreamSchema


@dataclass
class InfluxDbStreamLoggerConfig:  # pylint: disable=too-many-instance-attributes
    """Configuration for InfluxDbStreamLogger.

    Args:
        url: InfluxDB server URL (e.g., "http://localhost:8086").
        org: InfluxDB organization name.
        bucket: InfluxDB bucket name for telemetry data.
        token: InfluxDB authentication token. If None, reads from token_env.
        token_env: Environment variable name containing the token.
        measurement: Measurement name for all telemetry points.
        batch_size: Number of points to buffer before writing.
        flush_interval_ms: Maximum time between writes in milliseconds.
    """

    url: str
    org: str
    bucket: str
    token: str | None = None
    token_env: str = "INFLUXDB_TOKEN"
    measurement: str = "telemetry"
    batch_size: int = 1000
    flush_interval_ms: int = 1000


class InfluxDbStreamLogger:  # pylint: disable=too-many-instance-attributes
    """InfluxDB logger for streaming telemetry data.

    Implements the StreamLogger protocol for persisting streaming telemetry
    data to InfluxDB. Each data point is tagged with metadata for efficient
    querying.

    Point structure:
        Measurement: telemetry (configurable)
        Tags:
            - topic: Topic identifier
            - test_run_id, test_case_id, test_type, rack_id, dut_serial: From tags
        Fields: Schema field values
        Timestamp: Nanosecond precision

    Example Flux query:
        from(bucket: "telemetry")
          |> range(start: -1d)
          |> filter(fn: (r) => r.test_type == "HALT")
          |> filter(fn: (r) => r.topic == "dut_power")
          |> filter(fn: (r) => r._field == "voltage_measured")
    """

    def __init__(self, config: InfluxDbStreamLoggerConfig) -> None:
        """Initialize the InfluxDB logger.

        Args:
            config: Logger configuration.

        Raises:
            ImportError: If influxdb-client is not installed.
        """
        self._config = config
        self._tags: dict[str, str] = {}
        self._schemas: dict[str, StreamSchema] = {}
        self._running = False
        self._client: Any = None  # InfluxDBClient when influxdb-client is installed
        self._write_api: Any = None  # WriteApi when influxdb-client is installed

    def register_schema(self, topic: str, schema: StreamSchema) -> None:
        """Register a schema for a topic.

        Must be called before logging data for that topic.

        Args:
            topic: The topic identifier (e.g., channel alias).
            schema: The schema defining the data format.
        """
        self._schemas[topic] = schema

    async def log(self, topic: str, data: StreamData) -> None:
        """Log a batch of streaming data.

        Args:
            topic: The topic this data belongs to.
            data: The streaming data batch to log.

        Raises:
            ValueError: If no schema registered for the topic.
            ValueError: If data.schema_id doesn't match registered schema.
            RuntimeError: If logger not started.
        """
        if not self._running:
            raise RuntimeError("Logger not started")

        if self._write_api is None:
            raise RuntimeError("Write API not initialized")

        schema = self._schemas.get(topic)
        if schema is None:
            raise ValueError(f"No schema registered for topic: {topic}")

        if data.schema_id != schema.schema_id:
            raise ValueError(
                f"Schema ID mismatch for topic {topic}: "
                f"data has {data.schema_id:#x}, expected {schema.schema_id:#x}"
            )

        # Import here to allow module import without influxdb-client installed
        # pylint: disable=import-outside-toplevel
        from influxdb_client import Point  # type: ignore[import-not-found]

        points: list[Any] = []
        for i, sample in enumerate(data.samples):
            timestamp_ns = data.get_timestamp(i)

            point = Point(self._config.measurement)
            point.tag("topic", topic)

            # Add all configured tags
            for tag_key, tag_value in self._tags.items():
                point.tag(tag_key, tag_value)

            # Add field values
            for field_def, value in zip(schema.fields, sample):
                point.field(field_def.name, value)

            # Set timestamp in nanoseconds
            point.time(timestamp_ns)

            points.append(point)

        self._write_api.write(bucket=self._config.bucket, record=points)

    async def start(self, tags: dict[str, str]) -> None:
        """Start the logger with the given tags.

        Args:
            tags: Metadata tags for organizing logged data.

        Raises:
            ImportError: If influxdb-client is not installed.
            ValueError: If no token is configured.
        """
        if self._running:
            return

        # Import here to allow module import without influxdb-client installed
        try:
            # pylint: disable=import-outside-toplevel
            from influxdb_client import InfluxDBClient
            from influxdb_client.client.write_api import (  # type: ignore[import-not-found]
                WriteOptions,
            )
        except ImportError as e:
            raise ImportError(
                "influxdb-client is required for InfluxDbStreamLogger. "
                "Install with: pip install hwtest-logger[influxdb]"
            ) from e

        # Get token from config or environment
        token = self._config.token
        if token is None:
            token = os.environ.get(self._config.token_env)
        if token is None:
            raise ValueError(
                f"No InfluxDB token configured. Set token in config or "
                f"environment variable {self._config.token_env}"
            )

        self._tags = tags.copy()

        # Create client and write API
        self._client = InfluxDBClient(
            url=self._config.url,
            token=token,
            org=self._config.org,
        )

        write_options = WriteOptions(
            batch_size=self._config.batch_size,
            flush_interval=self._config.flush_interval_ms,
        )
        self._write_api = self._client.write_api(write_options=write_options)

        self._running = True

    async def stop(self) -> None:
        """Stop logging and flush any buffered data."""
        if not self._running:
            return

        if self._write_api is not None:
            self._write_api.close()
            self._write_api = None

        if self._client is not None:
            self._client.close()
            self._client = None

        self._running = False

    @property
    def is_running(self) -> bool:
        """Return True if the logger is actively logging."""
        return self._running

    async def health_check(self) -> bool:
        """Check if the InfluxDB connection is healthy.

        Returns:
            True if connected and healthy, False otherwise.
        """
        if self._client is None:
            return False

        try:
            health = self._client.health()
            return bool(health.status == "pass")
        except Exception:  # pylint: disable=broad-exception-caught
            return False
