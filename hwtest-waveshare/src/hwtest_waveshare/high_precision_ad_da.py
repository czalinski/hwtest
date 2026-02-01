"""High-Precision AD/DA board instrument driver for hwtest.

This module provides a unified instrument driver for the Waveshare High-Precision
AD/DA board, which contains:
- ADS1256: 8-channel 24-bit ADC (30 kSPS)
- DAC8532: 2-channel 16-bit DAC

The instrument exposes channel aliases for both ADC inputs and DAC outputs,
allowing integration with the hwtest rack system.
"""

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

from hwtest_waveshare.ads1256 import (
    Ads1256,
    Ads1256Config,
    Ads1256DataRate,
    Ads1256Gain,
    DATA_RATE_VALUES,
)
from hwtest_waveshare.dac8532 import Dac8532, Dac8532Channel, Dac8532Config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdcChannel:
    """ADC channel mapping.

    Args:
        id: Physical channel number (0-7 for single-ended).
        name: Logical alias used as the stream field name.
        differential_negative: For differential measurements, the negative
            input channel (0-7). If None, uses single-ended mode with AINCOM.
    """

    id: int
    name: str
    differential_negative: int | None = None


@dataclass(frozen=True)
class DacChannel:
    """DAC channel mapping.

    Args:
        id: Physical channel number (0 for A, 1 for B).
        name: Logical alias for the channel.
        initial_voltage: Initial output voltage (default 0.0).
    """

    id: int
    name: str
    initial_voltage: float = 0.0


@dataclass(frozen=True)
class HighPrecisionAdDaConfig:
    """Configuration for the High-Precision AD/DA instrument.

    Args:
        source_id: Stream source identifier.
        adc_channels: ADC input channels with their aliases.
        dac_channels: DAC output channels with their aliases.
        adc_gain: ADC programmable gain setting.
        adc_data_rate: ADC sample rate setting.
        adc_vref: ADC reference voltage (typically 2.5V).
        dac_vref: DAC reference voltage (typically 5.0V).
        spi_bus: SPI bus number.
        spi_device: SPI device number.
        adc_cs_pin: GPIO pin for ADC chip select.
        adc_drdy_pin: GPIO pin for ADC data ready.
        adc_reset_pin: GPIO pin for ADC reset (or None).
        dac_cs_pin: GPIO pin for DAC chip select.
    """

    source_id: str
    adc_channels: tuple[AdcChannel, ...] = ()
    dac_channels: tuple[DacChannel, ...] = ()
    adc_gain: Ads1256Gain = Ads1256Gain.GAIN_1
    adc_data_rate: Ads1256DataRate = Ads1256DataRate.SPS_100
    adc_vref: float = 2.5
    dac_vref: float = 5.0
    spi_bus: int = 0
    spi_device: int = 0
    adc_cs_pin: int = 22
    adc_drdy_pin: int = 17
    adc_reset_pin: int | None = 18
    dac_cs_pin: int = 23

    def __post_init__(self) -> None:
        """Validate configuration parameters."""
        # Validate ADC channels
        seen_ids: set[int] = set()
        seen_names: set[str] = set()
        for adc_ch in self.adc_channels:
            if not 0 <= adc_ch.id <= 7:
                raise ValueError(f"ADC channel id must be 0-7, got {adc_ch.id}")
            if adc_ch.differential_negative is not None:
                if not 0 <= adc_ch.differential_negative <= 7:
                    raise ValueError(
                        f"differential_negative must be 0-7, got {adc_ch.differential_negative}"
                    )
            if adc_ch.id in seen_ids:
                raise ValueError(f"duplicate ADC channel id: {adc_ch.id}")
            if adc_ch.name in seen_names:
                raise ValueError(f"duplicate channel name: {adc_ch.name}")
            seen_ids.add(adc_ch.id)
            seen_names.add(adc_ch.name)

        # Validate DAC channels
        seen_dac_ids: set[int] = set()
        for dac_ch in self.dac_channels:
            if not 0 <= dac_ch.id <= 1:
                raise ValueError(f"DAC channel id must be 0 or 1, got {dac_ch.id}")
            if dac_ch.name in seen_names:
                raise ValueError(f"duplicate channel name: {dac_ch.name}")
            if dac_ch.id in seen_dac_ids:
                raise ValueError(f"duplicate DAC channel id: {dac_ch.id}")
            if not 0.0 <= dac_ch.initial_voltage <= self.dac_vref:
                raise ValueError(
                    f"initial_voltage must be 0-{self.dac_vref}V, got {dac_ch.initial_voltage}"
                )
            seen_dac_ids.add(dac_ch.id)
            seen_names.add(dac_ch.name)


