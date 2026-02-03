"""Unit tests for InfluxDbStreamLogger."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from hwtest_core.types.common import DataType, SourceId
from hwtest_core.types.streaming import StreamData, StreamField, StreamSchema
from hwtest_logger.influxdb_logger import InfluxDbStreamLogger, InfluxDbStreamLoggerConfig


# Create mock influxdb_client module for tests
@pytest.fixture(autouse=True)
def mock_influxdb_client() -> MagicMock:
    """Mock the influxdb_client module for all tests."""
    mock_module = MagicMock()
    mock_module.InfluxDBClient = MagicMock()
    mock_module.Point = MagicMock()
    mock_module.client = MagicMock()
    mock_module.client.write_api = MagicMock()
    mock_module.client.write_api.SYNCHRONOUS = "synchronous"
    mock_module.client.write_api.WriteOptions = MagicMock()

    with patch.dict(sys.modules, {"influxdb_client": mock_module}):
        with patch.dict(
            sys.modules, {"influxdb_client.client.write_api": mock_module.client.write_api}
        ):
            yield mock_module


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


@pytest.fixture
def config() -> InfluxDbStreamLoggerConfig:
    """Create a sample config for testing."""
    return InfluxDbStreamLoggerConfig(
        url="http://localhost:8086",
        org="test-org",
        bucket="test-bucket",
        token="test-token",
    )


class TestInfluxDbStreamLoggerConfig:
    """Tests for InfluxDbStreamLoggerConfig."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = InfluxDbStreamLoggerConfig(
            url="http://localhost:8086",
            org="test-org",
            bucket="test-bucket",
        )
        assert config.token is None
        assert config.token_env == "INFLUXDB_TOKEN"
        assert config.measurement == "telemetry"
        assert config.batch_size == 1000
        assert config.flush_interval_ms == 1000


