"""MCC 152 digital I/O and analog output HAT instrument driver."""

# pylint: disable=broad-exception-caught  # HAT calls may raise unpredictable exceptions

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from hwtest_core.errors import HwtestError
from hwtest_core.types.common import InstrumentIdentity


class DioDirection(Enum):
    """Digital I/O pin direction."""

    INPUT = 0
    OUTPUT = 1


@dataclass(frozen=True)
class Mcc152DioChannel:
    """A single MCC 152 digital I/O channel configuration.

    Args:
        id: Physical channel number (0-7).
        name: Logical alias for this channel.
        direction: Whether this pin is an input or output.
        initial_value: Initial output value (only used if direction is OUTPUT).
    """

    id: int
    name: str
    direction: DioDirection
    initial_value: bool = False


@dataclass(frozen=True)
class Mcc152AnalogChannel:
    """A single MCC 152 analog output channel configuration.

    Args:
        id: Physical channel number (0-1).
        name: Logical alias for this channel.
        initial_voltage: Initial output voltage (0-5V).
    """

    id: int
    name: str
    initial_voltage: float = 0.0


@dataclass(frozen=True)
class Mcc152Config:
    """Configuration for an MCC 152 instrument.

    Args:
        address: HAT address on the stack (0-7).
        dio_channels: Digital I/O channel configurations.
        analog_channels: Analog output channel configurations.
        source_id: Instrument source identifier.
    """

    address: int
    dio_channels: tuple[Mcc152DioChannel, ...]
    analog_channels: tuple[Mcc152AnalogChannel, ...]
    source_id: str

    def __post_init__(self) -> None:
        """Validate configuration parameters."""
        if not 0 <= self.address <= 7:
            raise ValueError(f"address must be 0-7, got {self.address}")

        # Validate DIO channels
        seen_dio_ids: set[int] = set()
        seen_names: set[str] = set()
        for ch in self.dio_channels:
            if not 0 <= ch.id <= 7:
                raise ValueError(f"DIO channel id must be 0-7, got {ch.id}")
            if ch.id in seen_dio_ids:
                raise ValueError(f"duplicate DIO channel id: {ch.id}")
            if ch.name in seen_names:
                raise ValueError(f"duplicate channel name: {ch.name}")
            seen_dio_ids.add(ch.id)
            seen_names.add(ch.name)

        # Validate analog channels
        seen_analog_ids: set[int] = set()
        for ch in self.analog_channels:
            if not 0 <= ch.id <= 1:
                raise ValueError(f"analog channel id must be 0-1, got {ch.id}")
            if ch.id in seen_analog_ids:
                raise ValueError(f"duplicate analog channel id: {ch.id}")
            if ch.name in seen_names:
                raise ValueError(f"duplicate channel name: {ch.name}")
            if not 0.0 <= ch.initial_voltage <= 5.0:
                raise ValueError(
                    f"initial_voltage must be 0-5V, got {ch.initial_voltage}"
                )
            seen_analog_ids.add(ch.id)
            seen_names.add(ch.name)


