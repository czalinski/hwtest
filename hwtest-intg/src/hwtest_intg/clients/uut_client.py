"""HTTP client for the UUT simulator REST API.

Provides an async client for controlling the UUT simulator remotely.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    """Health check response from UUT."""

    status: str
    version: str
    uptime_seconds: float


@dataclass
class HeartbeatStatus:
    """CAN heartbeat status from UUT."""

    running: bool
    message_count: int
    arbitration_id: int
    interval_ms: int


@dataclass
class EchoConfig:
    """CAN echo configuration."""

    enabled: bool
    id_offset: int = 0
    filter_ids: list[int] | None = None


@dataclass
class CanMessageData:
    """CAN message data for sending."""

    arbitration_id: int
    data: list[int]
    is_extended_id: bool = False
    is_fd: bool = False


class UutClient:
    """Async HTTP client for the UUT simulator REST API.

    This client provides methods for interacting with the UUT simulator's
    REST API over HTTP.

    Example:
        >>> async with UutClient("http://192.168.68.100:8080") as client:
        ...     health = await client.health()
        ...     print(f"UUT status: {health.status}")
        ...     await client.can_set_echo(EchoConfig(enabled=True, id_offset=0x10))
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize the UUT client.

        Args:
            base_url: Base URL of the UUT simulator (e.g., "http://192.168.68.100:8080").
            timeout: Request timeout in seconds.
            client: Optional httpx client (for testing).
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "UutClient":
        """Async context manager entry."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
            )
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        """Get the HTTP client, raising if not initialized."""
        if self._client is None:
            raise RuntimeError("Client not initialized. Use 'async with UutClient(...) as client:'")
        return self._client

    # -------------------------------------------------------------------------
    # Health and Status
    # -------------------------------------------------------------------------

    async def health(self) -> HealthStatus:
        """Get the health status of the UUT.

        Returns:
            Health status including version and uptime.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = self._get_client()
        response = await client.get("/health")
        response.raise_for_status()
        data = response.json()
        return HealthStatus(
            status=data["status"],
            version=data["version"],
            uptime_seconds=data["uptime_seconds"],
        )

    async def status(self) -> dict[str, Any]:
        """Get the full status of the UUT.

        Returns:
            Full status dictionary.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = self._get_client()
        response = await client.get("/status")
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    # -------------------------------------------------------------------------
    # CAN Operations
    # -------------------------------------------------------------------------

    async def can_send(self, message: CanMessageData) -> None:
        """Send a CAN message from the UUT.

        Args:
            message: CAN message to send.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = self._get_client()
        response = await client.post(
            "/can/send",
            json={
                "message": {
                    "arbitration_id": message.arbitration_id,
                    "data": message.data,
                    "is_extended_id": message.is_extended_id,
                    "is_fd": message.is_fd,
                }
            },
        )
        response.raise_for_status()

    async def can_get_received(self) -> list[dict[str, Any]]:
        """Get received CAN messages from the UUT buffer.

        Returns:
            List of received CAN messages.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = self._get_client()
        response = await client.get("/can/received")
        response.raise_for_status()
        result: list[dict[str, Any]] = response.json()
        return result

    async def can_clear_received(self) -> None:
        """Clear the UUT's received message buffer.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = self._get_client()
        response = await client.delete("/can/received")
        response.raise_for_status()

    async def can_get_echo(self) -> EchoConfig:
        """Get the current CAN echo configuration.

        Returns:
            Current echo configuration.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = self._get_client()
        response = await client.get("/can/echo")
        response.raise_for_status()
        data = response.json()
        return EchoConfig(
            enabled=data["enabled"],
            id_offset=data["id_offset"],
            filter_ids=data.get("filter_ids"),
        )

    async def can_set_echo(self, config: EchoConfig) -> None:
        """Configure CAN echo mode on the UUT.

        Args:
            config: Echo configuration.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = self._get_client()
        response = await client.put(
            "/can/echo",
            json={
                "enabled": config.enabled,
                "id_offset": config.id_offset,
                "filter_ids": config.filter_ids,
            },
        )
        response.raise_for_status()

    async def can_get_heartbeat(self) -> HeartbeatStatus:
        """Get the CAN heartbeat status.

        Returns:
            Heartbeat status including message count.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = self._get_client()
        response = await client.get("/can/heartbeat")
        response.raise_for_status()
        data = response.json()
        return HeartbeatStatus(
            running=data["running"],
            message_count=data["message_count"],
            arbitration_id=data["arbitration_id"],
            interval_ms=data["interval_ms"],
        )

    # -------------------------------------------------------------------------
    # DAC Operations
    # -------------------------------------------------------------------------

    async def dac_write(self, channel: int, voltage: float) -> None:
        """Write a voltage to a DAC channel.

        Args:
            channel: DAC channel (0 or 1).
            voltage: Voltage to write (0-5V).

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = self._get_client()
        response = await client.post(
            "/dac/write",
            json={"channel": channel, "voltage": voltage},
        )
        response.raise_for_status()

    async def dac_read(self, channel: int) -> float:
        """Read the last written voltage from a DAC channel.

        Args:
            channel: DAC channel (0 or 1).

        Returns:
            Last written voltage.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = self._get_client()
        response = await client.get(f"/dac/{channel}")
        response.raise_for_status()
        data = response.json()
        result: float = data["voltage"]
        return result

    # -------------------------------------------------------------------------
    # ADC Operations
    # -------------------------------------------------------------------------

    async def adc_read(self, channel: int) -> float:
        """Read voltage from an ADC channel.

        Args:
            channel: ADC channel (0-7).

        Returns:
            Measured voltage.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = self._get_client()
        response = await client.get(f"/adc/{channel}")
        response.raise_for_status()
        data = response.json()
        result: float = data["voltage"]
        return result

    # -------------------------------------------------------------------------
    # GPIO Operations
    # -------------------------------------------------------------------------

    async def gpio_configure(
        self,
        pin: int,
        direction: str,
        pullup: bool = False,
    ) -> None:
        """Configure a GPIO pin.

        Args:
            pin: Pin number (0-15).
            direction: "input" or "output".
            pullup: Enable pull-up resistor.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = self._get_client()
        response = await client.post(
            "/gpio/configure",
            json={"pin": pin, "direction": direction, "pullup": pullup},
        )
        response.raise_for_status()

    async def gpio_write(self, pin: int, value: bool) -> None:
        """Write a value to a GPIO pin.

        Args:
            pin: Pin number (0-15).
            value: True for high, False for low.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = self._get_client()
        response = await client.post(
            "/gpio/write",
            json={"pin": pin, "value": value},
        )
        response.raise_for_status()

    async def gpio_read(self, pin: int) -> bool:
        """Read a GPIO pin value.

        Args:
            pin: Pin number (0-15).

        Returns:
            True if high, False if low.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = self._get_client()
        response = await client.get(f"/gpio/{pin}")
        response.raise_for_status()
        data = response.json()
        result: bool = data["value"]
        return result
