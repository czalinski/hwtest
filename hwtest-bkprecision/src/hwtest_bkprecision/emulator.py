"""BK Precision DC power supply emulator.

Provides an in-process SCPI emulator implementing the ``ScpiTransport`` protocol.
Supports both single-channel (9115) and multi-channel (9130B) BK Precision PSU models.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# ---------------------------------------------------------------------------
# Long-form → short-form SCPI keyword map
# ---------------------------------------------------------------------------

_LONG_TO_SHORT: dict[str, str] = {
    "VOLTAGE": "VOLT",
    "CURRENT": "CURR",
    "OUTPUT": "OUTP",
    "MEASURE": "MEAS",
    "INSTRUMENT": "INST",
    "SOURCE": "SOUR",
    "LEVEL": "LEV",
    "IMMEDIATE": "IMM",
    "AMPLITUDE": "AMPL",
    "SCALAR": "SCAL",
    "STATE": "STAT",
    "SYSTEM": "SYST",
    "ERROR": "ERR",
    "PROTECTION": "PROT",
    "APPLY": "APPL",
    "SELECT": "NSEL",
}

# Segments that are optional and should be stripped during normalization
_OPTIONAL_SEGMENTS: set[str] = {"SOUR", "LEV", "IMM", "AMPL", "SCAL", "DC", "STAT"}


def _normalize_header(header: str) -> str:
    """Normalize a SCPI header to canonical short form.

    1. Uppercase
    2. Strip leading colon
    3. Split on ``:``
    4. Map long forms to short forms
    5. Drop optional segments
    6. Rejoin with ``:``
    """
    upper = header.upper()
    if upper.startswith(":"):
        upper = upper[1:]
    segments = upper.split(":")
    short_segments = [_LONG_TO_SHORT.get(seg, seg) for seg in segments]
    filtered = [seg for seg in short_segments if seg not in _OPTIONAL_SEGMENTS]
    return ":".join(filtered)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BkDcPsuEmulatorConfig:
    """Configuration for a BK Precision DC PSU emulator instance.

    Args:
        identity: ``*IDN?`` response string.
        num_channels: Number of output channels (>= 1).
        max_voltage: Maximum voltage in volts (> 0).
        max_current: Maximum current in amps (> 0).
    """

    identity: str
    num_channels: int
    max_voltage: float
    max_current: float

    def __post_init__(self) -> None:
        if not self.identity:
            raise ValueError("identity must be non-empty")
        if self.num_channels < 1:
            raise ValueError("num_channels must be >= 1")
        if self.max_voltage <= 0:
            raise ValueError("max_voltage must be > 0")
        if self.max_current <= 0:
            raise ValueError("max_current must be > 0")


# ---------------------------------------------------------------------------
# Internal channel state
# ---------------------------------------------------------------------------


@dataclass
class _ChannelState:
    voltage_setpoint: float = 0.0
    current_limit: float = 0.0
    output_enabled: bool = False
    ovp_level: float = 0.0
    measured_voltage: float | None = None
    measured_current: float | None = None


# ---------------------------------------------------------------------------
# Emulator
# ---------------------------------------------------------------------------


class BkDcPsuEmulator:
    """In-process BK Precision DC PSU emulator implementing ``ScpiTransport``.

    Args:
        config: Emulator configuration specifying model characteristics.
    """

    def __init__(self, config: BkDcPsuEmulatorConfig) -> None:
        self._config = config
        self._channels: list[_ChannelState] = [
            _ChannelState(ovp_level=config.max_voltage) for _ in range(config.num_channels)
        ]
        self._selected_channel: int = 1
        self._response_buffer: str = ""
        self._error_queue: list[tuple[int, str]] = []

        # Build dispatch tables
        self._set_handlers: dict[str, Callable[[str], None]] = {
            "VOLT": self._set_voltage,
            "CURR": self._set_current,
            "VOLT:PROT": self._set_ovp,
            "OUTP": self._set_output,
            "INST:NSEL": self._set_channel,
            "APPL": self._set_apply,
        }

        self._query_handlers: dict[str, Callable[[], str]] = {
            "VOLT?": self._get_voltage,
            "CURR?": self._get_current,
            "VOLT:PROT?": self._get_ovp,
            "OUTP?": self._get_output,
            "INST:NSEL?": self._get_channel,
            "MEAS:VOLT?": self._measure_voltage,
            "MEAS:CURR?": self._measure_current,
            "MEAS:POW?": self._measure_power,
            "APPL?": self._get_apply,
        }

    # -- Transport interface ------------------------------------------------

    def write(self, message: str) -> None:
        """Process a SCPI command or query string."""
        line = message.strip()
        if not line:
            return

        is_query, header, args = self._parse_line(line)

        if self._handle_common_command(header, is_query):
            return

        self._dispatch(header, args, is_query)

    def _parse_line(self, line: str) -> tuple[bool, str, str]:
        """Parse a SCPI line into (is_query, header, args)."""
        is_query = "?" in line
        if is_query:
            qmark_idx = line.index("?")
            header = line[: qmark_idx + 1]
            args = line[qmark_idx + 1 :].strip()
        else:
            parts = line.split(None, 1)
            header = parts[0]
            args = parts[1] if len(parts) > 1 else ""
        return is_query, header, args

    def _handle_common_command(self, header: str, is_query: bool) -> bool:
        """Handle IEEE 488.2 and SYST:ERR? commands. Returns True if handled."""
        upper_header = header.upper()
        ieee_handlers: dict[str, str | None] = {
            "*IDN?": self._config.identity,
            "*OPC?": "1",
        }
        if upper_header in ieee_handlers:
            self._response_buffer = ieee_handlers[upper_header] or ""
            return True
        if upper_header == "*RST":
            self._reset()
            return True
        if upper_header == "*CLS":
            self._error_queue.clear()
            return True
        # SYST:ERR? handling
        if is_query:
            norm_header = _normalize_header(header.rstrip("?"))
            if norm_header == "SYST:ERR":
                self._response_buffer = self._pop_error()
                return True
        return False

    def _dispatch(self, header: str, args: str, is_query: bool) -> None:
        """Dispatch a normalized command or query to handler tables."""
        if is_query:
            norm_key = _normalize_header(header.rstrip("?")) + "?"
            handler = self._query_handlers.get(norm_key)
            if handler is not None:
                self._response_buffer = handler()
            else:
                self._error_queue.append((-100, "Command error"))
        else:
            norm_key = _normalize_header(header)
            handler_set = self._set_handlers.get(norm_key)
            if handler_set is not None:
                handler_set(args)
            else:
                self._error_queue.append((-100, "Command error"))

    def read(self) -> str:
        """Return and clear the buffered response."""
        resp = self._response_buffer
        self._response_buffer = ""
        return resp

    def close(self) -> None:
        """Close the emulator (no-op for in-process transport)."""

    # -- Test helpers -------------------------------------------------------

    def set_measured_voltage(self, value: float, channel: int = 1) -> None:
        """Set a fixed measured voltage override for testing.

        Args:
            value: Voltage reading to return from ``MEAS:VOLT?``.
            channel: Channel number (1-based).
        """
        self._get_channel_state(channel).measured_voltage = value

    def set_measured_current(self, value: float, channel: int = 1) -> None:
        """Set a fixed measured current override for testing.

        Args:
            value: Current reading to return from ``MEAS:CURR?``.
            channel: Channel number (1-based).
        """
        self._get_channel_state(channel).measured_current = value

    # -- Private helpers ----------------------------------------------------

    def _get_channel_state(self, channel: int) -> _ChannelState:
        if channel < 1 or channel > self._config.num_channels:
            raise ValueError(f"Channel {channel} out of range (1-{self._config.num_channels})")
        return self._channels[channel - 1]

    @property
    def _ch(self) -> _ChannelState:
        """Currently selected channel state."""
        return self._channels[self._selected_channel - 1]

    def _reset(self) -> None:
        """Reset all channels to defaults."""
        for ch in self._channels:
            ch.voltage_setpoint = 0.0
            ch.current_limit = 0.0
            ch.output_enabled = False
            ch.ovp_level = self._config.max_voltage
            ch.measured_voltage = None
            ch.measured_current = None
        self._selected_channel = 1

    def _pop_error(self) -> str:
        if self._error_queue:
            code, msg = self._error_queue.pop(0)
            return f'{code},"{msg}"'
        return '0,"No error"'

    # -- Set handlers -------------------------------------------------------

    def _set_voltage(self, args: str) -> None:
        try:
            value = float(args.strip())
        except ValueError:
            self._error_queue.append((-220, "Parameter error"))
            return
        self._ch.voltage_setpoint = value

    def _set_current(self, args: str) -> None:
        try:
            value = float(args.strip())
        except ValueError:
            self._error_queue.append((-220, "Parameter error"))
            return
        self._ch.current_limit = value

    def _set_ovp(self, args: str) -> None:
        try:
            value = float(args.strip())
        except ValueError:
            self._error_queue.append((-220, "Parameter error"))
            return
        self._ch.ovp_level = value

    def _set_output(self, args: str) -> None:
        token = args.strip().upper()
        if token in ("ON", "1"):
            self._ch.output_enabled = True
        elif token in ("OFF", "0"):
            self._ch.output_enabled = False
        else:
            self._error_queue.append((-220, "Parameter error"))

    def _set_channel(self, args: str) -> None:
        try:
            ch_num = int(args.strip())
        except ValueError:
            self._error_queue.append((-220, "Parameter error"))
            return
        if ch_num < 1 or ch_num > self._config.num_channels:
            self._error_queue.append((-220, "Parameter error"))
            return
        self._selected_channel = ch_num

    def _set_apply(self, args: str) -> None:
        """Parse ``APPL CH{n},<v>,<i>``."""
        parts = [p.strip() for p in args.split(",")]
        if len(parts) != 3:
            self._error_queue.append((-220, "Parameter error"))
            return
        ch_str = parts[0].upper()
        if not ch_str.startswith("CH"):
            self._error_queue.append((-220, "Parameter error"))
            return
        try:
            ch_num = int(ch_str[2:])
            voltage = float(parts[1])
            current = float(parts[2])
        except ValueError:
            self._error_queue.append((-220, "Parameter error"))
            return
        if ch_num < 1 or ch_num > self._config.num_channels:
            self._error_queue.append((-220, "Parameter error"))
            return
        ch = self._channels[ch_num - 1]
        ch.voltage_setpoint = voltage
        ch.current_limit = current

    # -- Query handlers -----------------------------------------------------

    def _get_voltage(self) -> str:
        return f"{self._ch.voltage_setpoint:.4f}"

    def _get_current(self) -> str:
        return f"{self._ch.current_limit:.4f}"

    def _get_ovp(self) -> str:
        return f"{self._ch.ovp_level:.4f}"

    def _get_output(self) -> str:
        return "1" if self._ch.output_enabled else "0"

    def _get_channel(self) -> str:
        return str(self._selected_channel)

    def _measure_voltage(self) -> str:
        ch = self._ch
        if ch.measured_voltage is not None:
            return f"{ch.measured_voltage:.4f}"
        if ch.output_enabled:
            return f"{ch.voltage_setpoint:.4f}"
        return "0.0000"

    def _measure_current(self) -> str:
        ch = self._ch
        if ch.measured_current is not None:
            return f"{ch.measured_current:.4f}"
        return "0.0000"

    def _measure_power(self) -> str:
        # Use same logic as individual measurements
        ch = self._ch
        if ch.measured_voltage is not None:
            v = ch.measured_voltage
        elif ch.output_enabled:
            v = ch.voltage_setpoint
        else:
            v = 0.0
        if ch.measured_current is not None:
            i = ch.measured_current
        else:
            i = 0.0
        return f"{v * i:.4f}"

    def _get_apply(self) -> str:
        return f"{self._ch.voltage_setpoint:.4f},{self._ch.current_limit:.4f}"


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def make_9115_emulator(serial: str = "SN000001") -> BkDcPsuEmulator:
    """Create a BK Precision 9115 single-channel PSU emulator.

    Args:
        serial: Serial number for the ``*IDN?`` response.

    Returns:
        Configured emulator instance (60 V, 5 A, 1 channel).
    """
    config = BkDcPsuEmulatorConfig(
        identity=f"B&K Precision,9115,{serial},V1.00-V1.00",
        num_channels=1,
        max_voltage=60.0,
        max_current=5.0,
    )
    return BkDcPsuEmulator(config)


def make_9130b_emulator(serial: str = "SN000001") -> BkDcPsuEmulator:
    """Create a BK Precision 9130B triple-channel PSU emulator.

    Args:
        serial: Serial number for the ``*IDN?`` response.

    Returns:
        Configured emulator instance (30 V, 3 A, 3 channels).
    """
    config = BkDcPsuEmulatorConfig(
        identity=f"B&K Precision,9130B,{serial},V1.00-V1.00",
        num_channels=3,
        max_voltage=30.0,
        max_current=3.0,
    )
    return BkDcPsuEmulator(config)
