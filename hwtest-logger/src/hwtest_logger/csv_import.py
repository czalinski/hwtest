"""Import CSV telemetry data into InfluxDB.

This module provides utilities for importing CSV data captured during offsite
testing into InfluxDB for analysis and visualization.

The CSV data is organized by the CsvStreamLogger in the following structure:
    {output_dir}/{test_type}/{test_case_id}/{test_run_id}/
        {topic}.csv
        metadata.json

The metadata.json file contains:
    - tags: test_type, test_case_id, test_run_id, rack_id, uut_id, etc.
    - topics: list of topic names
    - schemas: field definitions for each topic

Usage:
    # Import a single test run
    await import_test_run("/logs/HASS/voltage_echo_monitor/abc123", influx_config)

    # Import all test runs in a directory
    await import_all_test_runs("/logs", influx_config)

CLI:
    hwtest-import-csv /path/to/logs --url http://localhost:8086 --token <token>
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ImportConfig:
    """Configuration for CSV import to InfluxDB.

    Attributes:
        url: InfluxDB URL.
        org: InfluxDB organization.
        bucket: InfluxDB bucket.
        token: InfluxDB authentication token.
        measurement: Measurement name (default: "telemetry").
        batch_size: Number of points per batch (default: 1000).
        dry_run: If True, don't actually write to InfluxDB.
    """

    url: str
    org: str
    bucket: str
    token: str
    measurement: str = "telemetry"
    batch_size: int = 1000
    dry_run: bool = False


@dataclass
class ImportResult:
    """Result of a CSV import operation.

    Attributes:
        test_run_id: The test run ID.
        path: Path to the test run directory.
        topics_imported: Number of topics imported.
        points_imported: Total number of data points imported.
        errors: List of error messages.
        success: True if import completed without errors.
    """

    test_run_id: str
    path: Path
    topics_imported: int = 0
    points_imported: int = 0
    errors: list[str] | None = None
    success: bool = True

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def find_test_runs(root_dir: str | Path) -> list[Path]:
    """Find all test run directories under a root directory.

    A test run directory is identified by the presence of a metadata.json file.

    Args:
        root_dir: Root directory to search.

    Returns:
        List of paths to test run directories.
    """
    root = Path(root_dir)
    test_runs: list[Path] = []

    for metadata_path in root.rglob("metadata.json"):
        test_runs.append(metadata_path.parent)

    return sorted(test_runs)


def load_metadata(test_run_dir: Path) -> dict[str, Any] | None:
    """Load metadata.json from a test run directory.

    Args:
        test_run_dir: Path to the test run directory.

    Returns:
        Metadata dictionary, or None if not found.
    """
    metadata_path = test_run_dir / "metadata.json"
    if not metadata_path.exists():
        return None

    with open(metadata_path, encoding="utf-8") as f:
        return json.load(f)


async def import_test_run(
    test_run_dir: str | Path,
    config: ImportConfig,
) -> ImportResult:
    """Import a single test run from CSV files to InfluxDB.

    Args:
        test_run_dir: Path to the test run directory.
        config: Import configuration.

    Returns:
        ImportResult with import statistics.
    """
    test_run_dir = Path(test_run_dir)
    result = ImportResult(
        test_run_id=test_run_dir.name,
        path=test_run_dir,
    )

    # Load metadata
    metadata = load_metadata(test_run_dir)
    if metadata is None:
        result.success = False
        result.errors.append("metadata.json not found")
        return result

    # Extract tags from metadata
    tags = {
        k: v for k, v in metadata.items()
        if k not in ("topics", "schemas") and isinstance(v, str)
    }

    topics = metadata.get("topics", [])
    schemas = metadata.get("schemas", {})

    if not topics:
        result.success = False
        result.errors.append("No topics found in metadata")
        return result

    try:
        from influxdb_client import InfluxDBClient, Point
        from influxdb_client.client.write_api import SYNCHRONOUS
    except ImportError:
        result.success = False
        result.errors.append("influxdb-client not installed")
        return result

    # Connect to InfluxDB
    if not config.dry_run:
        client = InfluxDBClient(
            url=config.url,
            token=config.token,
            org=config.org,
        )
        write_api = client.write_api(write_options=SYNCHRONOUS)
    else:
        client = None
        write_api = None

    try:
        for topic in topics:
            csv_path = test_run_dir / f"{topic}.csv"
            if not csv_path.exists():
                result.errors.append(f"CSV file not found: {topic}.csv")
                continue

            schema = schemas.get(topic, {})
            fields_info = schema.get("fields", [])
            field_names = [f["name"] for f in fields_info]

            points_batch: list[Any] = []
            topic_points = 0

            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)

                for row in reader:
                    timestamp_ns = int(row["timestamp_ns"])

                    point = Point(config.measurement)
                    point.time(timestamp_ns)

                    # Add tags
                    for tag_name, tag_value in tags.items():
                        point.tag(tag_name, tag_value)
                    point.tag("topic", topic)

                    # Add fields
                    for field_name in field_names:
                        if field_name in row:
                            try:
                                point.field(field_name, float(row[field_name]))
                            except ValueError:
                                point.field(field_name, row[field_name])

                    points_batch.append(point)
                    topic_points += 1

                    # Write batch
                    if len(points_batch) >= config.batch_size:
                        if write_api is not None:
                            write_api.write(bucket=config.bucket, record=points_batch)
                        points_batch = []

                # Write remaining points
                if points_batch and write_api is not None:
                    write_api.write(bucket=config.bucket, record=points_batch)

            result.topics_imported += 1
            result.points_imported += topic_points
            logger.info(
                "Imported %d points from %s/%s",
                topic_points, test_run_dir.name, topic
            )

    except Exception as exc:
        result.success = False
        result.errors.append(str(exc))

    finally:
        if client is not None:
            client.close()

    return result


async def import_all_test_runs(
    root_dir: str | Path,
    config: ImportConfig,
) -> list[ImportResult]:
    """Import all test runs found under a root directory.

    Args:
        root_dir: Root directory to search for test runs.
        config: Import configuration.

    Returns:
        List of ImportResult objects for each test run.
    """
    test_runs = find_test_runs(root_dir)
    logger.info("Found %d test runs to import", len(test_runs))

    results: list[ImportResult] = []
    for test_run_dir in test_runs:
        logger.info("Importing %s...", test_run_dir)
        result = await import_test_run(test_run_dir, config)
        results.append(result)

        if result.success:
            logger.info(
                "  Success: %d topics, %d points",
                result.topics_imported, result.points_imported
            )
        else:
            logger.error("  Failed: %s", result.errors)

    return results


def main() -> None:
    """CLI entry point for CSV import."""
    import argparse
    import os

    parser = argparse.ArgumentParser(
        description="Import CSV telemetry data into InfluxDB"
    )
    parser.add_argument(
        "path",
        help="Path to test run directory or root directory containing test runs"
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("INFLUXDB_URL", "http://localhost:8086"),
        help="InfluxDB URL"
    )
    parser.add_argument(
        "--org",
        default=os.environ.get("INFLUXDB_ORG", "hwtest"),
        help="InfluxDB organization"
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("INFLUXDB_BUCKET", "telemetry"),
        help="InfluxDB bucket"
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("INFLUXDB_TOKEN"),
        help="InfluxDB token"
    )
    parser.add_argument(
        "--measurement",
        default="telemetry",
        help="InfluxDB measurement name"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Points per batch"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually write to InfluxDB"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )

    if not args.token and not args.dry_run:
        parser.error("--token is required (or set INFLUXDB_TOKEN)")

    config = ImportConfig(
        url=args.url,
        org=args.org,
        bucket=args.bucket,
        token=args.token or "",
        measurement=args.measurement,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )

    path = Path(args.path)

    if (path / "metadata.json").exists():
        # Single test run
        result = asyncio.run(import_test_run(path, config))
        if result.success:
            print(f"Imported {result.points_imported} points from {result.topics_imported} topics")
        else:
            print(f"Failed: {result.errors}")
            exit(1)
    else:
        # Multiple test runs
        results = asyncio.run(import_all_test_runs(path, config))
        success = sum(1 for r in results if r.success)
        total_points = sum(r.points_imported for r in results)
        print(f"Imported {success}/{len(results)} test runs, {total_points} total points")
        if success < len(results):
            exit(1)


if __name__ == "__main__":
    main()
