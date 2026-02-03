"""Unit tests for NATS stream publisher."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hwtest_core.types.common import DataType
from hwtest_core.types.streaming import StreamData, StreamField, StreamSchema

# DataType uses F64 not FLOAT64

from hwtest_nats.config import NatsConfig
from hwtest_nats.connection import NatsConnection, NatsConnectionError
from hwtest_nats.publisher import NatsStreamPublisher


class TestNatsStreamPublisher:
    """Tests for NatsStreamPublisher."""

    @pytest.fixture
    def config(self) -> NatsConfig:
        """Create a test configuration."""
        return NatsConfig(
            servers=("nats://localhost:4222",),
            schema_publish_interval=0.1,  # Fast interval for tests
        )

    @pytest.fixture
    def schema(self) -> StreamSchema:
        """Create a test schema."""
        return StreamSchema(
            source_id="test_sensor",
            fields=(
                StreamField("voltage", DataType.F64, "V"),
                StreamField("current", DataType.F64, "A"),
            ),
        )

    @pytest.fixture
    def sample_data(self, schema: StreamSchema) -> StreamData:
        """Create sample data matching the schema."""
        return StreamData(
            schema_id=schema.schema_id,
            timestamp_ns=1000000000,
            period_ns=1000000,
            samples=((3.3, 0.1), (3.31, 0.11)),
        )

    @pytest.fixture
    def mock_connection(self) -> MagicMock:
        """Create a mock NATS connection."""
        conn = MagicMock(spec=NatsConnection)
        conn.is_connected = True
        conn.ensure_stream = AsyncMock()
        conn.disconnect = AsyncMock()

        mock_js = MagicMock()
        mock_js.publish = AsyncMock()
        conn.jetstream = mock_js

        return conn

    def test_schema_property(self, config: NatsConfig, schema: StreamSchema) -> None:
        """Test schema property."""
        publisher = NatsStreamPublisher(config, schema)
        assert publisher.schema == schema

    def test_initial_state(self, config: NatsConfig, schema: StreamSchema) -> None:
        """Test initial publisher state."""
        publisher = NatsStreamPublisher(config, schema)

        assert not publisher.is_running

    async def test_start_creates_connection(self, config: NatsConfig, schema: StreamSchema) -> None:
        """Test that start creates connection if not provided."""
        with patch("hwtest_nats.publisher.NatsConnection") as mock_conn_class:
            mock_conn = MagicMock()
            mock_conn.connect = AsyncMock()
            mock_conn.ensure_stream = AsyncMock()
            mock_conn.disconnect = AsyncMock()
            mock_conn.jetstream = MagicMock()
            mock_conn.jetstream.publish = AsyncMock()
            mock_conn_class.return_value = mock_conn

            publisher = NatsStreamPublisher(config, schema)
            await publisher.start()

            mock_conn_class.assert_called_once_with(config)
            mock_conn.connect.assert_called_once()
            mock_conn.ensure_stream.assert_called_once()

            await publisher.stop()

    async def test_start_with_shared_connection(
        self, config: NatsConfig, schema: StreamSchema, mock_connection: MagicMock
    ) -> None:
        """Test start with shared connection."""
        publisher = NatsStreamPublisher(config, schema, connection=mock_connection)
        await publisher.start()

        assert publisher.is_running
        mock_connection.ensure_stream.assert_called_once()

        # Stop shouldn't disconnect shared connection
        await publisher.stop()
        mock_connection.disconnect.assert_not_called()

    async def test_start_idempotent(
        self, config: NatsConfig, schema: StreamSchema, mock_connection: MagicMock
    ) -> None:
        """Test that calling start twice is idempotent."""
        publisher = NatsStreamPublisher(config, schema, connection=mock_connection)
        await publisher.start()
        await publisher.start()  # Second call should be no-op

        mock_connection.ensure_stream.assert_called_once()

        await publisher.stop()

    async def test_stop(
        self, config: NatsConfig, schema: StreamSchema, mock_connection: MagicMock
    ) -> None:
        """Test stopping the publisher."""
        publisher = NatsStreamPublisher(config, schema, connection=mock_connection)
        await publisher.start()
        assert publisher.is_running

        await publisher.stop()
        assert not publisher.is_running

    async def test_stop_idempotent(
        self, config: NatsConfig, schema: StreamSchema, mock_connection: MagicMock
    ) -> None:
        """Test that calling stop twice is idempotent."""
        publisher = NatsStreamPublisher(config, schema, connection=mock_connection)
        await publisher.start()
        await publisher.stop()
        await publisher.stop()  # Should not raise

    async def test_publish_data(
        self,
        config: NatsConfig,
        schema: StreamSchema,
        sample_data: StreamData,
        mock_connection: MagicMock,
    ) -> None:
        """Test publishing data."""
        publisher = NatsStreamPublisher(config, schema, connection=mock_connection)
        await publisher.start()

        await publisher.publish(sample_data)

        mock_connection.jetstream.publish.assert_called()
        call_args = mock_connection.jetstream.publish.call_args
        assert call_args[0][0] == "telemetry.test_sensor.data"

        await publisher.stop()

    async def test_publish_not_running(
        self,
        config: NatsConfig,
        schema: StreamSchema,
        sample_data: StreamData,
    ) -> None:
        """Test publishing when not running raises error."""
        publisher = NatsStreamPublisher(config, schema)

        with pytest.raises(NatsConnectionError, match="not running"):
            await publisher.publish(sample_data)

    async def test_publish_schema_mismatch(
        self, config: NatsConfig, schema: StreamSchema, mock_connection: MagicMock
    ) -> None:
        """Test publishing data with wrong schema raises error."""
        publisher = NatsStreamPublisher(config, schema, connection=mock_connection)
        await publisher.start()

        wrong_data = StreamData(
            schema_id=0xDEADBEEF,
            timestamp_ns=1000000000,
            period_ns=1000000,
            samples=((1.0,),),
        )

        with pytest.raises(ValueError, match="Schema ID mismatch"):
            await publisher.publish(wrong_data)

        await publisher.stop()

    async def test_context_manager(
        self, config: NatsConfig, schema: StreamSchema, mock_connection: MagicMock
    ) -> None:
        """Test async context manager."""
        async with NatsStreamPublisher(config, schema, connection=mock_connection) as publisher:
            assert publisher.is_running

        assert not publisher.is_running

    async def test_schema_broadcast(
        self, config: NatsConfig, schema: StreamSchema, mock_connection: MagicMock
    ) -> None:
        """Test schema is broadcast periodically."""
        import asyncio

        publisher = NatsStreamPublisher(config, schema, connection=mock_connection)
        await publisher.start()

        # Wait for at least one schema broadcast
        await asyncio.sleep(0.15)

        await publisher.stop()

        # Check that schema was published (at least once)
        calls = mock_connection.jetstream.publish.call_args_list
        schema_calls = [c for c in calls if "schema" in c[0][0]]
        assert len(schema_calls) >= 1
