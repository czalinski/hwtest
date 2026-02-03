"""Unit tests for CsvStreamLogger."""

import csv
import json
from pathlib import Path

import pytest

from hwtest_core.types.common import DataType, SourceId
from hwtest_core.types.streaming import StreamData, StreamField, StreamSchema
from hwtest_logger.csv_logger import CsvStreamLogger, CsvStreamLoggerConfig


@pytest.fixture
def temp_output_dir(tmp_path: Path) -> Path:
    """Create a temporary output directory."""
    return tmp_path / "logs"


@pytest.fixture
def sample_schema() -> StreamSchema:
    """Create a sample schema for testing."""
    return StreamSchema(
        source_id=SourceId("test_source"),
        fields=(
            StreamField("voltage", DataType.F32, "V"),
            StreamField("current", DataType.F32, "A"),
        ),
    )


@pytest.fixture
def sample_tags() -> dict[str, str]:
    """Create sample tags for testing."""
    return {
        "test_run_id": "run-001",
        "test_case_id": "thermal-cycle",
        "test_type": "HALT",
        "rack_id": "rack-01",
        "dut_serial": "SN12345",
    }


@pytest.fixture
def sample_data(sample_schema: StreamSchema) -> StreamData:
    """Create sample data for testing."""
    return StreamData(
        schema_id=sample_schema.schema_id,
        timestamp_ns=1705329052000000000,
        period_ns=1000000,  # 1ms
        samples=(
            (3.30, 0.45),
            (3.29, 0.46),
            (3.31, 0.44),
        ),
    )


class TestCsvStreamLoggerConfig:
    """Tests for CsvStreamLoggerConfig."""

    def test_default_values(self, temp_output_dir: Path) -> None:
        """Test default configuration values."""
        config = CsvStreamLoggerConfig(output_dir=temp_output_dir)
        assert config.organize_by_tags is True
        assert config.buffer_size == 100