class Mcc152Instrument:
    """Instrument driver for the MCC 152 digital I/O and analog output HAT.

    Provides synchronous control of 8 digital I/O channels (configurable as
    input or output) and 2 analog output channels (0-5V).

    Args:
        config: Instrument configuration.
    """

    def __init__(self, config: Mcc152Config) -> None:
        self._config = config
        self._hat: Any = None
        self._is_open = False
        self._dio_by_name: dict[str, Mcc152DioChannel] = {
            ch.name: ch for ch in config.dio_channels
        }
        self._analog_by_name: dict[str, Mcc152AnalogChannel] = {
            ch.name: ch for ch in config.analog_channels
        }

    @property
    def is_open(self) -> bool:
        """Return True if the HAT connection is open."""
        return self._is_open

    def get_identity(self) -> InstrumentIdentity:
        """Return the instrument identity.

        For MCC DAQ HATs, identity information is obtained from the daqhats
        library rather than an ``*IDN?`` query. The HAT must be opened first
        (via :meth:`open`) before calling this method.

        Returns:
            Instrument identity with manufacturer, model, serial, and firmware.

        Raises:
            HwtestError: If the HAT has not been opened yet.
        """
        if self._hat is None:
            raise HwtestError("HAT not opened; call open() first")
        serial: str = self._hat.serial()
        return InstrumentIdentity(
            manufacturer="Measurement Computing",
            model="MCC 152",
            serial=serial,
            firmware="",
        )

    def open(self) -> None:
        """Open the HAT and configure channels.

        Raises:
            HwtestError: If the daqhats library is not installed or the
                HAT cannot be opened at the configured address.
        """
        if self._is_open:
            return

        try:
            import daqhats  # type: ignore[import-not-found]  # pylint: disable=import-outside-toplevel
        except ImportError as exc:
            raise HwtestError(
                "daqhats library is not installed. Install with: pip install daqhats"
            ) from exc

        try:
            hat = daqhats.mcc152(self._config.address)
        except Exception as exc:
            raise HwtestError(
                f"Failed to open MCC 152 at address {self._config.address}: {exc}"
            ) from exc

        self._hat = hat
        self._is_open = True

        # Configure DIO directions and initial values
        for ch in self._config.dio_channels:
            try:
                # Set direction for this single bit
                hat.dio_config_write_bit(ch.id, ch.direction.value)
                if ch.direction == DioDirection.OUTPUT:
                    hat.dio_output_write_bit(ch.id, int(ch.initial_value))
            except Exception as exc:
                self.close()
                raise HwtestError(
                    f"Failed to configure DIO channel {ch.id}: {exc}"
                ) from exc

        # Set initial analog output values
        for ch in self._config.analog_channels:
            try:
                hat.a_out_write(ch.id, ch.initial_voltage)
            except Exception as exc:
                self.close()
                raise HwtestError(
                    f"Failed to configure analog channel {ch.id}: {exc}"
                ) from exc

    def close(self) -> None:
        """Close the HAT connection."""
        self._hat = None
        self._is_open = False

    def _require_open(self) -> None:
        """Raise if the HAT is not open."""
        if not self._is_open:
            raise HwtestError("HAT not opened; call open() first")

    # -- Digital I/O operations ------------------------------------------------

    def dio_read(self, channel: str | int) -> bool:
        """Read the value of a digital I/O channel.

        Args:
            channel: Channel name or physical channel number (0-7).

        Returns:
            Current digital value (True = high, False = low).

        Raises:
            HwtestError: If the HAT is not open or channel is invalid.
        """
        self._require_open()
        ch_id = self._resolve_dio_channel(channel)
        try:
            value: int = self._hat.dio_input_read_bit(ch_id)
            return bool(value)
        except Exception as exc:
            raise HwtestError(f"Failed to read DIO channel {ch_id}: {exc}") from exc

    def dio_write(self, channel: str | int, value: bool) -> None:
        """Write a value to a digital output channel.

        Args:
            channel: Channel name or physical channel number (0-7).
            value: Output value (True = high, False = low).

        Raises:
            HwtestError: If the HAT is not open or channel is invalid.
        """
        self._require_open()
        ch_id = self._resolve_dio_channel(channel)
        try:
            self._hat.dio_output_write_bit(ch_id, int(value))
        except Exception as exc:
            raise HwtestError(f"Failed to write DIO channel {ch_id}: {exc}") from exc

    def dio_read_all(self) -> int:
        """Read all digital I/O channels as a bitmask.

        Returns:
            8-bit value where bit N corresponds to channel N.

        Raises:
            HwtestError: If the HAT is not open.
        """
        self._require_open()
        try:
            return self._hat.dio_input_read_port()
        except Exception as exc:
            raise HwtestError(f"Failed to read DIO port: {exc}") from exc

    def dio_write_all(self, value: int) -> None:
        """Write all digital output channels from a bitmask.

        Args:
            value: 8-bit value where bit N corresponds to channel N.

        Raises:
            HwtestError: If the HAT is not open.
        """
        self._require_open()
        try:
            self._hat.dio_output_write_port(value & 0xFF)
        except Exception as exc:
            raise HwtestError(f"Failed to write DIO port: {exc}") from exc

    def _resolve_dio_channel(self, channel: str | int) -> int:
        """Resolve a channel name or ID to a physical channel number."""
        if isinstance(channel, int):
            if not 0 <= channel <= 7:
                raise HwtestError(f"DIO channel must be 0-7, got {channel}")
            return channel
        if channel in self._dio_by_name:
            return self._dio_by_name[channel].id
        raise HwtestError(f"Unknown DIO channel: {channel}")

    # -- Analog output operations ----------------------------------------------

    def analog_write(self, channel: str | int, voltage: float) -> None:
        """Write a voltage to an analog output channel.

        Args:
            channel: Channel name or physical channel number (0-1).
            voltage: Output voltage (0-5V).

        Raises:
            HwtestError: If the HAT is not open, channel is invalid,
                or voltage is out of range.
        """
        self._require_open()
        ch_id = self._resolve_analog_channel(channel)
        if not 0.0 <= voltage <= 5.0:
            raise HwtestError(f"Voltage must be 0-5V, got {voltage}")
        try:
            self._hat.a_out_write(ch_id, voltage)
        except Exception as exc:
            raise HwtestError(
                f"Failed to write analog channel {ch_id}: {exc}"
            ) from exc

    def analog_write_all(self, voltages: tuple[float, float]) -> None:
        """Write voltages to both analog output channels.

        Args:
            voltages: Tuple of (channel_0_voltage, channel_1_voltage).

        Raises:
            HwtestError: If the HAT is not open or voltages are out of range.
        """
        self._require_open()
        for i, v in enumerate(voltages):
            if not 0.0 <= v <= 5.0:
                raise HwtestError(f"Voltage for channel {i} must be 0-5V, got {v}")
        try:
            self._hat.a_out_write_all(voltages[0], voltages[1])
        except Exception as exc:
            raise HwtestError(f"Failed to write analog outputs: {exc}") from exc

    def _resolve_analog_channel(self, channel: str | int) -> int:
        """Resolve a channel name or ID to a physical channel number."""
        if isinstance(channel, int):
            if not 0 <= channel <= 1:
                raise HwtestError(f"Analog channel must be 0-1, got {channel}")
            return channel
        if channel in self._analog_by_name:
            return self._analog_by_name[channel].id
        raise HwtestError(f"Unknown analog channel: {channel}")