class TestInfluxDbStreamLogger:
    """Tests for InfluxDbStreamLogger."""

    @pytest.mark.asyncio
    async def test_start_with_token_in_config(
        self,
        mock_influxdb_client: MagicMock,
        config: InfluxDbStreamLoggerConfig,
        sample_tags: dict[str, str],
    ) -> None:
        """Test start() with token provided in config."""
        mock_client = MagicMock()
        mock_influxdb_client.InfluxDBClient.return_value = mock_client
        mock_write_api = MagicMock()
        mock_client.write_api.return_value = mock_write_api

        logger = InfluxDbStreamLogger(config)
        await logger.start(sample_tags)

        assert logger.is_running
        mock_influxdb_client.InfluxDBClient.assert_called_once_with(
            url="http://localhost:8086",
            token="test-token",
            org="test-org",
        )

        await logger.stop()

    @pytest.mark.asyncio
    async def test_start_with_token_from_env(
        self, mock_influxdb_client: MagicMock, sample_tags: dict[str, str]
    ) -> None:
        """Test start() with token from environment variable."""
        config = InfluxDbStreamLoggerConfig(
            url="http://localhost:8086",
            org="test-org",
            bucket="test-bucket",
            token=None,
            token_env="TEST_INFLUX_TOKEN",
        )

        mock_client = MagicMock()
        mock_influxdb_client.InfluxDBClient.return_value = mock_client
        mock_write_api = MagicMock()
        mock_client.write_api.return_value = mock_write_api

        with patch.dict(os.environ, {"TEST_INFLUX_TOKEN": "env-token"}):
            logger = InfluxDbStreamLogger(config)
            await logger.start(sample_tags)

            mock_influxdb_client.InfluxDBClient.assert_called_once_with(
                url="http://localhost:8086",
                token="env-token",
                org="test-org",
            )

            await logger.stop()

    @pytest.mark.asyncio
    async def test_start_without_token_raises_error(
        self, mock_influxdb_client: MagicMock, sample_tags: dict[str, str]
    ) -> None:
        """Test start() raises ValueError when no token is available."""
        config = InfluxDbStreamLoggerConfig(
            url="http://localhost:8086",
            org="test-org",
            bucket="test-bucket",
            token=None,
            token_env="NONEXISTENT_TOKEN_VAR",
        )

        # Ensure the env var doesn't exist
        os.environ.pop("NONEXISTENT_TOKEN_VAR", None)

        logger = InfluxDbStreamLogger(config)

        with pytest.raises(ValueError, match="No InfluxDB token configured"):
            await logger.start(sample_tags)

    @pytest.mark.asyncio
    async def test_log_writes_points(
        self,
        mock_influxdb_client: MagicMock,
        config: InfluxDbStreamLoggerConfig,
        sample_tags: dict[str, str],
        sample_schema: StreamSchema,
        sample_data: StreamData,
    ) -> None:
        """Test log() writes points to InfluxDB."""
        mock_client = MagicMock()
        mock_influxdb_client.InfluxDBClient.return_value = mock_client
        mock_write_api = MagicMock()
        mock_client.write_api.return_value = mock_write_api

        # Create mock points that are returned by Point()
        mock_points = [MagicMock() for _ in range(3)]
        mock_influxdb_client.Point.side_effect = mock_points

        logger = InfluxDbStreamLogger(config)
        logger.register_schema("dut_power", sample_schema)
        await logger.start(sample_tags)
        await logger.log("dut_power", sample_data)

        # Should create 3 points (one per sample)
        assert mock_influxdb_client.Point.call_count == 3

        # Check write_api.write was called
        mock_write_api.write.assert_called_once()
        call_args = mock_write_api.write.call_args
        assert call_args.kwargs["bucket"] == "test-bucket"
        assert len(call_args.kwargs["record"]) == 3

        await logger.stop()

    @pytest.mark.asyncio
    async def test_log_without_schema_raises_error(
        self,
        mock_influxdb_client: MagicMock,
        config: InfluxDbStreamLoggerConfig,
        sample_tags: dict[str, str],
        sample_data: StreamData,
    ) -> None:
        """Test log() raises ValueError if schema not registered."""
        mock_client = MagicMock()
        mock_influxdb_client.InfluxDBClient.return_value = mock_client
        mock_write_api = MagicMock()
        mock_client.write_api.return_value = mock_write_api

        logger = InfluxDbStreamLogger(config)
        await logger.start(sample_tags)

        with pytest.raises(ValueError, match="No schema registered"):
            await logger.log("unknown_topic", sample_data)

        await logger.stop()

    @pytest.mark.asyncio
    async def test_log_with_schema_mismatch_raises_error(
        self,
        mock_influxdb_client: MagicMock,
        config: InfluxDbStreamLoggerConfig,
        sample_tags: dict[str, str],
        sample_schema: StreamSchema,
    ) -> None:
        """Test log() raises ValueError on schema ID mismatch."""
        mock_client = MagicMock()
        mock_influxdb_client.InfluxDBClient.return_value = mock_client
        mock_write_api = MagicMock()
        mock_client.write_api.return_value = mock_write_api

        logger = InfluxDbStreamLogger(config)
        logger.register_schema("dut_power", sample_schema)
        await logger.start(sample_tags)

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
        mock_influxdb_client: MagicMock,
        config: InfluxDbStreamLoggerConfig,
        sample_schema: StreamSchema,
        sample_data: StreamData,
    ) -> None:
        """Test log() raises RuntimeError if logger not started."""
        logger = InfluxDbStreamLogger(config)
        logger.register_schema("dut_power", sample_schema)

        with pytest.raises(RuntimeError, match="Logger not started"):
            await logger.log("dut_power", sample_data)

    @pytest.mark.asyncio
    async def test_stop_closes_client(
        self,
        mock_influxdb_client: MagicMock,
        config: InfluxDbStreamLoggerConfig,
        sample_tags: dict[str, str],
    ) -> None:
        """Test stop() closes the client and write API."""
        mock_client = MagicMock()
        mock_influxdb_client.InfluxDBClient.return_value = mock_client
        mock_write_api = MagicMock()
        mock_client.write_api.return_value = mock_write_api

        logger = InfluxDbStreamLogger(config)
        await logger.start(sample_tags)
        await logger.stop()

        mock_write_api.close.assert_called_once()
        mock_client.close.assert_called_once()
        assert logger.is_running is False

    @pytest.mark.asyncio
    async def test_is_running_state(
        self,
        mock_influxdb_client: MagicMock,
        config: InfluxDbStreamLoggerConfig,
        sample_tags: dict[str, str],
    ) -> None:
        """Test is_running property reflects logger state."""
        mock_client = MagicMock()
        mock_influxdb_client.InfluxDBClient.return_value = mock_client
        mock_write_api = MagicMock()
        mock_client.write_api.return_value = mock_write_api

        logger = InfluxDbStreamLogger(config)
        assert logger.is_running is False

        await logger.start(sample_tags)
        assert logger.is_running is True

        await logger.stop()
        assert logger.is_running is False

    @pytest.mark.asyncio
    async def test_start_is_idempotent(
        self,
        mock_influxdb_client: MagicMock,
        config: InfluxDbStreamLoggerConfig,
        sample_tags: dict[str, str],
    ) -> None:
        """Test that calling start() multiple times is safe."""
        mock_client = MagicMock()
        mock_influxdb_client.InfluxDBClient.return_value = mock_client
        mock_write_api = MagicMock()
        mock_client.write_api.return_value = mock_write_api

        logger = InfluxDbStreamLogger(config)
        await logger.start(sample_tags)
        await logger.start(sample_tags)  # Second call should be no-op

        # Client should only be created once
        assert mock_influxdb_client.InfluxDBClient.call_count == 1

        await logger.stop()

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(
        self,
        mock_influxdb_client: MagicMock,
        config: InfluxDbStreamLoggerConfig,
        sample_tags: dict[str, str],
    ) -> None:
        """Test that calling stop() multiple times is safe."""
        mock_client = MagicMock()
        mock_influxdb_client.InfluxDBClient.return_value = mock_client
        mock_write_api = MagicMock()
        mock_client.write_api.return_value = mock_write_api

        logger = InfluxDbStreamLogger(config)
        await logger.start(sample_tags)
        await logger.stop()
        await logger.stop()  # Should not raise

        assert logger.is_running is False

    @pytest.mark.asyncio
    async def test_health_check_success(
        self,
        mock_influxdb_client: MagicMock,
        config: InfluxDbStreamLoggerConfig,
        sample_tags: dict[str, str],
    ) -> None:
        """Test health_check() returns True when healthy."""
        mock_client = MagicMock()
        mock_influxdb_client.InfluxDBClient.return_value = mock_client
        mock_write_api = MagicMock()
        mock_client.write_api.return_value = mock_write_api

        mock_health = MagicMock()
        mock_health.status = "pass"
        mock_client.health.return_value = mock_health

        logger = InfluxDbStreamLogger(config)
        await logger.start(sample_tags)

        result = await logger.health_check()
        assert result is True

        await logger.stop()

    @pytest.mark.asyncio
    async def test_health_check_failure(
        self,
        mock_influxdb_client: MagicMock,
        config: InfluxDbStreamLoggerConfig,
        sample_tags: dict[str, str],
    ) -> None:
        """Test health_check() returns False when unhealthy."""
        mock_client = MagicMock()
        mock_influxdb_client.InfluxDBClient.return_value = mock_client
        mock_write_api = MagicMock()
        mock_client.write_api.return_value = mock_write_api

        mock_health = MagicMock()
        mock_health.status = "fail"
        mock_client.health.return_value = mock_health

        logger = InfluxDbStreamLogger(config)
        await logger.start(sample_tags)

        result = await logger.health_check()
        assert result is False

        await logger.stop()

    @pytest.mark.asyncio
    async def test_health_check_not_started(
        self, mock_influxdb_client: MagicMock, config: InfluxDbStreamLoggerConfig
    ) -> None:
        """Test health_check() returns False when not started."""
        logger = InfluxDbStreamLogger(config)
        result = await logger.health_check()
        assert result is False
