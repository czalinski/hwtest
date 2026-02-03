"""Telemetry monitor implementation."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Iterable

from hwtest_core.types.common import ChannelId, MonitorId, SourceId, StateId, Timestamp
from hwtest_core.types.monitor import MonitorResult, MonitorVerdict, ThresholdViolation
from hwtest_core.types.state import EnvironmentalState
from hwtest_core.types.streaming import StreamData, StreamSchema
from hwtest_core.types.telemetry import TelemetryValue, ValueQuality
from hwtest_core.types.threshold import StateThresholds, Threshold

from hwtest_nats.config import NatsConfig
from hwtest_nats.connection import NatsConnection, NatsConnectionError
from hwtest_nats.state import NatsStateSubscriber
from hwtest_nats.subscriber import NatsStreamSubscriber

logger = logging.getLogger(__name__)


class TelemetryMonitor:
    """Monitors telemetry data against state-dependent thresholds.

    The monitor:
    - Subscribes to stream data from a source
    - Subscribes to environmental state changes
    - Evaluates data against thresholds for the current state
    - Skips evaluation during state transitions (is_transition=True)
    - Publishes monitor results to NATS

    Example:
        # Define thresholds per state
        thresholds = {
            "ambient": StateThresholds(
                state_id="ambient",
                thresholds={
                    "voltage": Threshold(channel="voltage", low=ThresholdBound(3.0), high=ThresholdBound(3.6)),
                }
            ),
            "high_temp": StateThresholds(
                state_id="high_temp",
                thresholds={
                    "voltage": Threshold(channel="voltage", low=ThresholdBound(2.8), high=ThresholdBound(3.8)),
                }
            ),
        }

        monitor = TelemetryMonitor(
            config=nats_config,
            monitor_id="voltage_monitor",
            source_id="voltage_daq",
            thresholds=thresholds,
        )

        await monitor.start()

        # Monitor runs until stopped
        await asyncio.sleep(60)

        await monitor.stop()
    """

    def __init__(
        self,
        config: NatsConfig,
        monitor_id: MonitorId,
        source_id: str,
        thresholds: dict[StateId, StateThresholds],
        *,
        connection: NatsConnection | None = None,
        result_subject: str = "monitor.results",
        on_violation: Callable[[MonitorResult], None] | None = None,
    ) -> None:
        """Initialize the telemetry monitor.

        Args:
            config: NATS configuration.
            monitor_id: Unique identifier for this monitor.
            source_id: ID of the stream source to monitor.
            thresholds: Thresholds per state ID.
            connection: Optional shared connection.
            result_subject: Subject for publishing results.
            on_violation: Optional callback on threshold violations.
        """
        self._config = config
        self._monitor_id = monitor_id
        self._source_id = source_id
        self._thresholds = thresholds
        self._connection = connection
        self._owns_connection = connection is None
        self._result_subject = f"{config.subject_prefix}.{result_subject}"
        self._on_violation = on_violation

        self._stream_subscriber: NatsStreamSubscriber | None = None
        self._state_subscriber: NatsStateSubscriber | None = None
        self._current_state: EnvironmentalState | None = None
        self._schema: StreamSchema | None = None
        self._running = False
        self._monitor_task: asyncio.Task[None] | None = None

    @property
    def monitor_id(self) -> MonitorId:
        """Unique identifier for this monitor."""
        return self._monitor_id

    @property
    def is_running(self) -> bool:
        """Return True if the monitor is running."""
        return self._running

    @property
    def current_state(self) -> EnvironmentalState | None:
        """Return the current environmental state."""
        return self._current_state

    def get_thresholds(self, state_id: StateId) -> StateThresholds | None:
        """Get thresholds for a state.

        Args:
            state_id: The state ID.

        Returns:
            StateThresholds for the state, or None if not defined.
        """
        return self._thresholds.get(state_id)

    def get_all_states(self) -> Iterable[StateId]:
        """Get all state IDs with defined thresholds."""
        return self._thresholds.keys()

    async def start(self) -> None:
        """Start the monitor.

        Connects to NATS, subscribes to stream and state data,
        and begins continuous evaluation.
        """
        if self._running:
            return

        # Create connection if needed
        if self._owns_connection:
            self._connection = NatsConnection(self._config)
            await self._connection.connect()
            await self._connection.ensure_stream()

        if self._connection is None:
            raise NatsConnectionError("No connection available")

        # Create subscribers with shared connection
        self._stream_subscriber = NatsStreamSubscriber(self._config, connection=self._connection)
        self._state_subscriber = NatsStateSubscriber(self._config, connection=self._connection)

        # Register states for the state subscriber
        for state_id in self._thresholds:
            # Create minimal state for tracking
            state = EnvironmentalState(
                state_id=state_id,
                name=str(state_id),
                description="",
            )
            self._state_subscriber.register_state(state)

        # Subscribe to stream data
        await self._stream_subscriber.subscribe(SourceId(self._source_id))

        # Subscribe to state changes
        await self._state_subscriber.subscribe()

        self._running = True

        # Start monitor loop
        self._monitor_task = asyncio.create_task(self._monitor_loop())

        logger.info("Started monitor %s for source %s", self._monitor_id, self._source_id)

    async def stop(self) -> None:
        """Stop the monitor."""
        if not self._running:
            return

        self._running = False

        # Cancel monitor task
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        # Unsubscribe
        if self._stream_subscriber is not None:
            await self._stream_subscriber.unsubscribe()
        if self._state_subscriber is not None:
            await self._state_subscriber.unsubscribe()

        # Disconnect if we own the connection
        if self._owns_connection and self._connection is not None:
            await self._connection.disconnect()
            self._connection = None

        logger.info("Stopped monitor %s", self._monitor_id)

    async def evaluate(
        self,
        values: Iterable[TelemetryValue],
        state: EnvironmentalState,
        thresholds: StateThresholds,
    ) -> MonitorResult:
        """Evaluate telemetry values against thresholds.

        Args:
            values: Telemetry values to evaluate.
            state: Current environmental state.
            thresholds: Thresholds to evaluate against.

        Returns:
            MonitorResult with verdict and any violations.
        """
        # Skip if in transition state
        if state.is_transition:
            return MonitorResult(
                monitor_id=self._monitor_id,
                verdict=MonitorVerdict.SKIP,
                timestamp=Timestamp.now(),
                state_id=state.state_id,
                message="Skipped during state transition",
            )

        violations: list[ThresholdViolation] = []

        for value in values:
            threshold = thresholds.get_threshold(value.channel)
            if threshold is None:
                continue

            if not threshold.check(value.value):
                violation = ThresholdViolation(
                    channel=value.channel,
                    value=value.value,
                    threshold=threshold,
                    message=f"Value {value.value} outside threshold bounds",
                )
                violations.append(violation)

        if violations:
            result = MonitorResult(
                monitor_id=self._monitor_id,
                verdict=MonitorVerdict.FAIL,
                timestamp=Timestamp.now(),
                state_id=state.state_id,
                violations=tuple(violations),
                message=f"{len(violations)} threshold violation(s)",
            )
        else:
            result = MonitorResult(
                monitor_id=self._monitor_id,
                verdict=MonitorVerdict.PASS,
                timestamp=Timestamp.now(),
                state_id=state.state_id,
            )

        return result

    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        try:
            # Wait for schema first
            if self._stream_subscriber is not None:
                self._schema = await self._stream_subscriber.get_schema(timeout=30.0)
                logger.info(
                    "Monitor %s received schema with %d fields",
                    self._monitor_id,
                    len(self._schema.fields),
                )

            # Start state listener
            state_task = asyncio.create_task(self._state_listener())

            # Process data
            try:
                if self._stream_subscriber is not None:
                    async for data in self._stream_subscriber.data():
                        if not self._running:
                            break
                        await self._process_data(data)
            finally:
                state_task.cancel()
                try:
                    await state_task
                except asyncio.CancelledError:
                    pass

        except asyncio.CancelledError:
            pass
        except TimeoutError:
            logger.error("Monitor %s timed out waiting for schema", self._monitor_id)
        except Exception as e:  # pylint: disable=broad-except
            logger.error("Monitor %s error: %s", self._monitor_id, e)

    async def _state_listener(self) -> None:
        """Listen for state changes."""
        try:
            if self._state_subscriber is not None:
                async for transition in self._state_subscriber.transitions():
                    if not self._running:
                        break

                    # Update current state from registered states
                    state_id = transition.to_state
                    if state_id in self._thresholds:
                        self._current_state = EnvironmentalState(
                            state_id=state_id,
                            name=str(state_id),
                            description="",
                        )
                        logger.info(
                            "Monitor %s: state changed to %s",
                            self._monitor_id,
                            state_id,
                        )
        except asyncio.CancelledError:
            pass

    async def _process_data(self, data: StreamData) -> None:
        """Process received stream data."""
        if self._schema is None:
            return

        # No state set yet, skip
        if self._current_state is None:
            return

        # Get thresholds for current state
        thresholds = self._thresholds.get(self._current_state.state_id)
        if thresholds is None:
            return

        # Convert stream data to telemetry values
        values = self._stream_data_to_values(data, self._schema)

        # Evaluate
        result = await self.evaluate(values, self._current_state, thresholds)

        # Publish result
        await self._publish_result(result)

        # Call violation callback
        if result.failed and self._on_violation is not None:
            self._on_violation(result)

    def _stream_data_to_values(
        self, data: StreamData, schema: StreamSchema
    ) -> list[TelemetryValue]:
        """Convert StreamData to TelemetryValue list."""
        values: list[TelemetryValue] = []

        for i, sample in enumerate(data.samples):
            timestamp = Timestamp(unix_ns=data.get_timestamp(i), source="stream")

            for j, field in enumerate(schema.fields):
                if j < len(sample):
                    value = TelemetryValue(
                        channel=ChannelId(field.name),
                        value=float(sample[j]),
                        unit=field.unit,
                        source_timestamp=timestamp,
                        quality=ValueQuality.GOOD,
                    )
                    values.append(value)

        return values

    async def _publish_result(self, result: MonitorResult) -> None:
        """Publish monitor result to NATS."""
        if self._connection is None or not self._connection.is_connected:
            return

        try:
            await self._connection.jetstream.publish(
                self._result_subject,
                result.to_bytes(),
            )
            logger.debug(
                "Published result: %s (state=%s)",
                result.verdict.value,
                result.state_id,
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Failed to publish result: %s", e)

    async def __aenter__(self) -> TelemetryMonitor:
        """Enter async context."""
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exit async context."""
        await self.stop()
