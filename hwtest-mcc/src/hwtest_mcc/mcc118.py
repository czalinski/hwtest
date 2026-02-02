"""MCC 118 voltage DAQ HAT instrument driver."""

# pylint: disable=broad-exception-caught  # HAT calls may raise unpredictable exceptions

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from hwtest_core.errors import HwtestError
from hwtest_core.interfaces.streaming import StreamPublisher
from hwtest_core.types.common import DataType, InstrumentIdentity, SourceId
from hwtest_core.types.streaming import StreamData, StreamField, StreamSchema

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Mcc118Channel:
    """A single MCC 118 analog input channel mapping.

    Args:
        id: Physical channel number (0-7).
        name: Logical alias used as the stream field name.
    """

    id: int
    name: str


@dataclass(frozen=True)
class Mcc118Config:
    """Configuration for an MCC 118 instrument.

    Args:
        address: HAT address on the stack (0-7).
        sample_rate: Requested sample rate per channel in Hz.
        channels: Enabled channels with their aliases.
        source_id: Stream source identifier.
    """

    address: int
    sample_rate: float
    channels: tuple[Mcc118Channel, ...]
    source_id: str

    def __post_init__(self) -> None:
        """Validate configuration parameters."""
        if not 0 <= self.address <= 7:
            raise ValueError(f"address must be 0-7, got {self.address}")
        if self.sample_rate <= 0:
            raise ValueError(f"sample_rate must be > 0, got {self.sample_rate}")
        if not self.channels:
            raise ValueError("channels must not be empty")
        seen_ids: set[int] = set()
        seen_names: set[str] = set()
        for ch in self.channels:
            if not 0 <= ch.id <= 7:
                raise ValueError(f"channel id must be 0-7, got {ch.id}")
            if ch.id in seen_ids:
                raise ValueError(f"duplicate channel id: {ch.id}")
            if ch.name in seen_names:
                raise ValueError(f"duplicate channel name: {ch.name}")
            seen_ids.add(ch.id)
            seen_names.add(ch.name)


