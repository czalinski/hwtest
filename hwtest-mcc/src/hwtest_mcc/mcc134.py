"""MCC 134 thermocouple DAQ HAT instrument driver."""

# pylint: disable=broad-exception-caught  # HAT calls may raise unpredictable exceptions

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from hwtest_core.errors import HwtestError
from hwtest_core.interfaces.streaming import StreamPublisher
from hwtest_core.types.common import DataType, InstrumentIdentity, SourceId
from hwtest_core.types.streaming import StreamData, StreamField, StreamSchema

logger = logging.getLogger(__name__)


class ThermocoupleType(Enum):
    """Thermocouple type codes for MCC 134.

    Maps to daqhats.TcTypes values.
    """

    TYPE_J = 0
    TYPE_K = 1
    TYPE_T = 2
    TYPE_E = 3
    TYPE_R = 4
    TYPE_S = 5
    TYPE_B = 6
    TYPE_N = 7
    DISABLED = 255


@dataclass(frozen=True)
class Mcc134Channel:
    """A single MCC 134 thermocouple channel mapping.

    Args:
        id: Physical channel number (0-3).
        name: Logical alias used as the stream field name.
        tc_type: Thermocouple type for this channel.
    """

    id: int
    name: str
    tc_type: ThermocoupleType


@dataclass(frozen=True)
class Mcc134Config:
    """Configuration for an MCC 134 instrument.

    Args:
        address: HAT address on the stack (0-7).
        channels: Enabled channels with their aliases and thermocouple types.
        source_id: Stream source identifier.
        update_interval: Polling interval in seconds (default 1.0).
    """

    address: int
    channels: tuple[Mcc134Channel, ...]
    source_id: str
    update_interval: float = 1.0

    def __post_init__(self) -> None:
        """Validate configuration parameters."""
        if not 0 <= self.address <= 7:
            raise ValueError(f"address must be 0-7, got {self.address}")
        if not self.channels:
            raise ValueError("channels must not be empty")
        if self.update_interval <= 0:
            raise ValueError(f"update_interval must be > 0, got {self.update_interval}")
        seen_ids: set[int] = set()
        seen_names: set[str] = set()
        for ch in self.channels:
            if not 0 <= ch.id <= 3:
                raise ValueError(f"channel id must be 0-3, got {ch.id}")
            if ch.id in seen_ids:
                raise ValueError(f"duplicate channel id: {ch.id}")
            if ch.name in seen_names:
                raise ValueError(f"duplicate channel name: {ch.name}")
            seen_ids.add(ch.id)
            seen_names.add(ch.name)