class TestCsvStreamLogger:
    """Tests for CsvStreamLogger."""

    @pytest.mark.asyncio
    async def test_start_creates_directory(
        self, temp_output_dir: Path, sample_tags: dict[str, str]
    ) -> None:
        """Test that start() creates the log directory."""
        config = CsvStreamLoggerConfig(output_dir=temp_output_dir)
        logger = CsvStreamLogger(config)

        await logger.start(sample_tags)

        assert logger.is_running
        assert logger.log_directory is not None
        assert logger.log_directory.exists()

        await logger.stop()

    @pytest.mark.asyncio
    async def test_directory_structure_with_tags(
        self, temp_output_dir: Path, sample_tags: dict[str, str]
    ) -> None:
        """Test that directory structure follows tag organization."""
        config = CsvStreamLoggerConfig(output_dir=temp_output_dir, organize_by_tags=True)
        logger = CsvStreamLogger(config)

        await logger.start(sample_tags)

        expected_path = temp_output_dir / "HALT" / "thermal-cycle" / "run-001"
        assert logger.log_directory == expected_path

        await logger.stop()

    @pytest.mark.asyncio
    async def test_directory_structure_without_tags(
        self, temp_output_dir: Path, sample_tags: dict[str, str]
    ) -> None:
        """Test that directory is output_dir when organize_by_tags=False."""
        config = CsvStreamLoggerConfig(output_dir=temp_output_dir, organize_by_tags=False)
        logger = CsvStreamLogger(config)

        await logger.start(sample_tags)

        assert logger.log_directory == temp_output_dir

        await logger.stop()

    @pytest.mark.asyncio
    async def test_log_creates_csv_file(
        self,
        temp_output_dir: Path,
        sample_tags: dict[str, str],
        sample_schema: StreamSchema,
        sample_data: StreamData,
    ) -> None:
        """Test that log() creates a CSV file with correct content."""
        config = CsvStreamLoggerConfig(output_dir=temp_output_dir)
        logger = CsvStreamLogger(config)

        logger.register_schema("dut_power", sample_schema)
        await logger.start(sample_tags)
        await logger.log("dut_power", sample_data)
        await logger.stop()

        # Check CSV file exists
        csv_path = logger.log_directory / "dut_power.csv"  # type: ignore[operator]
        assert csv_path.exists()

        # Read and verify CSV content
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Header row
        assert rows[0] == ["timestamp_ns", "voltage", "current"]

        # Data rows
        assert len(rows) == 4  # header + 3 samples
        assert rows[1][0] == "1705329052000000000"
        assert float(rows[1][1]) == pytest.approx(3.30)
        assert float(rows[1][2]) == pytest.approx(0.45)

    @pytest.mark.asyncio
    async def test_log_without_schema_raises_error(
        self,
        temp_output_dir: Path,
        sample_tags: dict[str, str],
        sample_data: StreamData,
    ) -> None:
        """Test that log() raises ValueError if schema not registered."""
        config = CsvStreamLoggerConfig(output_dir=temp_output_dir)
        logger = CsvStreamLogger(config)

        await logger.start(sample_tags)

        with pytest.raises(ValueError, match="No schema registered"):
            await logger.log("unknown_topic", sample_data)

        await logger.stop()

    @pytest.mark.asyncio
    async def test_log_with_schema_mismatch_raises_error(
        self,
        temp_output_dir: Path,
        sample_tags: dict[str, str],
        sample_schema: StreamSchema,
    ) -> None:
        """Test that log() raises ValueError on schema ID mismatch."""
        config = CsvStreamLoggerConfig(output_dir=temp_output_dir)
        logger = CsvStreamLogger(config)

        logger.register_schema("dut_power", sample_schema)
        await logger.start(sample_tags)

        # Create data with wrong schema_id
        bad_data = StreamData(
            schema_id=0xDEADBEEF,
            timestamp_ns=1705329052000000000,
            period_ns=1000000,
            samples=((3.30, 0.45),),
        )

        with pytest.raises(ValueError, match="Schema ID mismatch"):
            await logger.log("dut_power", bad_data)

        await logger.stop()

    @pytest.mark.asyncio
    async def test_log_before_start_raises_error(
        self,
        temp_output_dir: Path,
        sample_schema: StreamSchema,
        sample_data: StreamData,
    ) -> None:
        """Test that log() raises RuntimeError if logger not started."""
        config = CsvStreamLoggerConfig(output_dir=temp_output_dir)
        logger = CsvStreamLogger(config)

        logger.register_schema("dut_power", sample_schema)

        with pytest.raises(RuntimeError, match="Logger not started"):
            await logger.log("dut_power", sample_data)

    @pytest.mark.asyncio
    async def test_metadata_json_created(
        self,
        temp_output_dir: Path,
        sample_tags: dict[str, str],
        sample_schema: StreamSchema,
        sample_data: StreamData,
    ) -> None:
        """Test that metadata.json is created on stop."""
        config = CsvStreamLoggerConfig(output_dir=temp_output_dir)
        logger = CsvStreamLogger(config)

        logger.register_schema("dut_power", sample_schema)
        await logger.start(sample_tags)
        await logger.log("dut_power", sample_data)
        await logger.stop()

        metadata_path = temp_output_dir / "HALT" / "thermal-cycle" / "run-001" / "metadata.json"
        assert metadata_path.exists()

        with open(metadata_path, encoding="utf-8") as f:
            metadata = json.load(f)

        assert metadata["test_run_id"] == "run-001"
        assert metadata["test_case_id"] == "thermal-cycle"
        assert metadata["test_type"] == "HALT"
        assert "dut_power" in metadata["topics"]
        assert "dut_power" in metadata["schemas"]
        assert metadata["schemas"]["dut_power"]["fields"][0]["name"] == "voltage"

    @pytest.mark.asyncio
    async def test_multiple_topics(
        self,
        temp_output_dir: Path,
        sample_tags: dict[str, str],
    ) -> None:
        """Test logging to multiple topics."""
        config = CsvStreamLoggerConfig(output_dir=temp_output_dir)
        logger = CsvStreamLogger(config)

        schema1 = StreamSchema(
            source_id=SourceId("source1"),
            fields=(StreamField("voltage", DataType.F32, "V"),),
        )
        schema2 = StreamSchema(
            source_id=SourceId("source2"),
            fields=(StreamField("temperature", DataType.F32, "C"),),
        )

        logger.register_schema("power", schema1)
        logger.register_schema("temp", schema2)
        await logger.start(sample_tags)

        data1 = StreamData(
            schema_id=schema1.schema_id,
            timestamp_ns=1000000000,
            period_ns=1000000,
            samples=((3.3,), (3.4,)),
        )
        data2 = StreamData(
            schema_id=schema2.schema_id,
            timestamp_ns=1000000000,
            period_ns=1000000,
            samples=((25.0,), (25.5,)),
        )

        await logger.log("power", data1)
        await logger.log("temp", data2)
        await logger.stop()

        assert (logger.log_directory / "power.csv").exists()  # type: ignore[operator]
        assert (logger.log_directory / "temp.csv").exists()  # type: ignore[operator]

    @pytest.mark.asyncio
    async def test_topic_name_sanitization(
        self,
        temp_output_dir: Path,
        sample_tags: dict[str, str],
        sample_schema: StreamSchema,
        sample_data: StreamData,
    ) -> None:
        """Test that topic names with special characters are sanitized."""
        config = CsvStreamLoggerConfig(output_dir=temp_output_dir)
        logger = CsvStreamLogger(config)

        logger.register_schema("rack.psu01.ch0", sample_schema)
        await logger.start(sample_tags)
        await logger.log("rack.psu01.ch0", sample_data)
        await logger.stop()

        # Dots should be replaced with underscores
        csv_path = logger.log_directory / "rack_psu01_ch0.csv"  # type: ignore[operator]
        assert csv_path.exists()

    @pytest.mark.asyncio
    async def test_is_running_state(
        self, temp_output_dir: Path, sample_tags: dict[str, str]
    ) -> None:
        """Test is_running property reflects logger state."""
        config = CsvStreamLoggerConfig(output_dir=temp_output_dir)
        logger = CsvStreamLogger(config)

        assert logger.is_running is False

        await logger.start(sample_tags)
        assert logger.is_running is True

        await logger.stop()
        assert logger.is_running is False

    @pytest.mark.asyncio
    async def test_start_is_idempotent(
        self, temp_output_dir: Path, sample_tags: dict[str, str]
    ) -> None:
        """Test that calling start() multiple times is safe."""
        config = CsvStreamLoggerConfig(output_dir=temp_output_dir)
        logger = CsvStreamLogger(config)

        await logger.start(sample_tags)
        log_dir1 = logger.log_directory

        # Second start should be no-op
        await logger.start(sample_tags)
        assert logger.log_directory == log_dir1

        await logger.stop()

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(
        self, temp_output_dir: Path, sample_tags: dict[str, str]
    ) -> None:
        """Test that calling stop() multiple times is safe."""
        config = CsvStreamLoggerConfig(output_dir=temp_output_dir)
        logger = CsvStreamLogger(config)

        await logger.start(sample_tags)
        await logger.stop()
        await logger.stop()  # Should not raise

        assert logger.is_running is False