class Mcc118Instrument:
    """Instrument driver for the MCC 118 voltage DAQ HAT.

    Performs continuous analog input scanning and publishes samples
    as StreamData batches via a StreamPublisher.

    Args:
        config: Instrument configuration.
        publisher: Stream publisher for sending data batches. Optional if only
            using the instrument for identity/status checks without streaming.
    """

    def __init__(
        self, config: Mcc118Config, publisher: StreamPublisher | None = None
    ) -> None:
        self._config = config
        self._publisher = publisher
        self._schema = StreamSchema(
            source_id=SourceId(config.source_id),
            fields=tuple(StreamField(ch.name, DataType.F64, "V") for ch in config.channels),
        )
        self._hat: Any = None
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._actual_sample_rate: float = 0.0

    @property
    def schema(self) -> StreamSchema:
        """The stream schema for this instrument."""
        return self._schema

    @property
    def actual_sample_rate(self) -> float:
        """The actual sample rate as reported by the hardware after scan start."""
        return self._actual_sample_rate

    @property
    def is_running(self) -> bool:
        """Return True if the instrument is actively scanning."""
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
            model="MCC 118",
            serial=serial,
            firmware="",
        )

    def open(self) -> None:
        """Open the HAT (synchronous).

        This method opens the HAT for identity queries without starting
        the continuous scan. Use :meth:`start` to begin scanning.

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
            hat = daqhats.mcc118(self._config.address)
        except Exception as exc:
            raise HwtestError(
                f"Failed to open MCC 118 at address {self._config.address}: {exc}"
            ) from exc

        self._hat = hat

    def close(self) -> None:
        """Close the HAT connection."""
        if self._hat is not None:
            try:
                self._hat.a_in_scan_stop()
                self._hat.a_in_scan_cleanup()
            except Exception:
                pass  # Ignore cleanup errors
            self._hat = None

    def _channel_mask(self) -> int:
        """Compute the channel bitmask for a_in_scan_start."""
        mask = 0
        for ch in self._config.channels:
            mask |= 1 << ch.id
        return mask

    async def start(self) -> None:
        """Open the HAT and begin continuous scanning.

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

        try:
            import daqhats  # type: ignore[import-not-found]  # pylint: disable=import-outside-toplevel
        except ImportError as exc:
            raise HwtestError(
                "daqhats library is not installed. Install with: pip install daqhats"
            ) from exc

        loop = asyncio.get_running_loop()

        channel_mask = self._channel_mask()
        options = daqhats.OptionFlags.CONTINUOUS

        actual_rate: float = await loop.run_in_executor(
            None,
            self._hat.a_in_scan_start,
            channel_mask,
            0,
            self._config.sample_rate,
            options,
        )
        self._actual_sample_rate = actual_rate
        self._running = True
        self._task = asyncio.create_task(self._scan_loop())

    async def stop(self) -> None:
        """Stop scanning and release the HAT."""
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

        if self._hat is not None:
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, self._hat.a_in_scan_stop)
            except Exception:
                logger.warning("Error stopping scan", exc_info=True)
            try:
                await loop.run_in_executor(None, self._hat.a_in_scan_cleanup)
            except Exception:
                logger.warning("Error cleaning up scan", exc_info=True)
            self._hat = None

    def _reshape_samples(
        self, raw_data: list[float], n_channels: int
    ) -> tuple[tuple[float, ...], ...]:
        """Reshape interleaved scan data into per-sample tuples."""
        n_samples = len(raw_data) // n_channels
        samples: list[tuple[float, ...]] = []
        for i in range(n_samples):
            offset = i * n_channels
            samples.append(tuple(raw_data[offset + j] for j in range(n_channels)))
        return tuple(samples)

    async def _scan_loop(self) -> None:
        """Read samples from the HAT and publish StreamData batches."""
        n_channels = len(self._config.channels)
        batch_size = max(1, int(self._actual_sample_rate / 10))
        period_ns = int(1_000_000_000 / self._actual_sample_rate)
        timeout_s = batch_size / self._actual_sample_rate * 2 + 1.0

        start_time_ns = time.time_ns()
        total_samples_read = 0
        loop = asyncio.get_running_loop()

        while self._running:
            try:
                result = await loop.run_in_executor(
                    None, self._hat.a_in_scan_read, batch_size, timeout_s
                )
            except asyncio.CancelledError:
                break
            except Exception:
                if self._running:
                    logger.warning("Error reading scan data", exc_info=True)
                break

            if result.hardware_overrun:
                logger.warning("MCC 118 hardware buffer overrun detected")
            if result.buffer_overrun:
                logger.warning("MCC 118 software buffer overrun detected")

            if not result.data:
                if not result.running:
                    logger.warning("MCC 118 scan stopped unexpectedly")
                    break
                continue

            samples = self._reshape_samples(result.data, n_channels)
            if not samples:
                continue

            data = StreamData(
                schema_id=self._schema.schema_id,
                timestamp_ns=start_time_ns + total_samples_read * period_ns,
                period_ns=period_ns,
                samples=samples,
            )
            total_samples_read += len(samples)

            try:
                await self._publisher.publish(data)
            except Exception:
                logger.warning("Error publishing stream data", exc_info=True)

            if not result.running:
                logger.warning("MCC 118 scan stopped unexpectedly")
                break


def create_instrument(
    address: int,
    sample_rate: float,
    channels: list[dict[str, Any]],
    source_id: str,
    publisher: StreamPublisher | None = None,
) -> Mcc118Instrument:
    """Create an MCC 118 instrument from configuration parameters.

    Standard factory entry point for the test rack and programmatic use.

    Args:
        address: HAT address on the stack (0-7).
        sample_rate: Requested sample rate per channel in Hz.
        channels: List of channel definitions, each with ``id`` and ``name``.
        source_id: Stream source identifier.
        publisher: Stream publisher for sending data batches. Optional if only
            using the instrument for identity/status checks without streaming.

    Returns:
        Configured instrument instance (call ``start()`` to begin scanning).
    """
    channel_objs = tuple(Mcc118Channel(ch["id"], ch["name"]) for ch in channels)
    config = Mcc118Config(
        address=address,
        sample_rate=sample_rate,
        channels=channel_objs,
        source_id=source_id,
    )
    return Mcc118Instrument(config, publisher)