class Mcc134Instrument:
    """Instrument driver for the MCC 134 thermocouple DAQ HAT.

    Performs periodic polling of thermocouple channels and publishes
    temperature samples as StreamData batches via a StreamPublisher.

    Args:
        config: Instrument configuration.
        publisher: Stream publisher for sending data batches. Optional if only
            using the instrument for identity/status checks without streaming.
    """

    def __init__(
        self, config: Mcc134Config, publisher: StreamPublisher | None = None
    ) -> None:
        self._config = config
        self._publisher = publisher
        self._schema = StreamSchema(
            source_id=SourceId(config.source_id),
            fields=tuple(
                StreamField(ch.name, DataType.F64, "degC") for ch in config.channels
            ),
        )
        self._hat: Any = None
        self._running = False
        self._task: asyncio.Task[None] | None = None

    @property
    def schema(self) -> StreamSchema:
        """The stream schema for this instrument."""
        return self._schema

    @property
    def is_running(self) -> bool:
        """Return True if the instrument is actively polling."""
        return self._running

    def get_identity(self) -> InstrumentIdentity:
        """Return the instrument identity.

        For MCC DAQ HATs, identity information is obtained from the daqhats
        library rather than an ``*IDN?`` query. The HAT must be opened first
        (via :meth:`open` or :meth:`start`) before calling this method.

        Returns:
            Instrument identity with manufacturer, model, serial, and firmware.

        Raises:
            HwtestError: If the HAT has not been opened yet.
        """
        if self._hat is None:
            raise HwtestError("HAT not opened; call open() or start() first")
        serial: str = self._hat.serial()
        return InstrumentIdentity(
            manufacturer="Measurement Computing",
            model="MCC 134",
            serial=serial,
            firmware="",
        )

    def open(self) -> None:
        """Open the HAT and configure channels (synchronous).

        This method opens the HAT for identity queries and manual reads
        without starting the async polling loop. Use :meth:`start` to
        begin automatic polling.

        Raises:
            HwtestError: If the daqhats library is not installed or the
                HAT cannot be opened at the configured address.
        """
        if self._hat is not None:
            return

        try:
            import daqhats  # type: ignore[import-not-found]  # pylint: disable=import-outside-toplevel
        except ImportError as exc:
            raise HwtestError(
                "daqhats library is not installed. Install with: pip install daqhats"
            ) from exc

        try:
            hat = daqhats.mcc134(self._config.address)
        except Exception as exc:
            raise HwtestError(
                f"Failed to open MCC 134 at address {self._config.address}: {exc}"
            ) from exc

        self._hat = hat

        # Configure thermocouple types for each channel
        for ch in self._config.channels:
            try:
                hat.tc_type_write(ch.id, ch.tc_type.value)
            except Exception as exc:
                raise HwtestError(
                    f"Failed to configure thermocouple type for channel {ch.id}: {exc}"
                ) from exc

    def close(self) -> None:
        """Close the HAT connection."""
        self._hat = None

    async def start(self) -> None:
        """Open the HAT and begin periodic temperature polling.

        Raises:
            HwtestError: If the daqhats library is not installed, the
                HAT cannot be opened, or no publisher was configured.
        """
        if self._running:
            return

        if self._publisher is None:
            raise HwtestError("Cannot start streaming without a publisher")

        # Open the HAT if not already opened
        if self._hat is None:
            self.open()

        # Start the polling task
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop polling and release the HAT."""
        if not self._running:
            return

        self._running = False

        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        self._hat = None

    async def _poll_loop(self) -> None:
        """Read temperatures from the HAT and publish StreamData batches."""
        loop = asyncio.get_running_loop()
        interval = self._config.update_interval

        while self._running:
            timestamp_ns = time.time_ns()
            values: list[float] = []

            for ch in self._config.channels:
                try:
                    temp: float = await loop.run_in_executor(
                        None, self._hat.t_in_read, ch.id
                    )
                    values.append(temp)
                except asyncio.CancelledError:
                    return
                except Exception:
                    if self._running:
                        logger.warning(
                            "Error reading temperature from channel %d", ch.id, exc_info=True
                        )
                    return

            if not values:
                continue

            # Single sample with all channel values
            data = StreamData(
                schema_id=self._schema.schema_id,
                timestamp_ns=timestamp_ns,
                period_ns=int(interval * 1_000_000_000),
                samples=(tuple(values),),
            )

            try:
                await self._publisher.publish(data)
            except Exception:
                logger.warning("Error publishing stream data", exc_info=True)

            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break


def create_instrument(
    address: int,
    channels: list[dict[str, Any]],
    source_id: str,
    publisher: StreamPublisher | None = None,
    update_interval: float = 1.0,
) -> Mcc134Instrument:
    """Create an MCC 134 instrument from configuration parameters.

    Standard factory entry point for the test rack and programmatic use.

    Args:
        address: HAT address on the stack (0-7).
        channels: List of channel definitions, each with ``id``, ``name``,
            and ``tc_type`` (thermocouple type name like "TYPE_K").
        source_id: Stream source identifier.
        publisher: Stream publisher for sending data batches. Optional if only
            using the instrument for identity/status checks without streaming.
        update_interval: Polling interval in seconds (default 1.0).

    Returns:
        Configured instrument instance (call ``start()`` to begin polling).
    """
    channel_objs = tuple(
        Mcc134Channel(
            id=ch["id"],
            name=ch["name"],
            tc_type=ThermocoupleType[ch.get("tc_type", "TYPE_K")],
        )
        for ch in channels
    )
    config = Mcc134Config(
        address=address,
        channels=channel_objs,
        source_id=source_id,
        update_interval=update_interval,
    )
    return Mcc134Instrument(config, publisher)
