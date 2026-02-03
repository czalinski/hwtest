"""CSV logger implementation for streaming telemetry data."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from hwtest_core.types.streaming import StreamData, StreamSchema


@dataclass
class CsvStreamLoggerConfig:
    """Configuration for CsvStreamLogger.

    Args:
        output_dir: Base directory for log files.
        organize_by_tags: If True, creates subdirectories based on tags.
            Directory structure: {output_dir}/{test_type}/{test_case_id}/{test_run_id}/
        buffer_size: Number of rows to buffer before flushing to disk.
    """

    output_dir: str | Path
    organize_by_tags: bool = True
    buffer_size: int = 100


@dataclass
class _TopicWriter:
    """Internal state for a single topic's CSV writer."""

    file_handle: TextIO
    writer: Any  # csv.writer returns _writer which is not properly typed
    schema: StreamSchema
    row_count: int = 0


class CsvStreamLogger:
    """CSV logger that writes one file per topic.

    Implements the StreamLogger protocol for persisting streaming telemetry
    data to CSV files. Each topic gets its own CSV file with columns matching
    the schema fields, plus a timestamp column.

    File organization (when organize_by_tags=True):
        {output_dir}/{test_type}/{test_case_id}/{test_run_id}/
            {topic}.csv
            metadata.json

    CSV format:
        timestamp_ns,field1,field2,...
        1705329052000000000,3.30,0.45,...
    """

    def __init__(self, config: CsvStreamLoggerConfig) -> None:
        """Initialize the CSV logger.

        Args:
            config: Logger configuration.
        """
        self._config = config
        self._output_dir = Path(config.output_dir)
        self._tags: dict[str, str] = {}
        self._schemas: dict[str, StreamSchema] = {}
        self._writers: dict[str, _TopicWriter] = {}
        self._running = False
        self._log_dir: Path | None = None

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

        schema = self._schemas.get(topic)
        if schema is None:
            raise ValueError(f"No schema registered for topic: {topic}")

        if data.schema_id != schema.schema_id:
            raise ValueError(
                f"Schema ID mismatch for topic {topic}: "
                f"data has {data.schema_id:#x}, expected {schema.schema_id:#x}"
            )

        writer = self._get_or_create_writer(topic, schema)

        for i, sample in enumerate(data.samples):
            timestamp_ns = data.get_timestamp(i)
            row = [timestamp_ns, *sample]
            writer.writer.writerow(row)
            writer.row_count += 1

        if writer.row_count >= self._config.buffer_size:
            writer.file_handle.flush()
            writer.row_count = 0

    async def start(self, tags: dict[str, str]) -> None:
        """Start the logger with the given tags.

        Args:
            tags: Metadata tags for organizing logged data.
        """
        if self._running:
            return

        self._tags = tags.copy()
        self._log_dir = self._create_log_directory()
        self._running = True

    async def stop(self) -> None:
        """Stop logging and flush any buffered data."""
        if not self._running:
            return

        # Close all file handles
        for writer in self._writers.values():
            writer.file_handle.flush()
            writer.file_handle.close()

        # Write metadata file
        if self._log_dir is not None:
            self._write_metadata()

        self._writers.clear()
        self._running = False

    @property
    def is_running(self) -> bool:
        """Return True if the logger is actively logging."""
        return self._running

    @property
    def log_directory(self) -> Path | None:
        """Return the current log directory, or None if not started."""
        return self._log_dir

    def _create_log_directory(self) -> Path:
        """Create and return the log directory based on tags."""
        if self._config.organize_by_tags:
            test_type = self._tags.get("test_type", "unknown")
            test_case_id = self._tags.get("test_case_id", "unknown")
            test_run_id = self._tags.get("test_run_id", "unknown")
            log_dir = self._output_dir / test_type / test_case_id / test_run_id
        else:
            log_dir = self._output_dir

        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir

    def _get_or_create_writer(self, topic: str, schema: StreamSchema) -> _TopicWriter:
        """Get or create a CSV writer for the given topic."""
        if topic in self._writers:
            return self._writers[topic]

        if self._log_dir is None:
            raise RuntimeError("Log directory not initialized")

        # Sanitize topic name for filesystem
        safe_topic = topic.replace("/", "_").replace(".", "_")
        file_path = self._log_dir / f"{safe_topic}.csv"

        # Create file and write header. File stays open for streaming writes
        # and is closed in stop(). pylint: disable=consider-using-with
        file_handle = open(file_path, "w", newline="", encoding="utf-8")
        writer = csv.writer(file_handle)

        # Header: timestamp_ns followed by field names
        header = ["timestamp_ns"] + [f.name for f in schema.fields]
        writer.writerow(header)

        topic_writer = _TopicWriter(
            file_handle=file_handle,
            writer=writer,
            schema=schema,
        )
        self._writers[topic] = topic_writer
        return topic_writer

    def _write_metadata(self) -> None:
        """Write metadata.json file with tags and topic information."""
        if self._log_dir is None:
            return

        metadata = {
            **self._tags,
            "topics": list(self._writers.keys()),
            "schemas": {
                topic: {
                    "source_id": str(writer.schema.source_id),
                    "schema_id": f"{writer.schema.schema_id:#x}",
                    "fields": [
                        {"name": f.name, "dtype": f.dtype.name, "unit": f.unit}
                        for f in writer.schema.fields
                    ],
                }
                for topic, writer in self._writers.items()
            },
        }

        metadata_path = self._log_dir / "metadata.json"
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
