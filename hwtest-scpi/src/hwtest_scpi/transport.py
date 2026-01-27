"""SCPI transport protocol definition."""

from __future__ import annotations

from typing import Protocol


class ScpiTransport(Protocol):
    """Protocol for SCPI message transport.

    Implementations provide the physical layer for sending commands to and
    receiving responses from SCPI instruments.  Callers are responsible for
    opening the transport before passing it to :class:`ScpiConnection`.
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
