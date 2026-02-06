"""SCPI transport protocol definition.

This module defines the :class:`ScpiTransport` protocol, which specifies the
interface that all SCPI transport implementations must provide. Transports
handle the physical layer communication with instruments.

Implementations include:
- :class:`hwtest_scpi.VisaResource`: PyVISA-backed transport for real hardware
- Emulator transports in instrument driver packages (e.g., hwtest-bkprecision)
"""

from __future__ import annotations

from typing import Protocol


class ScpiTransport(Protocol):
    """Protocol for SCPI message transport.

    Implementations provide the physical layer for sending commands to and
    receiving responses from SCPI instruments. Callers are responsible for
    opening the transport before passing it to :class:`ScpiConnection`.

    This is a structural subtyping protocol (duck typing). Any class that
    implements ``write()``, ``read()``, and ``close()`` methods with the
    correct signatures is considered a valid transport.

    Example:
        >>> class MyTransport:
        ...     def write(self, message: str) -> None:
        ...         # Send message to instrument
        ...         pass
        ...     def read(self) -> str:
        ...         # Read response from instrument
        ...         return "response"
        ...     def close(self) -> None:
        ...         # Release resources
        ...         pass
        ...
        >>> transport: ScpiTransport = MyTransport()  # Type checks OK
    """

    def write(self, message: str) -> None:
        """Send a message to the instrument.

        Args:
            message: The SCPI command or query string to send.
        """
        ...

    def read(self) -> str:
        """Read a response from the instrument.

        Returns:
            The response string with trailing whitespace stripped.
        """
        ...

    def close(self) -> None:
        """Close the transport and release resources."""
        ...
