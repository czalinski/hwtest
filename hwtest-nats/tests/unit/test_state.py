"""Unit tests for NATS state management."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hwtest_core.types.common import StateId
from hwtest_core.types.state import EnvironmentalState

from hwtest_nats.config import NatsConfig
from hwtest_nats.connection import NatsConnection, NatsConnectionError
from hwtest_nats.state import NatsStatePublisher, NatsStateSubscriber, StateError


class TestNatsStatePublisher:
    """Tests for NatsStatePublisher."""

    @pytest.fixture
    def config(self) -> NatsConfig:
        """Create a test configuration."""
        return NatsConfig(servers=("nats://localhost:4222",))

    @pytest.fixture
    def mock_connection(self) -> MagicMock:
        """Create a mock NATS connection."""
        conn = MagicMock(spec=NatsConnection)
        conn.is_connected = True
        conn.connect = AsyncMock()
        conn.disconnect = AsyncMock()
        conn.ensure_stream = AsyncMock()

        mock_js = MagicMock()
        mock_js.publish = AsyncMock()
        conn.jetstream = mock_js

        return conn

    @pytest.fixture
    def ambient_state(self) -> EnvironmentalState:
        """Create an ambient state."""
        return EnvironmentalState(
            state_id=StateId("ambient"),
            name="ambient",
            description="Ambient temperature",
        )

    @pytest.fixture
    def stress_state(self) -> EnvironmentalState:
        """Create a stress state."""
        return EnvironmentalState(
            state_id=StateId("high_temp"),
            name="high_temp",
            description="High temperature stress",
        )

    def test_initial_state(self, config: NatsConfig) -> None:
        """Test initial publisher state."""
        publisher = NatsStatePublisher(config)
        assert not publisher.is_connected
        assert publisher.current_state is None

    def test_register_state(self, config: NatsConfig, ambient_state: EnvironmentalState) -> None:
        """Test registering states."""
        publisher = NatsStatePublisher(config)
        publisher.register_state(ambient_state)

        assert publisher.get_state("ambient") == ambient_state
        assert publisher.get_state("nonexistent") is None

    async def test_connect(self, config: NatsConfig, mock_connection: MagicMock) -> None:
        """Test connecting with shared connection."""
        publisher = NatsStatePublisher(config, connection=mock_connection)
        await publisher.connect()

        assert publisher.is_connected
        mock_connection.connect.assert_not_called()  # Shared connection

    async def test_connect_creates_connection(self, config: NatsConfig) -> None:
        """Test connect creates connection when not provided."""
        with patch("hwtest_nats.state.NatsConnection") as mock_conn_class:
            mock_conn = MagicMock()
            mock_conn.connect = AsyncMock()
            mock_conn.disconnect = AsyncMock()
            mock_conn.ensure_stream = AsyncMock()
            mock_conn.is_connected = True
            mock_conn_class.return_value = mock_conn

            publisher = NatsStatePublisher(config)
            await publisher.connect()

            mock_conn.connect.assert_called_once()
            mock_conn.ensure_stream.assert_called_once()

            await publisher.disconnect()

    async def test_set_state(
        self,
        config: NatsConfig,
        mock_connection: MagicMock,
        ambient_state: EnvironmentalState,
    ) -> None:
        """Test setting state."""
        publisher = NatsStatePublisher(config, connection=mock_connection)
        await publisher.connect()

        await publisher.set_state(ambient_state, "Starting test")

        assert publisher.current_state == ambient_state
        mock_connection.jetstream.publish.assert_called_once()
        call_args = mock_connection.jetstream.publish.call_args
        assert call_args[0][0] == "telemetry.state"

    async def test_set_state_transition(
        self,
        config: NatsConfig,
        mock_connection: MagicMock,
        ambient_state: EnvironmentalState,
        stress_state: EnvironmentalState,
    ) -> None:
        """Test state transition."""
        publisher = NatsStatePublisher(config, connection=mock_connection)
        await publisher.connect()

        await publisher.set_state(ambient_state, "Starting")
        await publisher.set_state(stress_state, "Beginning stress")

        assert publisher.current_state == stress_state
        assert mock_connection.jetstream.publish.call_count == 2

    async def test_set_state_not_connected(
        self, config: NatsConfig, ambient_state: EnvironmentalState
    ) -> None:
        """Test set_state raises when not connected."""
        publisher = NatsStatePublisher(config)

        with pytest.raises(NatsConnectionError, match="Not connected"):
            await publisher.set_state(ambient_state)

    async def test_get_current_state(
        self,
        config: NatsConfig,
        mock_connection: MagicMock,
        ambient_state: EnvironmentalState,
    ) -> None:
        """Test getting current state."""
        publisher = NatsStatePublisher(config, connection=mock_connection)
        await publisher.connect()
        await publisher.set_state(ambient_state)

        state = await publisher.get_current_state()
        assert state == ambient_state

    async def test_get_current_state_not_set(
        self, config: NatsConfig, mock_connection: MagicMock
    ) -> None:
        """Test get_current_state raises when no state set."""
        publisher = NatsStatePublisher(config, connection=mock_connection)
        await publisher.connect()

        with pytest.raises(StateError, match="No state has been set"):
            await publisher.get_current_state()

    async def test_context_manager(self, config: NatsConfig, mock_connection: MagicMock) -> None:
        """Test async context manager."""
        async with NatsStatePublisher(config, connection=mock_connection) as publisher:
            assert publisher.is_connected


class TestNatsStateSubscriber:
    """Tests for NatsStateSubscriber."""

    @pytest.fixture
    def config(self) -> NatsConfig:
        """Create a test configuration."""
        return NatsConfig(servers=("nats://localhost:4222",))

    @pytest.fixture
    def mock_connection(self) -> MagicMock:
        """Create a mock NATS connection."""
        conn = MagicMock(spec=NatsConnection)
        conn.is_connected = True
        conn.connect = AsyncMock()
        conn.disconnect = AsyncMock()

        mock_sub = MagicMock()
        mock_sub.unsubscribe = AsyncMock()

        mock_js = MagicMock()
        mock_js.subscribe = AsyncMock(return_value=mock_sub)
        conn.jetstream = mock_js

        return conn

    @pytest.fixture
    def ambient_state(self) -> EnvironmentalState:
        """Create an ambient state."""
        return EnvironmentalState(
            state_id=StateId("ambient"),
            name="ambient",
            description="Ambient temperature",
        )

    def test_initial_state(self, config: NatsConfig) -> None:
        """Test initial subscriber state."""
        subscriber = NatsStateSubscriber(config)
        assert not subscriber.is_connected

    async def test_connect(self, config: NatsConfig, mock_connection: MagicMock) -> None:
        """Test connecting with shared connection."""
        subscriber = NatsStateSubscriber(config, connection=mock_connection)
        await subscriber.connect()

        assert subscriber.is_connected

    async def test_subscribe(self, config: NatsConfig, mock_connection: MagicMock) -> None:
        """Test subscribing to state changes."""
        subscriber = NatsStateSubscriber(config, connection=mock_connection)
        await subscriber.connect()
        await subscriber.subscribe()

        mock_connection.jetstream.subscribe.assert_called_once()
        call_args = mock_connection.jetstream.subscribe.call_args
        assert call_args[0][0] == "telemetry.state"

    async def test_subscribe_not_connected(self, config: NatsConfig) -> None:
        """Test subscribe raises when not connected."""
        subscriber = NatsStateSubscriber(config)

        with pytest.raises(NatsConnectionError, match="Not connected"):
            await subscriber.subscribe()

    async def test_subscribe_idempotent(
        self, config: NatsConfig, mock_connection: MagicMock
    ) -> None:
        """Test subscribe is idempotent."""
        subscriber = NatsStateSubscriber(config, connection=mock_connection)
        await subscriber.connect()
        await subscriber.subscribe()
        await subscriber.subscribe()  # Second call should be no-op

        mock_connection.jetstream.subscribe.assert_called_once()

    async def test_unsubscribe(self, config: NatsConfig, mock_connection: MagicMock) -> None:
        """Test unsubscribing."""
        subscriber = NatsStateSubscriber(config, connection=mock_connection)
        await subscriber.connect()
        await subscriber.subscribe()
        await subscriber.unsubscribe()

        # Can subscribe again after unsubscribe
        await subscriber.subscribe()

    async def test_get_current_state_not_received(
        self, config: NatsConfig, mock_connection: MagicMock
    ) -> None:
        """Test get_current_state raises when no state received."""
        subscriber = NatsStateSubscriber(config, connection=mock_connection)
        await subscriber.connect()

        with pytest.raises(StateError, match="No state has been received"):
            await subscriber.get_current_state()

    async def test_register_and_get_state(
        self,
        config: NatsConfig,
        mock_connection: MagicMock,
        ambient_state: EnvironmentalState,
    ) -> None:
        """Test registering states for lookup."""
        subscriber = NatsStateSubscriber(config, connection=mock_connection)
        subscriber.register_state(ambient_state)

        # State is registered by state_id
        assert subscriber._states.get("ambient") == ambient_state

    async def test_message_handler(
        self,
        config: NatsConfig,
        mock_connection: MagicMock,
        ambient_state: EnvironmentalState,
    ) -> None:
        """Test message handler processes transitions."""
        from hwtest_core.types.common import Timestamp
        from hwtest_core.types.state import StateTransition

        subscriber = NatsStateSubscriber(config, connection=mock_connection)
        subscriber.register_state(ambient_state)

        # Create a transition message
        transition = StateTransition(
            from_state=None,
            to_state=ambient_state.state_id,
            timestamp=Timestamp.now(),
            reason="Test",
        )

        # Create mock message
        mock_msg = MagicMock()
        mock_msg.data = transition.to_bytes()
        mock_msg.ack = AsyncMock()

        await subscriber._message_handler(mock_msg)

        # Transition should be in queue
        assert not subscriber._transition_queue.empty()
        queued = await subscriber._transition_queue.get()
        assert queued.to_state == ambient_state.state_id

        # Current state should be updated
        current = await subscriber.get_current_state()
        assert current == ambient_state

        mock_msg.ack.assert_called_once()

    async def test_transitions_iterator(
        self, config: NatsConfig, mock_connection: MagicMock
    ) -> None:
        """Test transitions async iterator."""
        from hwtest_core.types.common import Timestamp
        from hwtest_core.types.state import StateTransition

        subscriber = NatsStateSubscriber(config, connection=mock_connection)

        # Add a transition to the queue
        transition = StateTransition(
            from_state=None,
            to_state=StateId("test"),
            timestamp=Timestamp.now(),
            reason="Test",
        )
        await subscriber._transition_queue.put(transition)

        # Collect from iterator
        collected = []

        async def collect() -> None:
            async for t in subscriber.transitions():
                collected.append(t)
                break

        await asyncio.wait_for(collect(), timeout=1.0)
        assert len(collected) == 1
        assert collected[0] == transition

    async def test_context_manager(self, config: NatsConfig, mock_connection: MagicMock) -> None:
        """Test async context manager."""
        async with NatsStateSubscriber(config, connection=mock_connection) as subscriber:
            assert subscriber.is_connected
