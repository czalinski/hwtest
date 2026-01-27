"""SCPI protocol error types."""

from __future__ import annotations

from dataclasses import dataclass

from hwtest_core.errors import HwtestError


class ScpiError(HwtestError):
    """Base exception for SCPI protocol errors."""


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
        return f'{self.code},"{self.message}"'


class ScpiCommandError(ScpiError):
    """Raised when an instrument reports errors after a command or query.

    Attributes:
        errors: One or more errors drained from the instrument's error queue.
    """

    def __init__(self, errors: tuple[ScpiInstrumentError, ...]) -> None:
        self.errors = errors
        messages = "; ".join(str(e) for e in errors)
        super().__init__(f"SCPI instrument error(s): {messages}")
