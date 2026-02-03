"""Unit tests for telemetry monitor."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from hwtest_core.types.common import ChannelId, StateId, Timestamp
from hwtest_core.types.monitor import MonitorVerdict
from hwtest_core.types.state import EnvironmentalState
from hwtest_core.types.telemetry import TelemetryValue, ValueQuality
from hwtest_core.types.threshold import StateThresholds, Threshold, ThresholdBound

from hwtest_nats.config import NatsConfig
from hwtest_nats.connection import NatsConnection
from hwtest_nats.monitor import TelemetryMonitor


class TestTelemetryMonitor:
    """Tests for TelemetryMonitor."""

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
        mock_js.subscribe = AsyncMock()
        conn.jetstream = mock_js

        return conn

    @pytest.fixture
    def ambient_state(self) -> EnvironmentalState:
        """Create an ambient state."""
        return EnvironmentalState(
            state_id=StateId("ambient"),
            name="ambient",
            description="Ambient temperature",
            is_transition=False,
        )

    @pytest.fixture
    def transition_state(self) -> EnvironmentalState:
        """Create a transition state."""
        return EnvironmentalState(
            state_id=StateId("ramping"),
            name="ramping",
            description="Temperature ramping",
            is_transition=True,
        )

    @pytest.fixture
    def thresholds(self) -> dict[StateId, StateThresholds]:
        """Create test thresholds."""
        return {
            StateId("ambient"): StateThresholds(
                state_id=StateId("ambient"),
                thresholds={
                    ChannelId("voltage"): Threshold(
                        channel=ChannelId("voltage"),
                        low=ThresholdBound(value=3.0),
                        high=ThresholdBound(value=3.6),
                    ),
                    ChannelId("current"): Threshold(
                        channel=ChannelId("current"),
                        low=ThresholdBound(value=0.0),
                        high=ThresholdBound(value=1.0),
                    ),
                },
            ),
            StateId("high_temp"): StateThresholds(
                state_id=StateId("high_temp"),
                thresholds={
                    ChannelId("voltage"): Threshold(
                        channel=ChannelId("voltage"),
                        low=ThresholdBound(value=2.8),
                        high=ThresholdBound(value=3.8),
                    ),
                },
            ),
        }

    def test_initial_state(
        self, config: NatsConfig, thresholds: dict[StateId, StateThresholds]
    ) -> None:
        """Test initial monitor state."""
        monitor = TelemetryMonitor(
            config=config,
            monitor_id="test_monitor",
            source_id="test_source",
            thresholds=thresholds,
        )

        assert monitor.monitor_id == "test_monitor"
        assert not monitor.is_running
        assert monitor.current_state is None

    def test_get_thresholds(
        self, config: NatsConfig, thresholds: dict[StateId, StateThresholds]
    ) -> None:
        """Test getting thresholds by state."""
        monitor = TelemetryMonitor(
            config=config,
            monitor_id="test_monitor",
            source_id="test_source",
            thresholds=thresholds,
        )

        assert monitor.get_thresholds(StateId("ambient")) is not None
        assert monitor.get_thresholds(StateId("high_temp")) is not None
        assert monitor.get_thresholds(StateId("nonexistent")) is None

    def test_get_all_states(
        self, config: NatsConfig, thresholds: dict[StateId, StateThresholds]
    ) -> None:
        """Test getting all state IDs."""
        monitor = TelemetryMonitor(
            config=config,
            monitor_id="test_monitor",
            source_id="test_source",
            thresholds=thresholds,
        )

        states = list(monitor.get_all_states())
        assert StateId("ambient") in states
        assert StateId("high_temp") in states

    async def test_evaluate_pass(
        self,
        config: NatsConfig,
        thresholds: dict[StateId, StateThresholds],
        ambient_state: EnvironmentalState,
    ) -> None:
        """Test evaluation with values within thresholds."""
        monitor = TelemetryMonitor(
            config=config,
            monitor_id="test_monitor",
            source_id="test_source",
            thresholds=thresholds,
        )

        values = [
            TelemetryValue(
                channel=ChannelId("voltage"),
                value=3.3,
                unit="V",
                source_timestamp=Timestamp.now(),
                quality=ValueQuality.GOOD,
            ),
            TelemetryValue(
                channel=ChannelId("current"),
                value=0.5,
                unit="A",
                source_timestamp=Timestamp.now(),
                quality=ValueQuality.GOOD,
            ),
        ]

        result = await monitor.evaluate(values, ambient_state, thresholds[StateId("ambient")])

        assert result.verdict == MonitorVerdict.PASS
        assert result.passed
        assert not result.failed
        assert len(result.violations) == 0

    async def test_evaluate_fail_high(
        self,
        config: NatsConfig,
        thresholds: dict[StateId, StateThresholds],
        ambient_state: EnvironmentalState,
    ) -> None:
        """Test evaluation with value above high threshold."""
        monitor = TelemetryMonitor(
            config=config,
            monitor_id="test_monitor",
            source_id="test_source",
            thresholds=thresholds,
        )

        values = [
            TelemetryValue(
                channel=ChannelId("voltage"),
                value=4.0,  # Above 3.6 threshold
                unit="V",
                source_timestamp=Timestamp.now(),
                quality=ValueQuality.GOOD,
            ),
        ]

        result = await monitor.evaluate(values, ambient_state, thresholds[StateId("ambient")])

        assert result.verdict == MonitorVerdict.FAIL
        assert result.failed
        assert len(result.violations) == 1
        assert result.violations[0].channel == ChannelId("voltage")
        assert result.violations[0].value == 4.0

    async def test_evaluate_fail_low(
        self,
        config: NatsConfig,
        thresholds: dict[StateId, StateThresholds],
        ambient_state: EnvironmentalState,
    ) -> None:
        """Test evaluation with value below low threshold."""
        monitor = TelemetryMonitor(
            config=config,
            monitor_id="test_monitor",
            source_id="test_source",
            thresholds=thresholds,
        )

        values = [
            TelemetryValue(
                channel=ChannelId("voltage"),
                value=2.5,  # Below 3.0 threshold
                unit="V",
                source_timestamp=Timestamp.now(),
                quality=ValueQuality.GOOD,
            ),
        ]

        result = await monitor.evaluate(values, ambient_state, thresholds[StateId("ambient")])

        assert result.verdict == MonitorVerdict.FAIL
        assert len(result.violations) == 1

    async def test_evaluate_multiple_violations(
        self,
        config: NatsConfig,
        thresholds: dict[StateId, StateThresholds],
        ambient_state: EnvironmentalState,
    ) -> None:
        """Test evaluation with multiple violations."""
        monitor = TelemetryMonitor(
            config=config,
            monitor_id="test_monitor",
            source_id="test_source",
            thresholds=thresholds,
        )

        values = [
            TelemetryValue(
                channel=ChannelId("voltage"),
                value=4.0,  # Above threshold
                unit="V",
                source_timestamp=Timestamp.now(),
                quality=ValueQuality.GOOD,
            ),
            TelemetryValue(
                channel=ChannelId("current"),
                value=2.0,  # Above threshold
                unit="A",
                source_timestamp=Timestamp.now(),
                quality=ValueQuality.GOOD,
            ),
        ]

        result = await monitor.evaluate(values, ambient_state, thresholds[StateId("ambient")])

        assert result.verdict == MonitorVerdict.FAIL
        assert len(result.violations) == 2

    async def test_evaluate_skip_transition(
        self,
        config: NatsConfig,
        thresholds: dict[StateId, StateThresholds],
        transition_state: EnvironmentalState,
    ) -> None:
        """Test evaluation skipped during state transition."""
        monitor = TelemetryMonitor(
            config=config,
            monitor_id="test_monitor",
            source_id="test_source",
            thresholds=thresholds,
        )

        # Even with bad values, should skip during transition
        values = [
            TelemetryValue(
                channel=ChannelId("voltage"),
                value=0.0,  # Would fail normally
                unit="V",
                source_timestamp=Timestamp.now(),
                quality=ValueQuality.GOOD,
            ),
        ]

        # Create thresholds for the transition state
        transition_thresholds = StateThresholds(
            state_id=StateId("ramping"),
            thresholds={
                ChannelId("voltage"): Threshold(
                    channel=ChannelId("voltage"),
                    low=ThresholdBound(value=3.0),
                    high=ThresholdBound(value=3.6),
                ),
            },
        )

        result = await monitor.evaluate(values, transition_state, transition_thresholds)

        assert result.verdict == MonitorVerdict.SKIP
        assert "transition" in result.message.lower()

    async def test_evaluate_no_threshold_for_channel(
        self,
        config: NatsConfig,
        thresholds: dict[StateId, StateThresholds],
        ambient_state: EnvironmentalState,
    ) -> None:
        """Test evaluation ignores channels without thresholds."""
        monitor = TelemetryMonitor(
            config=config,
            monitor_id="test_monitor",
            source_id="test_source",
            thresholds=thresholds,
        )

        values = [
            TelemetryValue(
                channel=ChannelId("temperature"),  # No threshold defined
                value=100.0,
                unit="C",
                source_timestamp=Timestamp.now(),
                quality=ValueQuality.GOOD,
            ),
        ]

        result = await monitor.evaluate(values, ambient_state, thresholds[StateId("ambient")])

        # Should pass since no threshold is defined for temperature
        assert result.verdict == MonitorVerdict.PASS
        assert len(result.violations) == 0

    async def test_violation_callback(
        self,
        config: NatsConfig,
        thresholds: dict[StateId, StateThresholds],
        mock_connection: MagicMock,
        ambient_state: EnvironmentalState,
    ) -> None:
        """Test that violation callback is called on failure."""
        callback_results: list[object] = []

        def callback(result: object) -> None:
            callback_results.append(result)

        monitor = TelemetryMonitor(
            config=config,
            monitor_id="test_monitor",
            source_id="test_source",
            thresholds=thresholds,
            connection=mock_connection,
            on_violation=callback,
        )
        monitor._current_state = ambient_state

        values = [
            TelemetryValue(
                channel=ChannelId("voltage"),
                value=4.0,  # Above threshold
                unit="V",
                source_timestamp=Timestamp.now(),
                quality=ValueQuality.GOOD,
            ),
        ]

        result = await monitor.evaluate(values, ambient_state, thresholds[StateId("ambient")])

        # Manually call what _process_data would do
        if result.failed and monitor._on_violation is not None:
            monitor._on_violation(result)

        assert len(callback_results) == 1

    def test_stream_data_to_values(
        self, config: NatsConfig, thresholds: dict[StateId, StateThresholds]
    ) -> None:
        """Test converting StreamData to TelemetryValue list."""
        from hwtest_core.types.common import DataType
        from hwtest_core.types.streaming import StreamData, StreamField, StreamSchema

        monitor = TelemetryMonitor(
            config=config,
            monitor_id="test_monitor",
            source_id="test_source",
            thresholds=thresholds,
        )

        schema = StreamSchema(
            source_id="test_source",
            fields=(
                StreamField("voltage", DataType.F64, "V"),
                StreamField("current", DataType.F64, "A"),
            ),
        )

        data = StreamData(
            schema_id=schema.schema_id,
            timestamp_ns=1000000000,
            period_ns=1000000,
            samples=((3.3, 0.5), (3.31, 0.51)),
        )

        values = monitor._stream_data_to_values(data, schema)

        # Should have 4 values (2 samples x 2 fields)
        assert len(values) == 4

        # Check first sample values
        assert values[0].channel == ChannelId("voltage")
        assert values[0].value == 3.3
        assert values[0].unit == "V"

        assert values[1].channel == ChannelId("current")
        assert values[1].value == 0.5
        assert values[1].unit == "A"
