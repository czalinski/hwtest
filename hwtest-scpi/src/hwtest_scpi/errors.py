"""SCPI protocol error types.

This module defines exception classes for SCPI protocol errors that may occur
during communication with instruments. All exceptions inherit from
:class:`hwtest_core.errors.HwtestError`.
"""

from __future__ import annotations

from dataclasses import dataclass

from hwtest_core.errors import HwtestError


class ScpiError(HwtestError):
    """Base exception for SCPI protocol errors.

    All SCPI-related exceptions inherit from this class, allowing callers
    to catch all SCPI errors with a single except clause.
    """


@dataclass(frozen=True)
class ScpiInstrumentError:
    """Single error from an instrument's error queue.

    Attributes:
        code: SCPI error code (negative for standard errors, positive for device-specific).
        message: Human-readable error description from the instrument.
    """

    code: int
    message: str

    def __str__(self) -> str:
        """Return SCPI-format error string.

        Returns:
            Error formatted as ``code,"message"``.
        """
        return f'{self.code},"{self.message}"'


class ScpiCommandError(ScpiError):
    """Raised when an instrument reports errors after a command or query.

    This exception is raised by :class:`ScpiConnection` when automatic error
    checking is enabled and the instrument's error queue contains errors after
    a command or query.

    Attributes:
        errors: One or more errors drained from the instrument's error queue.

    Example:
        >>> try:
        ...     conn.command("INVALID:COMMAND")
        ... except ScpiCommandError as e:
        ...     for err in e.errors:
        ...         print(f"Error {err.code}: {err.message}")
    """

    def __init__(self, errors: tuple[ScpiInstrumentError, ...]) -> None:
        """Initialize the command error with instrument errors.

        Args:
            errors: Tuple of instrument errors from the error queue.
        """
        self.errors = errors
        messages = "; ".join(str(e) for e in errors)
        super().__init__(f"SCPI instrument error(s): {messages}")
