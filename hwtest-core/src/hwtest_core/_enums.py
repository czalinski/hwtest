"""Enumerations shared by the Python API and the Rust core.

These stay as real Python ``Enum`` classes (value lookup, iteration, pickling)
and mirror the Rust enums in ``crates/hwtest-core``; the native extension
converts between them by value. Keep the two in sync.
"""

from __future__ import annotations

from enum import Enum


class DataType(Enum):
    """Data type codes for the binary streaming protocol.

    These codes identify the binary representation of values in streaming
    data messages. Each type has an associated size, struct format character,
    and category (signed/unsigned integer or floating point).

    Attributes:
        I8: 8-bit signed integer.
        I16: 16-bit signed integer.
        I32: 32-bit signed integer.
        I64: 64-bit signed integer.
        U8: 8-bit unsigned integer.
        U16: 16-bit unsigned integer.
        U32: 32-bit unsigned integer.
        U64: 64-bit unsigned integer.
        F32: 32-bit IEEE 754 floating point.
        F64: 64-bit IEEE 754 floating point.
    """

    I8 = 0x01
    I16 = 0x02
    I32 = 0x03
    I64 = 0x04
    U8 = 0x05
    U16 = 0x06
    U32 = 0x07
    U64 = 0x08
    F32 = 0x09
    F64 = 0x0A

    @property
    def size(self) -> int:
        """Return the size in bytes for this data type.

        Returns:
            Number of bytes required to store a value of this type.
        """
        sizes = {
            DataType.I8: 1,
            DataType.I16: 2,
            DataType.I32: 4,
            DataType.I64: 8,
            DataType.U8: 1,
            DataType.U16: 2,
            DataType.U32: 4,
            DataType.U64: 8,
            DataType.F32: 4,
            DataType.F64: 8,
        }
        return sizes[self]

    @property
    def struct_format(self) -> str:
        """Return the struct format character for this data type.

        The format character is suitable for use with Python's struct module
        in big-endian mode (prepend '!' to the format string).

        Returns:
            Single character struct format code.
        """
        formats = {
            DataType.I8: "b",
            DataType.I16: "h",
            DataType.I32: "i",
            DataType.I64: "q",
            DataType.U8: "B",
            DataType.U16: "H",
            DataType.U32: "I",
            DataType.U64: "Q",
            DataType.F32: "f",
            DataType.F64: "d",
        }
        return formats[self]

    @property
    def is_signed(self) -> bool:
        """Check if this is a signed integer type.

        Returns:
            True if this type is I8, I16, I32, or I64.
        """
        return self in (DataType.I8, DataType.I16, DataType.I32, DataType.I64)

    @property
    def is_unsigned(self) -> bool:
        """Check if this is an unsigned integer type.

        Returns:
            True if this type is U8, U16, U32, or U64.
        """
        return self in (DataType.U8, DataType.U16, DataType.U32, DataType.U64)

    @property
    def is_float(self) -> bool:
        """Check if this is a floating point type.

        Returns:
            True if this type is F32 or F64.
        """
        return self in (DataType.F32, DataType.F64)


class ValueQuality(Enum):
    """Quality indicator for telemetry values.

    Attributes:
        GOOD: Value is valid and current.
        UNCERTAIN: Value may be inaccurate (e.g., sensor warming up).
        BAD: Value is known to be invalid (e.g., sensor fault).
        STALE: Value is outdated (e.g., communication timeout).
    """

    GOOD = "good"
    UNCERTAIN = "uncertain"
    BAD = "bad"
    STALE = "stale"


class BoundType(Enum):
    """Whether a threshold bound includes its limit value.

    Attributes:
        INCLUSIVE: ``value >= low`` / ``value <= high``.
        EXCLUSIVE: ``value > low`` / ``value < high``.
    """

    INCLUSIVE = "inclusive"
    EXCLUSIVE = "exclusive"


class MonitorVerdict(Enum):
    """Outcome of a monitor evaluation.

    Attributes:
        PASS: All checked values within limits.
        FAIL: At least one threshold violation.
        SKIP: Evaluation skipped (e.g., during a state transition).
        ERROR: The evaluation itself failed.
    """

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"
