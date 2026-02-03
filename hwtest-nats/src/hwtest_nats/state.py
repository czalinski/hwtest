"""NATS-based state management."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, AsyncIterator

from hwtest_core.types.common import Timestamp
from hwtest_core.types.state import EnvironmentalState, StateTransition

from hwtest_nats.config import NatsConfig
from hwtest_nats.connection import NatsConnection, NatsConnectionError

if TYPE_CHECKING:
    from nats.aio.msg import Msg

logger = logging.getLogger(__name__)


class StateError(Exception):
    """Raised when state operations fail."""


class NatsStatePublisher:
    """StatePublisher implementation using NATS.

    Manages environmental state and broadcasts changes to subscribers.
    State transitions are published to a dedicated NATS subject.

    Example:
        publisher = NatsStatePublisher(config, states)
        await publisher.connect()

        # Set initial state
        initial = states["ambient"]
        await publisher.set_state(initial, "Test starting")

        # Transition to new state
        stress = states["high_temp"]
        await publisher.set_state(stress, "Beginning stress phase")

        await publisher.disconnect()
    """

    def __init__(
        self,
        config: NatsConfig,
        states: dict[str, EnvironmentalState] | None = None,
        *,
        connection: NatsConnection | None = None,
        state_subject: str = "state",
    ) -> None:
        """Initialize the state publisher.

        Args:
            config: NATS configuration.
            states: Optional predefined states by name.
            connection: Optional shared connection.
            state_subject: Subject for state messages (appended to subject_prefix).
        """
        self._config = config
        self._states = states or {}
        self._connection = connection
        self._owns_connection = connection is None
        self._state_subject = f"{config.subject_prefix}.{state_subject}"
        self._current_state: EnvironmentalState | None = None

    @property
    def is_connected(self) -> bool:
        """Return True if connected to NATS."""
        if self._connection is None:
            return False
        return self._connection.is_connected

    @property
    def current_state(self) -> EnvironmentalState | None:
        """Return the current state, or None if not set."""
        return self._current_state

    async def connect(self) -> None:
        """Connect to NATS.

        Raises:
            NatsConnectionError: If connection fails.
        """
        if self._owns_connection:
            self._connection = NatsConnection(self._config)
            await self._connection.connect()
            await self._connection.ensure_stream()

    async def disconnect(self) -> None:
        """Disconnect from NATS."""
        if self._owns_connection and self._connection is not None:
            await self._connection.disconnect()
            self._connection = None

    async def set_state(self, state: EnvironmentalState, reason: str = "") -> None:
        """Transition to a new environmental state.

        Args:
            state: The new environmental state.
            reason: Optional reason for the state change.

        Raises:
            NatsConnectionError: If not connected to NATS.
            StateError: If the state is invalid.
        """
        if self._connection is None or not self._connection.is_connected:
            raise NatsConnectionError("Not connected to NATS")

        # Create transition record
        from_state = self._current_state.state_id if self._current_state else None
        transition = StateTransition(
            from_state=from_state,
            to_state=state.state_id,
            timestamp=Timestamp.now(),
            reason=reason,
        )

        # Publish transition
        await self._connection.jetstream.publish(
            self._state_subject,
            transition.to_bytes(),
        )

        self._current_state = state
        logger.info(
            "State transition: %s -> %s (%s)",
            from_state,
            state.state_id,
            reason or "no reason",
        )

    async def get_current_state(self) -> EnvironmentalState:
        """Get the current environmental state.

        Returns:
            The current environmental state.

        Raises:
            StateError: If no state has been set.
        """
        if self._current_state is None:
            raise StateError("No state has been set")
        return self._current_state

    def register_state(self, state: EnvironmentalState) -> None:
        """Register a state definition.

        Args:
            state: The state to register.
        """
        self._states[state.name] = state

    def get_state(self, name: str) -> EnvironmentalState | None:
        """Get a registered state by name.

        Args:
            name: The state name.

        Returns:
            The state, or None if not found.
        """
        return self._states.get(name)

    async def __aenter__(self) -> NatsStatePublisher:
        """Enter async context."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exit async context."""
        await self.disconnect()


