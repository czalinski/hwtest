"""Unit tests for NATS connection management."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hwtest_nats.config import NatsConfig
from hwtest_nats.connection import NatsConnection, NatsConnectionError


class TestNatsConnection:
    """Tests for NatsConnection."""

    @pytest.fixture
    def config(self) -> NatsConfig:
        """Create a test configuration."""
        return NatsConfig(servers=("nats://localhost:4222",))

    @pytest.fixture
    def mock_nats_client(self) -> MagicMock:
        """Create a mock NATS client."""
        client = MagicMock()
        client.is_connected = True
        client.jetstream.return_value = MagicMock()
        client.drain = AsyncMock()
        return client

    def test_initial_state(self, config: NatsConfig) -> None:
        """Test initial connection state."""
        conn = NatsConnection(config)

        assert conn.config == config
        assert not conn.is_connected

    def test_client_not_connected(self, config: NatsConfig) -> None:
        """Test accessing client when not connected raises error."""
        conn = NatsConnection(config)

        with pytest.raises(NatsConnectionError, match="Not connected"):
            _ = conn.client

    def test_jetstream_not_connected(self, config: NatsConfig) -> None:
        """Test accessing jetstream when not connected raises error."""
        conn = NatsConnection(config)

        with pytest.raises(NatsConnectionError, match="Not connected"):
            _ = conn.jetstream

    @patch("hwtest_nats.connection.nats.connect")
    async def test_connect_success(
        self, mock_connect: AsyncMock, config: NatsConfig, mock_nats_client: MagicMock
    ) -> None:
        """Test successful connection."""
        mock_connect.return_value = mock_nats_client

        conn = NatsConnection(config)
        await conn.connect()

        assert conn.is_connected
        mock_connect.assert_called_once()

    @patch("hwtest_nats.connection.nats.connect")
    async def test_connect_already_connected(
        self, mock_connect: AsyncMock, config: NatsConfig, mock_nats_client: MagicMock
    ) -> None:
        """Test connect when already connected does nothing."""
        mock_connect.return_value = mock_nats_client

        conn = NatsConnection(config)
        await conn.connect()
        await conn.connect()  # Second call should be no-op

        mock_connect.assert_called_once()

    @patch("hwtest_nats.connection.nats.connect")
    async def test_connect_with_auth_user_password(
        self, mock_connect: AsyncMock, mock_nats_client: MagicMock
    ) -> None:
        """Test connection with user/password authentication."""
        mock_connect.return_value = mock_nats_client
        config = NatsConfig(user="admin", password="secret")

        conn = NatsConnection(config)
        await conn.connect()

        call_kwargs = mock_connect.call_args.kwargs
        assert call_kwargs["user"] == "admin"
        assert call_kwargs["password"] == "secret"

    @patch("hwtest_nats.connection.nats.connect")
    async def test_connect_with_token(
        self, mock_connect: AsyncMock, mock_nats_client: MagicMock
    ) -> None:
        """Test connection with token authentication."""
        mock_connect.return_value = mock_nats_client
        config = NatsConfig(token="mytoken")

        conn = NatsConnection(config)
        await conn.connect()

        call_kwargs = mock_connect.call_args.kwargs
        assert call_kwargs["token"] == "mytoken"

    @patch("hwtest_nats.connection.nats.connect")
    async def test_disconnect(
        self, mock_connect: AsyncMock, config: NatsConfig, mock_nats_client: MagicMock
    ) -> None:
        """Test disconnection."""
        mock_connect.return_value = mock_nats_client

        conn = NatsConnection(config)
        await conn.connect()
        await conn.disconnect()

        assert not conn.is_connected
        mock_nats_client.drain.assert_called_once()

    @patch("hwtest_nats.connection.nats.connect")
    async def test_disconnect_not_connected(
        self, mock_connect: AsyncMock, config: NatsConfig
    ) -> None:
        """Test disconnect when not connected does nothing."""
        conn = NatsConnection(config)
        await conn.disconnect()  # Should not raise

    @patch("hwtest_nats.connection.nats.connect")
    async def test_context_manager(
        self, mock_connect: AsyncMock, config: NatsConfig, mock_nats_client: MagicMock
    ) -> None:
        """Test async context manager."""
        mock_connect.return_value = mock_nats_client

        async with NatsConnection(config) as conn:
            assert conn.is_connected

        mock_nats_client.drain.assert_called_once()

    @patch("hwtest_nats.connection.nats.connect")
    async def test_ensure_stream_exists(
        self, mock_connect: AsyncMock, config: NatsConfig, mock_nats_client: MagicMock
    ) -> None:
        """Test ensure_stream when stream already exists."""
        mock_js = MagicMock()
        mock_js.stream_info = AsyncMock()
        mock_nats_client.jetstream.return_value = mock_js
        mock_connect.return_value = mock_nats_client

        conn = NatsConnection(config)
        await conn.connect()
        await conn.ensure_stream()

        mock_js.stream_info.assert_called_once_with(config.stream_name)
        mock_js.add_stream.assert_not_called()

    @patch("hwtest_nats.connection.nats.connect")
    async def test_ensure_stream_creates(
        self, mock_connect: AsyncMock, config: NatsConfig, mock_nats_client: MagicMock
    ) -> None:
        """Test ensure_stream creates stream when not found."""
        import nats.js.errors

        mock_js = MagicMock()
        mock_js.stream_info = AsyncMock(side_effect=nats.js.errors.NotFoundError)
        mock_js.add_stream = AsyncMock()
        mock_nats_client.jetstream.return_value = mock_js
        mock_connect.return_value = mock_nats_client

        conn = NatsConnection(config)
        await conn.connect()
        await conn.ensure_stream()

        mock_js.add_stream.assert_called_once()
        call_kwargs = mock_js.add_stream.call_args.kwargs
        assert call_kwargs["name"] == config.stream_name
        assert f"{config.subject_prefix}.>" in call_kwargs["subjects"]

    async def test_ensure_stream_not_connected(self, config: NatsConfig) -> None:
        """Test ensure_stream raises when not connected."""
        conn = NatsConnection(config)

        with pytest.raises(NatsConnectionError, match="Not connected"):
            await conn.ensure_stream()

    @patch("hwtest_nats.connection.nats.connect")
    async def test_wait_connected(
        self, mock_connect: AsyncMock, config: NatsConfig, mock_nats_client: MagicMock
    ) -> None:
        """Test wait_connected after successful connection."""
        mock_connect.return_value = mock_nats_client

        conn = NatsConnection(config)
        await conn.connect()
        await conn.wait_connected(timeout=1.0)  # Should not raise

    async def test_wait_connected_timeout(self, config: NatsConfig) -> None:
        """Test wait_connected times out when not connected."""
        conn = NatsConnection(config)

        with pytest.raises(TimeoutError, match="Timed out waiting"):
            await conn.wait_connected(timeout=0.01)