class HighPrecisionAdDaInstrument:
    """Instrument driver for the Waveshare High-Precision AD/DA board.

    Provides access to the ADS1256 24-bit ADC and DAC8532 16-bit DAC
    through a unified interface compatible with the hwtest rack system.

    Args:
        config: Instrument configuration.
        publisher: Stream publisher for ADC data (can be None if not streaming).
    """

    def __init__(
        self,
        config: HighPrecisionAdDaConfig,
        publisher: StreamPublisher | None = None,
    ) -> None:
        self._config = config
        self._publisher = publisher

        # Build stream schema for ADC channels
        self._schema = StreamSchema(
            source_id=SourceId(config.source_id),
            fields=tuple(StreamField(ch.name, DataType.F64, "V") for ch in config.adc_channels),
        )

        # Build channel name lookup
        self._adc_by_name: dict[str, AdcChannel] = {ch.name: ch for ch in config.adc_channels}
        self._dac_by_name: dict[str, DacChannel] = {ch.name: ch for ch in config.dac_channels}

        # Device handles
        self._adc: Ads1256 | None = None
        self._dac: Dac8532 | None = None
        self._running = False
        self._task: asyncio.Task[None] | None = None

    @property
    def schema(self) -> StreamSchema:
        """The stream schema for this instrument's ADC channels."""
        return self._schema

    @property
    def actual_sample_rate(self) -> float:
        """The actual ADC sample rate in Hz."""
        return DATA_RATE_VALUES[self._config.adc_data_rate]

    @property
    def is_running(self) -> bool:
        """Return True if the instrument is actively scanning."""
        return self._running

    def get_identity(self) -> InstrumentIdentity:
        """Return the instrument identity.

        Returns:
            Instrument identity with manufacturer, model, and serial.
        """
        # Try to get ADC chip ID if device is open
        serial = ""
        if self._adc is not None and self._adc.is_open:
            try:
                chip_id = self._adc.get_chip_id()
                serial = f"ADS1256-{chip_id:02X}"
            except Exception:  # pylint: disable=broad-exception-caught
                serial = "unknown"

        return InstrumentIdentity(
            manufacturer="Waveshare",
            model="High-Precision AD/DA",
            serial=serial,
            firmware="",
        )

    async def start(self) -> None:
        """Open the devices and begin continuous ADC scanning.

        Raises:
            HwtestError: If the devices cannot be opened.
        """
        if self._running:
            return

        loop = asyncio.get_running_loop()

        # Create and open ADC
        adc_config = Ads1256Config(
            spi_bus=self._config.spi_bus,
            spi_device=self._config.spi_device,
            cs_pin=self._config.adc_cs_pin,
            drdy_pin=self._config.adc_drdy_pin,
            reset_pin=self._config.adc_reset_pin,
            gain=self._config.adc_gain,
            data_rate=self._config.adc_data_rate,
            vref=self._config.adc_vref,
        )
        self._adc = Ads1256(adc_config)

        try:
            await loop.run_in_executor(None, self._adc.open)
        except ImportError:
            raise
        except Exception as exc:
            raise HwtestError(f"Failed to open ADS1256: {exc}") from exc

        # Create and open DAC if channels configured
        if self._config.dac_channels:
            dac_config = Dac8532Config(
                spi_bus=self._config.spi_bus,
                spi_device=self._config.spi_device,
                cs_pin=self._config.dac_cs_pin,
                vref=self._config.dac_vref,
            )
            self._dac = Dac8532(dac_config)

            try:
                await loop.run_in_executor(None, self._dac.open)
            except ImportError:
                self._adc.close()
                raise
            except Exception as exc:
                self._adc.close()
                raise HwtestError(f"Failed to open DAC8532: {exc}") from exc

            # Set initial voltages
            for ch in self._config.dac_channels:
                await loop.run_in_executor(None, self._dac.write_voltage, ch.id, ch.initial_voltage)

        self._running = True

        # Start scan loop if we have ADC channels and a publisher
        if self._config.adc_channels and self._publisher is not None:
            self._task = asyncio.create_task(self._scan_loop())

    async def stop(self) -> None:
        """Stop scanning and release the devices."""
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

        loop = asyncio.get_running_loop()

        if self._dac is not None:
            try:
                await loop.run_in_executor(None, self._dac.close)
            except Exception:
                logger.warning("Error closing DAC", exc_info=True)
            self._dac = None

        if self._adc is not None:
            try:
                await loop.run_in_executor(None, self._adc.close)
            except Exception:
                logger.warning("Error closing ADC", exc_info=True)
            self._adc = None

    async def _scan_loop(self) -> None:
        """Read ADC samples and publish StreamData batches."""
        if self._adc is None or self._publisher is None:
            return

        sample_rate = self.actual_sample_rate
        period_ns = int(1_000_000_000 / sample_rate)
        batch_size = max(1, int(sample_rate / 10))  # ~10 batches/second

        start_time_ns = time.time_ns()
        total_samples = 0
        loop = asyncio.get_running_loop()
        batch: list[tuple[float, ...]] = []

        while self._running:
            try:
                # Read all configured ADC channels
                voltages: list[float] = []
                for ch in self._config.adc_channels:
                    if ch.differential_negative is not None:
                        voltage = await loop.run_in_executor(
                            None,
                            self._adc.read_differential,
                            ch.id,
                            ch.differential_negative,
                        )
                    else:
                        voltage = await loop.run_in_executor(None, self._adc.read_voltage, ch.id)
                    voltages.append(voltage)

                batch.append(tuple(voltages))
                total_samples += 1

                if len(batch) >= batch_size:
                    data = StreamData(
                        schema_id=self._schema.schema_id,
                        timestamp_ns=start_time_ns + (total_samples - len(batch)) * period_ns,
                        period_ns=period_ns,
                        samples=tuple(batch),
                    )
                    batch = []

                    try:
                        await self._publisher.publish(data)
                    except Exception:
                        logger.warning("Error publishing stream data", exc_info=True)

            except asyncio.CancelledError:
                break
            except Exception:
                if self._running:
                    logger.warning("Error reading ADC", exc_info=True)
                await asyncio.sleep(0.1)

    # Direct read/write methods for non-streaming use

    async def read_voltage(self, channel: str | int) -> float:
        """Read voltage from an ADC channel.

        Args:
            channel: Channel name or number (0-7).

        Returns:
            Voltage in volts.

        Raises:
            HwtestError: If device is not running.
            ValueError: If channel is invalid.
        """
        if self._adc is None:
            raise HwtestError("Device not started; call start() first")

        if isinstance(channel, str):
            ch_info = self._adc_by_name.get(channel)
            if ch_info is None:
                raise ValueError(f"Unknown ADC channel: {channel}")
            ch_id = ch_info.id
            diff_neg = ch_info.differential_negative
        else:
            ch_id = channel
            diff_neg = None

        loop = asyncio.get_running_loop()
        if diff_neg is not None:
            return await loop.run_in_executor(None, self._adc.read_differential, ch_id, diff_neg)
        return await loop.run_in_executor(None, self._adc.read_voltage, ch_id)

    async def write_voltage(self, channel: str | int, voltage: float) -> None:
        """Write voltage to a DAC channel.

        Args:
            channel: Channel name or number (0 for A, 1 for B).
            voltage: Output voltage (0 to Vref).

        Raises:
            HwtestError: If device is not running or no DAC configured.
            ValueError: If channel or voltage is invalid.
        """
        if self._dac is None:
            raise HwtestError("DAC not configured or device not started")

        if isinstance(channel, str):
            ch_info = self._dac_by_name.get(channel)
            if ch_info is None:
                raise ValueError(f"Unknown DAC channel: {channel}")
            ch_id = ch_info.id
        else:
            ch_id = channel

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._dac.write_voltage, ch_id, voltage)

    async def read_all_adc(self) -> dict[str, float]:
        """Read voltages from all configured ADC channels.

        Returns:
            Dictionary mapping channel names to voltages.

        Raises:
            HwtestError: If device is not running.
        """
        if self._adc is None:
            raise HwtestError("Device not started; call start() first")

        result: dict[str, float] = {}
        for ch in self._config.adc_channels:
            voltage = await self.read_voltage(ch.name)
            result[ch.name] = voltage

        return result

    async def read_all_dac(self) -> dict[str, float]:
        """Read last written voltages from all configured DAC channels.

        Returns:
            Dictionary mapping channel names to voltages.

        Raises:
            HwtestError: If DAC is not configured or device not started.
        """
        if self._dac is None:
            raise HwtestError("DAC not configured or device not started")

        result: dict[str, float] = {}
        loop = asyncio.get_running_loop()
        for ch in self._config.dac_channels:
            voltage = await loop.run_in_executor(None, self._dac.read_voltage, ch.id)
            result[ch.name] = voltage

        return result


