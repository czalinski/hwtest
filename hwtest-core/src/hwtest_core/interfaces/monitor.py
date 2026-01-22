"""Monitor interfaces."""

# pylint: disable=unnecessary-ellipsis  # Ellipsis required for Protocol method stubs

from __future__ import annotations

from typing import Iterable, Protocol

from hwtest_core.types.common import MonitorId, StateId
from hwtest_core.types.monitor import MonitorResult
from hwtest_core.types.state import EnvironmentalState
from hwtest_core.types.telemetry import TelemetryValue
from hwtest_core.types.threshold import StateThresholds


class Monitor(Protocol):
    """Interface for a telemetry monitor.

    Monitors continuously evaluate telemetry data against thresholds
    for the current environmental state.
    """

    @property
    def monitor_id(self) -> MonitorId:
        """Unique identifier for this monitor."""
        ...

    async def evaluate(
        self,
        values: Iterable[TelemetryValue],
        state: EnvironmentalState,
        thresholds: StateThresholds,
    ) -> MonitorResult:
        """Evaluate telemetry values against thresholds for the given state.

        Args:
            values: Telemetry values to evaluate.
            state: Current environmental state.
            thresholds: Thresholds to evaluate against.

        Returns:
            MonitorResult with verdict and any violations.
        """
        ...

    async def start(self) -> None:
        """Start the monitor (begin continuous evaluation)."""
        ...

    async def stop(self) -> None:
        """Stop the monitor."""
        ...

    @property
    def is_running(self) -> bool:
        """Return True if the monitor is running."""
        ...


class ThresholdProvider(Protocol):
    """Interface for retrieving thresholds by state.

    Implementations may load thresholds from files, databases,
    or other configuration sources.
    """

    def get_thresholds(self, state_id: StateId) -> StateThresholds | None:
        """Get thresholds for a given environmental state.

        Args:
            state_id: The state to get thresholds for.

        Returns:
            StateThresholds for the state, or None if no thresholds defined.
        """
        ...

    def get_all_states(self) -> Iterable[StateId]:
        """Get all state IDs that have defined thresholds.

        Returns:
            Iterable of StateIds with defined thresholds.
        """
        ...