def create_instrument(
    address: int,
    source_id: str,
    dio_channels: list[dict[str, Any]] | None = None,
    analog_channels: list[dict[str, Any]] | None = None,
) -> Mcc152Instrument:
    """Create an MCC 152 instrument from configuration parameters.

    Standard factory entry point for the test rack and programmatic use.

    Args:
        address: HAT address on the stack (0-7).
        source_id: Instrument source identifier.
        dio_channels: List of DIO channel definitions, each with ``id``, ``name``,
            ``direction`` ("INPUT" or "OUTPUT"), and optional ``initial_value``.
        analog_channels: List of analog channel definitions, each with ``id``,
            ``name``, and optional ``initial_voltage``.

    Returns:
        Configured instrument instance (call ``open()`` to connect).
    """
    dio_objs: tuple[Mcc152DioChannel, ...] = ()
    if dio_channels:
        dio_objs = tuple(
            Mcc152DioChannel(
                id=ch["id"],
                name=ch["name"],
                direction=DioDirection[ch.get("direction", "INPUT")],
                initial_value=ch.get("initial_value", False),
            )
            for ch in dio_channels
        )

    analog_objs: tuple[Mcc152AnalogChannel, ...] = ()
    if analog_channels:
        analog_objs = tuple(
            Mcc152AnalogChannel(
                id=ch["id"],
                name=ch["name"],
                initial_voltage=ch.get("initial_voltage", 0.0),
            )
            for ch in analog_channels
        )

    config = Mcc152Config(
        address=address,
        dio_channels=dio_objs,
        analog_channels=analog_objs,
        source_id=source_id,
    )
    return Mcc152Instrument(config)
