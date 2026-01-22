"""Environmental state management interfaces."""

# pylint: disable=unnecessary-ellipsis  # Ellipsis required for Protocol method stubs

from __future__ import annotations

from typing import AsyncIterator, Protocol

from hwtest_core.types.state import EnvironmentalState, StateTransition


class StatePublisher(Protocol):
    """Interface for publishing environmental state changes.

    Implementations are responsible for:
    - Managing the current environmental state
    - Broadcasting state changes to subscribers
    """

    async def set_state(self, state: EnvironmentalState, reason: str = "") -> None:
        """Transition to a new environmental state.

        Args:
            state: The new environmental state.
            reason: Optional reason for the state change.

        Raises:
            TelemetryConnectionError: If not connected to the server.
            StateError: If the state transition is invalid.
        """
        ...

    async def get_current_state(self) -> EnvironmentalState:
        """Get the current environmental state.

        Returns:
            The current environmental state.

        Raises:
            StateError: If no state has been set.
        """
        ...

    async def connect(self) -> None:
        """Establish connection to the telemetry server."""
        ...

    async def disconnect(self) -> None:
        """Disconnect from the telemetry server."""
        ...

    @property
    def is_connected(self) -> bool:
        """Return True if connected to the server."""
        ...


class StateSubscriber(Protocol):
    """Interface for receiving environmental state changes.

    Implementations are responsible for:
    - Subscribing to state change notifications
    - Providing state transitions to the consumer
    """

    async def subscribe(self) -> None:
        """Subscribe to state changes.

        Raises:
            TelemetryConnectionError: If not connected to the server.
        """
        ...

    async def get_current_state(self) -> EnvironmentalState:
        """Get the current environmental state.

        Returns:
            The current environmental state.

        Raises:
            StateError: If no state has been received.
        """
        ...

    def transitions(self) -> AsyncIterator[StateTransition]:
        """Async iterator over state transitions.

        Yields:
            StateTransition objects as they occur.
        """
        ...

    async def unsubscribe(self) -> None:
        """Unsubscribe from state changes."""
        ...

    async def connect(self) -> None:
        """Establish connection to the telemetry server."""
        ...

    async def disconnect(self) -> None:
        """Disconnect from the telemetry server."""
        ...

    @property
    def is_connected(self) -> bool:
        """Return True if connected to the server."""
        ...
