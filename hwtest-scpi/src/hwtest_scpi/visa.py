"""PyVISA transport for SCPI instruments.

This module provides a VISA-based transport implementation for communicating
with SCPI instruments. It wraps the PyVISA library, which is lazily imported
to allow the rest of hwtest-scpi to work without VISA installed.

Supported resource string formats include:
- TCPIP: ``TCPIP::192.168.1.100::INSTR`` (LAN instruments)
- USB: ``USB0::0x0957::0x0407::MY12345678::0::INSTR``
- GPIB: ``GPIB0::22::INSTR``
- Serial: ``ASRL1::INSTR``
"""

from __future__ import annotations

from typing import Any

from hwtest_core.errors import HwtestError


class VisaResource:
    """SCPI transport backed by PyVISA.

    Uses NI-style VISA resource strings (e.g.
    ``"TCPIP::192.168.1.100::INSTR"``) to address instruments.  The
    ``pyvisa`` library is imported lazily on :meth:`open` so the rest of
    ``hwtest-scpi`` works without it installed.

    This class implements the :class:`ScpiTransport` protocol and can be
    passed to :class:`ScpiConnection` for high-level SCPI operations.

    Attributes:
        resource_string: The VISA resource address string.
        is_open: Whether the resource is currently open.

    Args:
        resource_string: VISA resource address.
        timeout_ms: I/O timeout in milliseconds (applied on open).
        read_termination: Character(s) that terminate read operations.
        write_termination: Character(s) appended to write operations.

    Example:
        >>> resource = VisaResource("TCPIP::192.168.1.100::INSTR")
        >>> resource.open()
        >>> resource.write("*IDN?")
        >>> print(resource.read())
        >>> resource.close()
    """

    def __init__(
        self,
        resource_string: str,
        *,
        timeout_ms: int = 5000,
        read_termination: str = "\n",
        write_termination: str = "\n",
    ) -> None:
        """Initialize the VISA resource.

        Args:
            resource_string: VISA resource address string.
            timeout_ms: I/O timeout in milliseconds. Defaults to 5000.
            read_termination: Character(s) that terminate read operations.
                Defaults to newline.
            write_termination: Character(s) appended to write operations.
                Defaults to newline.
        """
        self._resource_string = resource_string
        self._timeout_ms = timeout_ms
        self._read_termination = read_termination
        self._write_termination = write_termination
        self._rm: Any = None
        self._resource: Any = None

    # -- Properties ----------------------------------------------------------

    @property
    def resource_string(self) -> str:
        """The VISA resource string."""
        return self._resource_string

    @property
    def is_open(self) -> bool:
        """Return True if the resource is currently open."""
        return self._resource is not None

    # -- Lifecycle -----------------------------------------------------------

    def open(self) -> None:
        """Open the VISA resource.

        Lazily imports ``pyvisa`` and creates a :class:`ResourceManager`.

        Raises:
            HwtestError: If ``pyvisa`` is not installed or the resource
                cannot be opened.
        """
        if self._resource is not None:
            return

        try:
            import pyvisa  # type: ignore[import-not-found]  # pylint: disable=import-outside-toplevel
        except ImportError as exc:
            raise HwtestError(
                "pyvisa library is not installed. Install with: pip install pyvisa"
            ) from exc

        try:
            self._rm = pyvisa.ResourceManager()
            self._resource = self._rm.open_resource(
                self._resource_string,
                read_termination=self._read_termination,
                write_termination=self._write_termination,
            )
            self._resource.timeout = self._timeout_ms
        except Exception as exc:
            self._resource = None
            if self._rm is not None:
                try:
                    self._rm.close()
                except Exception:  # pylint: disable=broad-except
                    pass
            self._rm = None
            raise HwtestError(
                f"Failed to open VISA resource {self._resource_string!r}: {exc}"
            ) from exc

    def close(self) -> None:
        """Close the VISA resource and resource manager.

        Safe to call multiple times.
        """
        if self._resource is not None:
            try:
                self._resource.close()
            except Exception:  # pylint: disable=broad-except
                pass
            self._resource = None
        if self._rm is not None:
            try:
                self._rm.close()
            except Exception:  # pylint: disable=broad-except
                pass
            self._rm = None

    # -- Transport interface -------------------------------------------------

    def write(self, message: str) -> None:
        """Send a message to the instrument.

        Args:
            message: The SCPI command or query string.

        Raises:
            HwtestError: If the resource is not open.
        """
        if self._resource is None:
            raise HwtestError("VISA resource is not open")
        self._resource.write(message)

    def read(self) -> str:
        """Read a response from the instrument.

        Returns:
            The response string.

        Raises:
            HwtestError: If the resource is not open.
        """
        if self._resource is None:
            raise HwtestError("VISA resource is not open")
        result: str = self._resource.read()
        return result
