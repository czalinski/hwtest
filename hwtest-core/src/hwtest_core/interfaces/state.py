"""Environmental state management interfaces.

This module defines protocols for publishing and subscribing to environmental
state changes during HASS/HALT testing. States represent discrete test
conditions (e.g., ambient, thermal stress, vibration).

State management is critical for:
- Applying state-dependent thresholds during monitoring
- Logging state context with telemetry data
- Coordinating test phases across distributed components

Protocols:
    StatePublisher: Manage and broadcast environmental state.
    StateSubscriber: Receive state change notifications.
"""

# pylint: disable=unnecessary-ellipsis  # Ellipsis required for Protocol method stubs

from __future__ import annotations

from typing import AsyncIterator, Protocol

from hwtest_core.types.state import EnvironmentalState, StateTransition


class StatePublisher(Protocol):
    """Protocol for managing and publishing environmental state.

    The publisher is the authoritative source of the current state.
    It broadcasts state transitions to all subscribers and maintains
    the current state for queries.

    Typical implementations use NATS JetStream or similar for
    reliable state distribution.
    """

    async def set_state(self, state: EnvironmentalState, reason: str = "") -> None:
        """Transition to a new environmental state.

        Creates a StateTransition record and broadcasts to subscribers.

        Args:
            state: The new environmental state.
            reason: Optional explanation for the transition.

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
            StateError: If no state has been set yet.
        """
        ...

    async def connect(self) -> None:
        """Establish connection to the telemetry server.

        Raises:
            TelemetryConnectionError: If connection fails.
        """
        ...

    async def disconnect(self) -> None:
        """Disconnect from the telemetry server.

        Safe to call even if not connected.
        """
        ...

    @property
    def is_connected(self) -> bool:
        """Check if connected to the server.

        Returns:
            True if currently connected.
        """
        ...


class StateSubscriber(Protocol):
    """Protocol for receiving environmental state change notifications.

    Subscribers receive the current state on subscription and all
    subsequent state transitions. They can query the current state
    at any time or iterate over transitions asynchronously.

    Typical implementations use NATS JetStream or similar for
    reliable, ordered delivery.
    """

    async def subscribe(self) -> None:
        """Subscribe to state change notifications.

        On subscription, the current state is retrieved if available.

        Raises:
            TelemetryConnectionError: If not connected to the server.
        """
        ...

    async def get_current_state(self) -> EnvironmentalState:
        """Get the current environmental state.

        Returns:
            The most recently received environmental state.

        Raises:
            StateError: If no state has been received yet.
        """
        ...

    def transitions(self) -> AsyncIterator[StateTransition]:
        """Create an async iterator over state transitions.

        Yields:
            StateTransition objects as they are received.

        Note:
            The iterator terminates when unsubscribe() is called
            or the connection is lost.
        """
        ...

    async def unsubscribe(self) -> None:
        """Unsubscribe from state change notifications.

        Safe to call even if not subscribed.
        """
        ...

    async def connect(self) -> None:
        """Establish connection to the telemetry server.

        Raises:
            TelemetryConnectionError: If connection fails.
        """
        ...

    async def disconnect(self) -> None:
        """Disconnect from the telemetry server.

        Also unsubscribes if currently subscribed. Safe to call
        even if not connected.
        """
        ...

    @property
    def is_connected(self) -> bool:
        """Check if connected to the server.

        Returns:
            True if currently connected.
        """
        ...
