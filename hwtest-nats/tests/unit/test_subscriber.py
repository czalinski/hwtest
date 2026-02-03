"""Unit tests for NATS stream subscriber."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hwtest_core.types.common import DataType
from hwtest_core.types.streaming import StreamData, StreamField, StreamSchema

# DataType uses F64 not FLOAT64

from hwtest_nats.config import NatsConfig
from hwtest_nats.connection import NatsConnection, NatsConnectionError
from hwtest_nats.subscriber import NatsStreamSubscriber


class TestNatsStreamSubscriber:
    """Tests for NatsStreamSubscriber."""

    @pytest.fixture
    def config(self) -> NatsConfig:
        """Create a test configuration."""
        return NatsConfig(servers=("nats://localhost:4222",))

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
    def mock_connection(self) -> MagicMock:
        """Create a mock NATS connection."""
        conn = MagicMock(spec=NatsConnection)
        conn.is_connected = True
        conn.connect = AsyncMock()
        conn.disconnect = AsyncMock()

        mock_js = MagicMock()
        mock_subscription = MagicMock()
        mock_subscription.unsubscribe = AsyncMock()
        mock_js.subscribe = AsyncMock(return_value=mock_subscription)
        conn.jetstream = mock_js

        return conn

    def test_initial_state(self, config: NatsConfig) -> None:
        """Test initial subscriber state."""
        subscriber = NatsStreamSubscriber(config)

        assert subscriber.schema is None
        assert not subscriber.is_connected

    async def test_connect_creates_connection(self, config: NatsConfig) -> None:
        """Test connect creates connection when not provided."""
        with patch("hwtest_nats.subscriber.NatsConnection") as mock_conn_class:
            mock_conn = MagicMock()
            mock_conn.connect = AsyncMock()
            mock_conn.disconnect = AsyncMock()
            mock_conn.is_connected = True
            mock_conn_class.return_value = mock_conn

            subscriber = NatsStreamSubscriber(config)
            await subscriber.connect()

            mock_conn_class.assert_called_once_with(config)
            mock_conn.connect.assert_called_once()

            await subscriber.disconnect()

    async def test_connect_with_shared_connection(
        self, config: NatsConfig, mock_connection: MagicMock
    ) -> None:
        """Test connect with shared connection."""
        subscriber = NatsStreamSubscriber(config, connection=mock_connection)
        await subscriber.connect()

        assert subscriber.is_connected
        mock_connection.connect.assert_not_called()  # Shared connection not managed

    async def test_subscribe(self, config: NatsConfig, mock_connection: MagicMock) -> None:
        """Test subscribing to a source."""
        subscriber = NatsStreamSubscriber(config, connection=mock_connection)
        await subscriber.subscribe("test_sensor")

        mock_connection.jetstream.subscribe.assert_called_once()
        call_args = mock_connection.jetstream.subscribe.call_args
        assert call_args[0][0] == "telemetry.test_sensor.>"

    async def test_subscribe_not_connected(self, config: NatsConfig) -> None:
        """Test subscribing when not connected raises error."""
        subscriber = NatsStreamSubscriber(config)

        with pytest.raises(NatsConnectionError, match="Not connected"):
            await subscriber.subscribe("test_sensor")

    async def test_subscribe_already_subscribed(
        self, config: NatsConfig, mock_connection: MagicMock
    ) -> None:
        """Test subscribing when already subscribed raises error."""
        subscriber = NatsStreamSubscriber(config, connection=mock_connection)
        await subscriber.subscribe("test_sensor")

        with pytest.raises(RuntimeError, match="Already subscribed"):
            await subscriber.subscribe("other_sensor")

        await subscriber.unsubscribe()

    async def test_get_schema_not_subscribed(self, config: NatsConfig) -> None:
        """Test get_schema when not subscribed raises error."""
        subscriber = NatsStreamSubscriber(config)

        with pytest.raises(RuntimeError, match="Not subscribed"):
            await subscriber.get_schema()

    async def test_get_schema_timeout(self, config: NatsConfig, mock_connection: MagicMock) -> None:
        """Test get_schema times out when no schema received."""
        subscriber = NatsStreamSubscriber(config, connection=mock_connection)
        await subscriber.subscribe("test_sensor")

        with pytest.raises(TimeoutError, match="Timed out waiting"):
            await subscriber.get_schema(timeout=0.01)

        await subscriber.unsubscribe()

    async def test_get_schema_returns_cached(
        self, config: NatsConfig, schema: StreamSchema, mock_connection: MagicMock
    ) -> None:
        """Test get_schema returns cached schema immediately."""
        subscriber = NatsStreamSubscriber(config, connection=mock_connection)
        await subscriber.subscribe("test_sensor")

        # Simulate receiving a schema message
        schema_bytes = schema.to_bytes()
        await subscriber._handle_schema_message(schema_bytes)

        # Should return immediately
        result = await subscriber.get_schema(timeout=0.01)
        assert result == schema

        await subscriber.unsubscribe()

    async def test_unsubscribe(self, config: NatsConfig, mock_connection: MagicMock) -> None:
        """Test unsubscribing."""
        subscriber = NatsStreamSubscriber(config, connection=mock_connection)
        await subscriber.subscribe("test_sensor")
        await subscriber.unsubscribe()

        assert subscriber.schema is None

    async def test_unsubscribe_not_subscribed(
        self, config: NatsConfig, mock_connection: MagicMock
    ) -> None:
        """Test unsubscribe when not subscribed does nothing."""
        subscriber = NatsStreamSubscriber(config, connection=mock_connection)
        await subscriber.unsubscribe()  # Should not raise

    async def test_handle_schema_message(
        self, config: NatsConfig, schema: StreamSchema, mock_connection: MagicMock
    ) -> None:
        """Test handling schema messages."""
        subscriber = NatsStreamSubscriber(config, connection=mock_connection)
        await subscriber.subscribe("test_sensor")

        schema_bytes = schema.to_bytes()
        await subscriber._handle_schema_message(schema_bytes)

        assert subscriber.schema == schema

        await subscriber.unsubscribe()

    async def test_handle_data_message(
        self, config: NatsConfig, schema: StreamSchema, mock_connection: MagicMock
    ) -> None:
        """Test handling data messages."""
        subscriber = NatsStreamSubscriber(config, connection=mock_connection)
        await subscriber.subscribe("test_sensor")

        # First receive schema
        await subscriber._handle_schema_message(schema.to_bytes())

        # Then receive data
        data = StreamData(
            schema_id=schema.schema_id,
            timestamp_ns=1000000000,
            period_ns=1000000,
            samples=((3.3, 0.1), (3.31, 0.11)),
        )
        data_bytes = data.to_bytes(schema)
        await subscriber._handle_data_message(data_bytes)

        # Data should be in the queue
        assert not subscriber._data_queue.empty()
        queued_data = await subscriber._data_queue.get()
        assert queued_data.sample_count == 2

        await subscriber.unsubscribe()

    async def test_handle_data_message_no_schema(
        self, config: NatsConfig, schema: StreamSchema, mock_connection: MagicMock
    ) -> None:
        """Test data messages are discarded when no schema."""
        subscriber = NatsStreamSubscriber(config, connection=mock_connection)
        await subscriber.subscribe("test_sensor")

        data = StreamData(
            schema_id=schema.schema_id,
            timestamp_ns=1000000000,
            period_ns=1000000,
            samples=((3.3, 0.1),),
        )
        data_bytes = data.to_bytes(schema)
        await subscriber._handle_data_message(data_bytes)

        # Data should be discarded
        assert subscriber._data_queue.empty()

        await subscriber.unsubscribe()

    async def test_data_iterator(
        self, config: NatsConfig, schema: StreamSchema, mock_connection: MagicMock
    ) -> None:
        """Test data async iterator."""
        subscriber = NatsStreamSubscriber(config, connection=mock_connection)
        await subscriber.subscribe("test_sensor")
        await subscriber._handle_schema_message(schema.to_bytes())

        # Add some data to queue
        data = StreamData(
            schema_id=schema.schema_id,
            timestamp_ns=1000000000,
            period_ns=1000000,
            samples=((3.3, 0.1),),
        )
        await subscriber._data_queue.put(data)

        # Collect data with timeout
        collected: list[StreamData] = []

        async def collect() -> None:
            async for d in subscriber.data():
                collected.append(d)
                break  # Just get one

        await asyncio.wait_for(collect(), timeout=1.0)
        assert len(collected) == 1
        assert collected[0] == data

        await subscriber.unsubscribe()

    async def test_context_manager(self, config: NatsConfig, mock_connection: MagicMock) -> None:
        """Test async context manager."""
        # For context manager, subscriber creates its own connection
        with patch("hwtest_nats.subscriber.NatsConnection") as mock_conn_class:
            mock_conn = MagicMock()
            mock_conn.connect = AsyncMock()
            mock_conn.disconnect = AsyncMock()
            mock_conn.is_connected = True
            mock_conn_class.return_value = mock_conn

            async with NatsStreamSubscriber(config) as subscriber:
                assert subscriber.is_connected

    async def test_message_handler(
        self, config: NatsConfig, schema: StreamSchema, mock_connection: MagicMock
    ) -> None:
        """Test the unified message handler."""
        subscriber = NatsStreamSubscriber(config, connection=mock_connection)
        await subscriber.subscribe("test_sensor")

        # Create mock message
        mock_msg = MagicMock()
        mock_msg.ack = AsyncMock()

        # Test schema message
        mock_msg.data = schema.to_bytes()
        await subscriber._message_handler(mock_msg)
        assert subscriber.schema == schema
        mock_msg.ack.assert_called()

        # Test data message
        data = StreamData(
            schema_id=schema.schema_id,
            timestamp_ns=1000000000,
            period_ns=1000000,
            samples=((3.3, 0.1),),
        )
        mock_msg.data = data.to_bytes(schema)
        mock_msg.ack.reset_mock()
        await subscriber._message_handler(mock_msg)
        mock_msg.ack.assert_called()
        assert not subscriber._data_queue.empty()

        await subscriber.unsubscribe()

    async def test_message_handler_unknown_type(
        self, config: NatsConfig, mock_connection: MagicMock
    ) -> None:
        """Test message handler ignores unknown message types."""
        subscriber = NatsStreamSubscriber(config, connection=mock_connection)
        await subscriber.subscribe("test_sensor")

        mock_msg = MagicMock()
        mock_msg.data = b"\x99\x00\x00\x00"  # Unknown message type
        mock_msg.ack = AsyncMock()

        await subscriber._message_handler(mock_msg)
        mock_msg.ack.assert_called()  # Should still ack

        await subscriber.unsubscribe()
