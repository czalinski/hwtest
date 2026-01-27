"""SCPI number parsing and formatting utilities.

Handles NR1 (integer), NR2 (fixed-point), and NR3 (scientific notation)
numeric formats as well as the special values defined by SCPI (NAN, INF,
NINF, MIN, MAX, DEF).
"""

from __future__ import annotations

import math
from enum import Enum


class ScpiSpecial(Enum):
    """SCPI special parameter values."""

    MIN = "MIN"
    MAX = "MAX"
    DEF = "DEF"
    NAN = "NAN"
    INF = "INF"
    NINF = "NINF"


_SPECIAL_FLOAT_MAP: dict[str, float] = {
    "NAN": float("nan"),
    "INF": float("inf"),
    "NINF": float("-inf"),
    "-INF": float("-inf"),
}

_SPECIAL_KEYWORDS: frozenset[str] = frozenset({"MIN", "MAX", "DEF"})


def parse_number(text: str) -> float:
    """Parse a SCPI numeric response into a float.

    Accepts NR1 (``"42"``), NR2 (``"1.23"``), NR3 (``"1.23E+4"``),
    and the special tokens ``NAN``, ``INF``, ``NINF``, and ``-INF``.

    Args:
        text: The raw response string (leading/trailing whitespace is stripped).

    Returns:
        The parsed float value.

    Raises:
        ValueError: If *text* cannot be parsed as a SCPI number.
    """
    token = text.strip().upper()
    special = _SPECIAL_FLOAT_MAP.get(token)
    if special is not None:
        return special
    try:
        return float(token)
    except ValueError:
        raise ValueError(f"Invalid SCPI number: {text!r}") from None


def parse_numbers(text: str) -> tuple[float, ...]:
    """Parse a comma-separated list of SCPI numbers.

    Args:
        text: Comma-separated numeric values (e.g. ``"1.0,2.0,3.0"``).

    Returns:
        A tuple of parsed float values.

    Raises:
        ValueError: If any element cannot be parsed.
    """
    return tuple(parse_number(part) for part in text.split(","))


def parse_int(text: str) -> int:
    """Parse a SCPI NR1 (integer) response.

    Args:
        text: The raw response string.

    Returns:
        The parsed integer.

    Raises:
        ValueError: If *text* is not a valid integer.
    """
    token = text.strip()
    try:
        return int(token)
    except ValueError:
        raise ValueError(f"Invalid SCPI integer: {text!r}") from None


def parse_bool(text: str) -> bool:
    """Parse a SCPI boolean response.

    Accepts ``"1"`` / ``"0"`` and ``"ON"`` / ``"OFF"`` (case-insensitive).

    Args:
        text: The raw response string.

    Returns:
        The parsed boolean.

    Raises:
        ValueError: If *text* is not a recognized boolean token.
    """
    token = text.strip().upper()
    if token in ("1", "ON"):
        return True
    if token in ("0", "OFF"):
        return False
    raise ValueError(f"Invalid SCPI boolean: {text!r}")


def parse_special(text: str) -> float | ScpiSpecial:
    """Parse a SCPI value that may be numeric or a special keyword.

    Returns a :class:`ScpiSpecial` member for ``MIN``, ``MAX``, and ``DEF``.
    Numeric values (including ``NAN``, ``INF``, ``NINF``) are returned as
    ``float``.

    Args:
        text: The raw response string.

    Returns:
        A float for numeric values, or a :class:`ScpiSpecial` for keywords.

    Raises:
        ValueError: If *text* cannot be parsed.
    """
    token = text.strip().upper()
    if token in _SPECIAL_KEYWORDS:
        return ScpiSpecial(token)
    return parse_number(text)


def format_number(value: float) -> str:
    """Format a float for use in a SCPI command.

    ``nan``, ``inf``, and ``-inf`` are rendered as ``NAN``, ``INF``, and
    ``NINF`` respectively.  Finite values use Python's default ``str()``
    representation.

    Args:
        value: The numeric value to format.

    Returns:
        A SCPI-compatible string representation.
    """
    if math.isnan(value):
        return "NAN"
    if math.isinf(value):
        return "NINF" if value < 0 else "INF"
    return str(value)


def format_bool(value: bool) -> str:
    """Format a boolean for use in a SCPI command.

    Args:
        value: The boolean to format.

    Returns:
        ``"1"`` for True, ``"0"`` for False.
    """
    return "1" if value else "0"
