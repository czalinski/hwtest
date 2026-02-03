"""NATS connection management."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import nats
from nats.errors import ConnectionClosedError, NoServersError, TimeoutError as NatsTimeoutError
from nats.js import JetStreamContext

from hwtest_nats.config import NatsConfig

if TYPE_CHECKING:
    from nats.aio.client import Client as NatsClient

logger = logging.getLogger(__name__)


class NatsConnectionError(Exception):
    """Raised when NATS connection fails."""


class NatsConnection:
    """Manages NATS connection and JetStream context.

    This class handles connection lifecycle, automatic reconnection,
    and provides access to JetStream for persistent messaging.

    Example:
        async with NatsConnection(config) as conn:
            js = conn.jetstream
            await js.publish("telemetry.sensor", data)
    """

    def __init__(self, config: NatsConfig) -> None:
        """Initialize NATS connection manager.

        Args:
            config: NATS configuration.
        """
        self._config = config
        self._client: NatsClient | None = None
        self._jetstream: JetStreamContext | None = None
        self._connected = asyncio.Event()
        self._closed = False

    @property
    def config(self) -> NatsConfig:
        """Return the configuration."""
        return self._config

    @property
    def client(self) -> NatsClient:
        """Return the NATS client.

        Raises:
            NatsConnectionError: If not connected.
        """
        if self._client is None:
            raise NatsConnectionError("Not connected to NATS")
        return self._client

    @property
    def jetstream(self) -> JetStreamContext:
        """Return the JetStream context.

        Raises:
            NatsConnectionError: If not connected.
        """
        if self._jetstream is None:
            raise NatsConnectionError("Not connected to NATS")
        return self._jetstream

    @property
    def is_connected(self) -> bool:
        """Return True if connected to NATS."""
        return self._client is not None and self._client.is_connected

    async def connect(self) -> None:
        """Connect to NATS servers.

        Raises:
            NatsConnectionError: If connection fails.
        """
        if self._client is not None:
            return

        options: dict[str, Any] = {
            "servers": list(self._config.servers),
            "connect_timeout": self._config.connect_timeout,
            "reconnect_time_wait": self._config.reconnect_time_wait,
            "max_reconnect_attempts": self._config.max_reconnect_attempts,
            "error_cb": self._error_callback,
            "disconnected_cb": self._disconnected_callback,
            "reconnected_cb": self._reconnected_callback,
            "closed_cb": self._closed_callback,
        }

        if self._config.user and self._config.password:
            options["user"] = self._config.user
            options["password"] = self._config.password
        elif self._config.token:
            options["token"] = self._config.token

        try:
            self._client = await nats.connect(**options)
            self._jetstream = self._client.jetstream()
            self._connected.set()
            logger.info("Connected to NATS: %s", self._config.servers)
        except (NoServersError, NatsTimeoutError, OSError) as e:
            raise NatsConnectionError(f"Failed to connect to NATS: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from NATS."""
        if self._client is None:
            return

        self._closed = True
        self._connected.clear()

        try:
            await self._client.drain()
        except (ConnectionClosedError, NatsTimeoutError):
            pass

        self._client = None
        self._jetstream = None
        logger.info("Disconnected from NATS")

    async def ensure_stream(self) -> None:
        """Ensure the telemetry stream exists.

        Creates the JetStream stream if it doesn't exist.
        The stream is configured for telemetry data retention.
        """
        if self._jetstream is None:
            raise NatsConnectionError("Not connected to NATS")

        try:
            await self._jetstream.stream_info(self._config.stream_name)
            logger.debug("Stream %s already exists", self._config.stream_name)
        except nats.js.errors.NotFoundError:
            # Create the stream
            subjects = [f"{self._config.subject_prefix}.>"]
            await self._jetstream.add_stream(
                name=self._config.stream_name,
                subjects=subjects,
                retention="limits",
                max_age=86400_000_000_000,  # 24 hours in nanoseconds
                storage="file",
                discard="old",
            )
            logger.info("Created stream %s with subjects %s", self._config.stream_name, subjects)

    async def wait_connected(self, timeout: float | None = None) -> None:
        """Wait for connection to be established.

        Args:
            timeout: Maximum time to wait in seconds, or None for no timeout.

        Raises:
            TimeoutError: If timeout expires before connection.
        """
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
        except asyncio.TimeoutError as e:
            raise TimeoutError("Timed out waiting for NATS connection") from e

    async def __aenter__(self) -> NatsConnection:
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

    async def _error_callback(self, exc: Exception) -> None:
        """Handle NATS errors."""
        logger.error("NATS error: %s", exc)

    async def _disconnected_callback(self) -> None:
        """Handle disconnection."""
        self._connected.clear()
        if not self._closed:
            logger.warning("Disconnected from NATS, will attempt reconnection")

    async def _reconnected_callback(self) -> None:
        """Handle reconnection."""
        self._connected.set()
        logger.info("Reconnected to NATS")

    async def _closed_callback(self) -> None:
        """Handle connection closed."""
        self._connected.clear()
        logger.info("NATS connection closed")
