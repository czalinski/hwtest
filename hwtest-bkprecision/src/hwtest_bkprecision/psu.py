"""BK Precision DC power supply instrument driver.

Wraps a ``ScpiConnection`` with typed methods for controlling BK Precision
9100 series DC power supplies (single-channel 9115, triple-channel 9130B).
"""

from __future__ import annotations

from hwtest_core import InstrumentIdentity
from hwtest_scpi import ScpiConnection, VisaResource


class BkDcPsu:
    """High-level driver for BK Precision DC power supplies.

    Args:
        connection: An open ``ScpiConnection`` to the instrument.
    """

    def __init__(self, connection: ScpiConnection) -> None:
        self._conn = connection

    # -- Identity / lifecycle -----------------------------------------------

    def identify(self) -> str:
        """Query instrument identification string (``*IDN?``)."""
        return self._conn.identify()

    def get_identity(self) -> InstrumentIdentity:
        """Query and parse instrument identification (``*IDN?``).

        Returns:
            Parsed identity with manufacturer, model, serial, and firmware.
        """
        return self._conn.get_identity()

    def reset(self) -> None:
        """Reset instrument to factory defaults (``*RST``)."""
        self._conn.reset()

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    # -- Voltage ------------------------------------------------------------

    def set_voltage(self, voltage: float) -> None:
        """Set the output voltage setpoint.

        Args:
            voltage: Voltage in volts.
        """
        self._conn.command(f"VOLT {voltage}")

    def get_voltage(self) -> float:
        """Query the output voltage setpoint."""
        return self._conn.query_number("VOLT?")

    def measure_voltage(self) -> float:
        """Measure the actual output voltage."""
        return self._conn.query_number("MEAS:VOLT?")

    # -- Current ------------------------------------------------------------

    def set_current(self, current: float) -> None:
        """Set the output current limit.

        Args:
            current: Current in amps.
        """
        self._conn.command(f"CURR {current}")

    def get_current(self) -> float:
        """Query the output current limit."""
        return self._conn.query_number("CURR?")

    def measure_current(self) -> float:
        """Measure the actual output current."""
        return self._conn.query_number("MEAS:CURR?")

    # -- Power --------------------------------------------------------------

    def measure_power(self) -> float:
        """Measure the actual output power."""
        return self._conn.query_number("MEAS:POW?")

    # -- Output -------------------------------------------------------------

    def enable_output(self) -> None:
        """Enable the output."""
        self._conn.command("OUTP ON")

    def disable_output(self) -> None:
        """Disable the output."""
        self._conn.command("OUTP OFF")

    def is_output_enabled(self) -> bool:
        """Query whether the output is enabled."""
        return self._conn.query_bool("OUTP?")

    # -- OVP ----------------------------------------------------------------

    def set_ovp(self, voltage: float) -> None:
        """Set the over-voltage protection level.

        Args:
            voltage: OVP threshold in volts.
        """
        self._conn.command(f"VOLT:PROT {voltage}")

    def get_ovp(self) -> float:
        """Query the over-voltage protection level."""
        return self._conn.query_number("VOLT:PROT?")

    # -- Channel selection (multi-channel) ----------------------------------

    def select_channel(self, channel: int) -> None:
        """Select the active output channel.

        Args:
            channel: Channel number (1-based).
        """
        self._conn.command(f"INST:NSEL {channel}")

    def get_selected_channel(self) -> int:
        """Query the currently selected channel number."""
        return self._conn.query_int("INST:NSEL?")

    # -- Apply (multi-channel convenience) ----------------------------------

    def apply(self, channel: int, voltage: float, current: float) -> None:
        """Set voltage and current for a specific channel.

        Args:
            channel: Channel number (1-based).
            voltage: Voltage in volts.
            current: Current in amps.
        """
        self._conn.command(f"APPL CH{channel},{voltage},{current}")

    def get_apply(self) -> tuple[float, float]:
        """Query the voltage and current setpoints for the selected channel.

        Returns:
            Tuple of (voltage, current).
        """
        values = self._conn.query_numbers("APPL?")
        return (values[0], values[1])


def create_instrument(visa_address: str) -> BkDcPsu:
    """Create a BK Precision PSU driver from a VISA address.

    Standard factory entry point for the test rack and programmatic use.
    Opens the VISA resource, wraps it in a :class:`ScpiConnection`, and
    returns a ready-to-use :class:`BkDcPsu`.

    Args:
        visa_address: VISA resource string
            (e.g. ``"TCPIP::192.168.1.100::5025::SOCKET"``).

    Returns:
        Connected PSU driver instance.
    """
    resource = VisaResource(visa_address)
    resource.open()
    conn = ScpiConnection(resource)
    return BkDcPsu(conn)