class NatsStateSubscriber:
    """StateSubscriber implementation using NATS.

    Receives environmental state changes from NATS.

    Example:
        subscriber = NatsStateSubscriber(config)
        await subscriber.connect()
        await subscriber.subscribe()

        async for transition in subscriber.transitions():
            print(f"State changed: {transition.to_state}")

        await subscriber.disconnect()
    """

    def __init__(
        self,
        config: NatsConfig,
        *,
        connection: NatsConnection | None = None,
        state_subject: str = "state",
    ) -> None:
        """Initialize the state subscriber.

        Args:
            config: NATS configuration.
            connection: Optional shared connection.
            state_subject: Subject for state messages.
        """
        self._config = config
        self._connection = connection
        self._owns_connection = connection is None
        self._state_subject = f"{config.subject_prefix}.{state_subject}"
        self._subscription: Any = None
        self._current_state: EnvironmentalState | None = None
        self._states: dict[str, EnvironmentalState] = {}
        self._transition_queue: asyncio.Queue[StateTransition] = asyncio.Queue()

    @property
    def is_connected(self) -> bool:
        """Return True if connected to NATS."""
        if self._connection is None:
            return False
        return self._connection.is_connected

    async def connect(self) -> None:
        """Connect to NATS.

        Raises:
            NatsConnectionError: If connection fails.
        """
        if self._owns_connection:
            self._connection = NatsConnection(self._config)
            await self._connection.connect()

    async def disconnect(self) -> None:
        """Disconnect from NATS."""
        await self.unsubscribe()
        if self._owns_connection and self._connection is not None:
            await self._connection.disconnect()
            self._connection = None

    async def subscribe(self) -> None:
        """Subscribe to state changes.

        Raises:
            NatsConnectionError: If not connected to NATS.
        """
        if self._connection is None or not self._connection.is_connected:
            raise NatsConnectionError("Not connected to NATS")

        if self._subscription is not None:
            return

        js = self._connection.jetstream
        self._subscription = await js.subscribe(
            self._state_subject,
            cb=self._message_handler,
        )
        logger.info("Subscribed to state changes on %s", self._state_subject)

    async def unsubscribe(self) -> None:
        """Unsubscribe from state changes."""
        if self._subscription is not None:
            try:
                await self._subscription.unsubscribe()
            except Exception as e:  # pylint: disable=broad-except
                logger.warning("Error unsubscribing from state: %s", e)
            self._subscription = None

    async def get_current_state(self) -> EnvironmentalState:
        """Get the current environmental state.

        Returns:
            The current environmental state.

        Raises:
            StateError: If no state has been received.
        """
        if self._current_state is None:
            raise StateError("No state has been received")
        return self._current_state

    def register_state(self, state: EnvironmentalState) -> None:
        """Register a state definition for lookup by state_id.

        Args:
            state: The state to register.
        """
        self._states[state.state_id] = state

    async def transitions(self) -> AsyncIterator[StateTransition]:
        """Async iterator over state transitions.

        Yields:
            StateTransition objects as they occur.
        """
        while True:
            try:
                transition = await self._transition_queue.get()
                yield transition
            except asyncio.CancelledError:
                break

    async def _message_handler(self, msg: Msg) -> None:
        """Handle incoming state messages."""
        try:
            transition = StateTransition.from_bytes(msg.data)
            await self._transition_queue.put(transition)

            # Update current state if we have the definition
            if transition.to_state in self._states:
                self._current_state = self._states[transition.to_state]

            logger.debug("Received state transition: %s", transition.to_state)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Failed to parse state message: %s", e)

        try:
            await msg.ack()
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Failed to ack state message: %s", e)

    async def __aenter__(self) -> NatsStateSubscriber:
        """Enter async context."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exit async context."""
        await self.disconnect()
