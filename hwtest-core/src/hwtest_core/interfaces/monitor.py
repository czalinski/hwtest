"""Monitor and threshold provider interfaces.

This module defines protocols for telemetry monitoring and threshold
management. Monitors continuously evaluate measurement data against
state-dependent thresholds to detect out-of-specification conditions.

The monitoring system supports:
- State-dependent thresholds (different limits for different conditions)
- Transition state handling (evaluation suspended during transitions)
- Configurable violation callbacks for real-time alerts

Protocols:
    Monitor: Evaluate telemetry against thresholds.
    ThresholdProvider: Supply thresholds for environmental states.
"""

# pylint: disable=unnecessary-ellipsis  # Ellipsis required for Protocol method stubs

from __future__ import annotations

from typing import Iterable, Protocol

from hwtest_core.types.common import MonitorId, StateId
from hwtest_core.types.monitor import MonitorResult
from hwtest_core.types.state import EnvironmentalState
from hwtest_core.types.telemetry import TelemetryValue
from hwtest_core.types.threshold import StateThresholds


class Monitor(Protocol):
    """Protocol for telemetry monitoring with threshold evaluation.

    Monitors subscribe to telemetry data and continuously evaluate
    measurements against thresholds appropriate for the current
    environmental state. They produce MonitorResult records indicating
    pass/fail status and any threshold violations.

    Typical implementations integrate with NATS for telemetry subscription
    and state tracking.
    """

    @property
    def monitor_id(self) -> MonitorId:
        """Get the unique identifier for this monitor.

        Returns:
            The monitor's unique ID.
        """
        ...

    async def evaluate(
        self,
        values: Iterable[TelemetryValue],
        state: EnvironmentalState,
        thresholds: StateThresholds,
    ) -> MonitorResult:
        """Evaluate telemetry values against thresholds.

        This method performs a single evaluation cycle. For continuous
        monitoring, use start() to begin automatic evaluation.

        Args:
            values: Telemetry values to evaluate.
            state: Current environmental state.
            thresholds: Thresholds to evaluate against.

        Returns:
            MonitorResult with verdict (PASS/FAIL/SKIP/ERROR)
            and any threshold violations.
        """
        ...

    async def start(self) -> None:
        """Start continuous monitoring.

        Begins subscribing to telemetry and evaluating against
        state-dependent thresholds automatically.
        """
        ...

    async def stop(self) -> None:
        """Stop continuous monitoring.

        Stops telemetry subscription and evaluation. Safe to call
        even if not running.
        """
        ...

    @property
    def is_running(self) -> bool:
        """Check if the monitor is running.

        Returns:
            True if continuous monitoring is active.
        """
        ...


class ThresholdProvider(Protocol):
    """Protocol for retrieving state-dependent thresholds.

    Implementations load threshold definitions from various sources:
    - YAML configuration files
    - Database tables
    - Remote configuration services

    The provider is queried by monitors to obtain appropriate
    thresholds for the current environmental state.
    """

    def get_thresholds(self, state_id: StateId) -> StateThresholds | None:
        """Get thresholds for a specific environmental state.

        Args:
            state_id: The state identifier to look up.

        Returns:
            StateThresholds containing channel thresholds for the state,
            or None if no thresholds are defined for this state.
        """
        ...

    def get_all_states(self) -> Iterable[StateId]:
        """Get all state IDs that have defined thresholds.

        Returns:
            Iterable of StateId values for which thresholds exist.
        """
        ...