def create_instrument(
    source_id: str,
    adc_channels: list[dict[str, Any]] | None = None,
    dac_channels: list[dict[str, Any]] | None = None,
    adc_gain: str | int = "GAIN_1",
    adc_data_rate: str | int = "SPS_100",
    adc_vref: float = 2.5,
    dac_vref: float = 5.0,
    spi_bus: int = 0,
    spi_device: int = 0,
    adc_cs_pin: int = 22,
    adc_drdy_pin: int = 17,
    adc_reset_pin: int | None = 18,
    dac_cs_pin: int = 23,
    publisher: StreamPublisher | None = None,
) -> HighPrecisionAdDaInstrument:
    """Create a High-Precision AD/DA instrument from configuration parameters.

    Standard factory entry point for the test rack and programmatic use.

    Args:
        source_id: Stream source identifier.
        adc_channels: List of ADC channel definitions, each with ``id``, ``name``,
            and optional ``differential_negative``.
        dac_channels: List of DAC channel definitions, each with ``id``, ``name``,
            and optional ``initial_voltage``.
        adc_gain: ADC gain setting (enum name or value).
        adc_data_rate: ADC data rate setting (enum name or value).
        adc_vref: ADC reference voltage.
        dac_vref: DAC reference voltage.
        spi_bus: SPI bus number.
        spi_device: SPI device number.
        adc_cs_pin: GPIO pin for ADC chip select.
        adc_drdy_pin: GPIO pin for ADC data ready.
        adc_reset_pin: GPIO pin for ADC reset (or None).
        dac_cs_pin: GPIO pin for DAC chip select.
        publisher: Stream publisher for ADC data.

    Returns:
        Configured instrument instance (call ``start()`` to begin operation).
    """
    # Parse ADC channels
    adc_ch_objs: list[AdcChannel] = []
    if adc_channels:
        for ch in adc_channels:
            adc_ch_objs.append(
                AdcChannel(
                    id=ch["id"],
                    name=ch["name"],
                    differential_negative=ch.get("differential_negative"),
                )
            )

    # Parse DAC channels
    dac_ch_objs: list[DacChannel] = []
    if dac_channels:
        for ch in dac_channels:
            dac_ch_objs.append(
                DacChannel(
                    id=ch["id"],
                    name=ch["name"],
                    initial_voltage=ch.get("initial_voltage", 0.0),
                )
            )

    # Parse gain
    if isinstance(adc_gain, str):
        gain_enum = Ads1256Gain[adc_gain]
    else:
        gain_enum = Ads1256Gain(adc_gain)

    # Parse data rate
    if isinstance(adc_data_rate, str):
        rate_enum = Ads1256DataRate[adc_data_rate]
    else:
        rate_enum = Ads1256DataRate(adc_data_rate)

    config = HighPrecisionAdDaConfig(
        source_id=source_id,
        adc_channels=tuple(adc_ch_objs),
        dac_channels=tuple(dac_ch_objs),
        adc_gain=gain_enum,
        adc_data_rate=rate_enum,
        adc_vref=adc_vref,
        dac_vref=dac_vref,
        spi_bus=spi_bus,
        spi_device=spi_device,
        adc_cs_pin=adc_cs_pin,
        adc_drdy_pin=adc_drdy_pin,
        adc_reset_pin=adc_reset_pin,
        dac_cs_pin=dac_cs_pin,
    )

    return HighPrecisionAdDaInstrument(config, publisher)
