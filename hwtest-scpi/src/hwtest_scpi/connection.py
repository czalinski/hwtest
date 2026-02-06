"""SCPI connection with automatic error checking.

This module provides the :class:`ScpiConnection` class, which wraps a
transport layer to provide high-level SCPI operations with automatic error
queue checking, typed query methods, and IEEE 488.2 common commands.

Typical usage::

    from hwtest_scpi import VisaResource, ScpiConnection

    transport = VisaResource("TCPIP::192.168.1.100::INSTR")
    transport.open()
    conn = ScpiConnection(transport)

    # Get instrument identity
    identity = conn.get_identity()

    # Send commands and queries
    conn.command("CONF:VOLT:DC 10")
    voltage = conn.query_number("MEAS:VOLT:DC?")

    conn.close()
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from hwtest_core.types.common import InstrumentIdentity

from hwtest_scpi.errors import ScpiCommandError, ScpiInstrumentError
from hwtest_scpi.number import parse_bool, parse_int, parse_number, parse_numbers

if TYPE_CHECKING:
    from hwtest_scpi.transport import ScpiTransport


def parse_idn_response(response: str) -> InstrumentIdentity:
    """Parse a SCPI ``*IDN?`` response into an :class:`InstrumentIdentity`.

    The standard ``*IDN?`` response format is four comma-separated fields::

        manufacturer,model,serial_number,firmware_version

    If the response contains more than four comma-separated fields, the
    extra fields are joined into the firmware string.

    Args:
        response: The raw ``*IDN?`` response string.

    Returns:
        Parsed identity with manufacturer, model, serial, and firmware.

    Raises:
        ValueError: If the response has fewer than four fields.
    """
    parts = [p.strip() for p in response.split(",")]
    if len(parts) < 4:
        raise ValueError(
            f"Expected at least 4 comma-separated fields in *IDN? response, "
            f"got {len(parts)}: {response!r}"
        )
    return InstrumentIdentity(
        manufacturer=parts[0],
        model=parts[1],
        serial=parts[2],
        firmware=",".join(parts[3:]),
    )


# Matches SCPI error responses: optional +/- code, comma, optional quoted message.
_ERROR_RE = re.compile(r"^\s*([+-]?\d+)\s*,\s*\"?([^\"]*)\"?\s*$")


class ScpiConnection:
    """High-level SCPI connection wrapping a transport.

    Provides command/query methods with automatic ``SYST:ERR?`` checking,
    typed query variants, and common IEEE 488.2 convenience methods.

    The connection performs automatic error checking by default: after each
    command or query, it drains the instrument's error queue via ``SYST:ERR?``
    and raises :class:`ScpiCommandError` if errors are present.

    Attributes:
        check_errors: Whether automatic error checking is enabled.

    Args:
        transport: An open :class:`ScpiTransport` instance.
        check_errors: If True (default), every command and query is followed
            by draining the instrument error queue. Errors raise
            :class:`ScpiCommandError`.

    Example:
        >>> conn = ScpiConnection(transport)
        >>> conn.reset()  # Send *RST
        >>> voltage = conn.query_number("MEAS:VOLT:DC?")
        >>> print(f"Measured: {voltage} V")
    """

    def __init__(self, transport: ScpiTransport, *, check_errors: bool = True) -> None:
        """Initialize the SCPI connection.

        Args:
            transport: An open transport implementing :class:`ScpiTransport`.
            check_errors: Enable automatic error queue checking. Defaults to True.
        """
        self._transport = transport
        self._check_errors = check_errors

    # -- Core operations -----------------------------------------------------

    def command(self, cmd: str, *, check: bool | None = None) -> None:
        """Send a SCPI command (no response expected).

        Args:
            cmd: The SCPI command string (e.g. ``"CONF:VOLT:DC 10"``).
            check: Override the instance-level error check setting.

        Raises:
            ScpiCommandError: If the instrument reports errors.
        """
        self._transport.write(cmd)
        self._check(check)

    def query(self, cmd: str, *, check: bool | None = None) -> str:
        """Send a SCPI query and return the response.

        Args:
            cmd: The SCPI query string (e.g. ``"MEAS:VOLT:DC?"``).
            check: Override the instance-level error check setting.

        Returns:
            The instrument response with trailing whitespace stripped.

        Raises:
            ScpiCommandError: If the instrument reports errors.
        """
        self._transport.write(cmd)
        response = self._transport.read().strip()
        self._check(check)
        return response

    # -- Typed query variants ------------------------------------------------

    def query_number(self, cmd: str, *, check: bool | None = None) -> float:
        """Query and parse the response as a SCPI number.

        Parses NR1, NR2, NR3 formats and special values (NAN, INF, NINF).

        Args:
            cmd: The SCPI query string.
            check: Override the instance-level error check setting.

        Returns:
            The parsed float value.

        Raises:
            ValueError: If the response cannot be parsed as a number.
            ScpiCommandError: If the instrument reports errors.
        """
        return parse_number(self.query(cmd, check=check))

    def query_numbers(self, cmd: str, *, check: bool | None = None) -> tuple[float, ...]:
        """Query and parse the response as a comma-separated list of numbers.

        Args:
            cmd: The SCPI query string.
            check: Override the instance-level error check setting.

        Returns:
            A tuple of parsed float values.

        Raises:
            ValueError: If any element cannot be parsed as a number.
            ScpiCommandError: If the instrument reports errors.
        """
        return parse_numbers(self.query(cmd, check=check))

    def query_int(self, cmd: str, *, check: bool | None = None) -> int:
        """Query and parse the response as an integer.

        Args:
            cmd: The SCPI query string.
            check: Override the instance-level error check setting.

        Returns:
            The parsed integer value.

        Raises:
            ValueError: If the response is not a valid integer.
            ScpiCommandError: If the instrument reports errors.
        """
        return parse_int(self.query(cmd, check=check))

    def query_bool(self, cmd: str, *, check: bool | None = None) -> bool:
        """Query and parse the response as a boolean.

        Accepts ``"1"``/``"0"`` and ``"ON"``/``"OFF"`` (case-insensitive).

        Args:
            cmd: The SCPI query string.
            check: Override the instance-level error check setting.

        Returns:
            The parsed boolean value.

        Raises:
            ValueError: If the response is not a recognized boolean token.
            ScpiCommandError: If the instrument reports errors.
        """
        return parse_bool(self.query(cmd, check=check))

    # -- IEEE 488.2 convenience methods --------------------------------------

    def identify(self) -> str:
        """Query the instrument identification string (``*IDN?``).

        Returns:
            Raw identification string in the format:
            ``manufacturer,model,serial,firmware``.
        """
        return self.query("*IDN?")

    def get_identity(self) -> InstrumentIdentity:
        """Query and parse the instrument identification (``*IDN?``).

        Returns:
            Parsed :class:`InstrumentIdentity` with manufacturer, model,
            serial number, and firmware version.
        """
        return parse_idn_response(self.identify())

    def reset(self) -> None:
        """Send a reset command (``*RST``).

        Resets the instrument to its power-on default state.
        """
        self.command("*RST")

    def clear_status(self) -> None:
        """Clear the status registers (``*CLS``).

        Clears the error queue, status registers, and any pending triggers.
        """
        self.command("*CLS")

    def wait_complete(self) -> None:
        """Wait for all pending operations to complete (``*OPC?``).

        Error checking is disabled for this query since ``*OPC?`` blocks
        until the instrument is ready.
        """
        self.query("*OPC?", check=False)

    # -- Error queue ---------------------------------------------------------

    def get_errors(self) -> tuple[ScpiInstrumentError, ...]:
        """Drain the instrument error queue.

        Repeatedly queries ``SYST:ERR?`` until the instrument returns a
        ``0,"No error"`` response.

        Returns:
            A tuple of :class:`ScpiInstrumentError` for every queued error.
            Empty if no errors.
        """
        errors: list[ScpiInstrumentError] = []
        while True:
            self._transport.write("SYST:ERR?")
            raw = self._transport.read().strip()
            error = self._parse_error_response(raw)
            if error is None:
                break
            errors.append(error)
        return tuple(errors)

    # -- Lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Close the underlying transport."""
        self._transport.close()

    # -- Private helpers -----------------------------------------------------

    def _check(self, override: bool | None) -> None:
        """Drain the error queue and raise if errors are found."""
        should_check = self._check_errors if override is None else override
        if not should_check:
            return
        errors = self.get_errors()
        if errors:
            raise ScpiCommandError(errors)

    @staticmethod
    def _parse_error_response(raw: str) -> ScpiInstrumentError | None:
        """Parse a ``SYST:ERR?`` response into an error object.

        Returns ``None`` when the response indicates no error (code 0).
        """
        match = _ERROR_RE.match(raw)
        if match is None:
            return None
        code = int(match.group(1))
        message = match.group(2).strip()
        if code == 0:
            return None
        return ScpiInstrumentError(code=code, message=message)
