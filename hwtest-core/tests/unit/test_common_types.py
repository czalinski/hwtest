"""Tests for common types."""

from datetime import datetime, timezone

import pytest

from hwtest_core.types.common import DataType, InstrumentIdentity, Timestamp


class TestDataType:
    """Tests for the DataType enum."""

    def test_type_codes(self) -> None:
        """Verify type codes match specification."""
        assert DataType.I8.value == 0x01
        assert DataType.I16.value == 0x02
        assert DataType.I32.value == 0x03
        assert DataType.I64.value == 0x04
        assert DataType.U8.value == 0x05
        assert DataType.U16.value == 0x06
        assert DataType.U32.value == 0x07
        assert DataType.U64.value == 0x08
        assert DataType.F32.value == 0x09
        assert DataType.F64.value == 0x0A

    def test_sizes(self) -> None:
        """Verify sizes for each type."""
        assert DataType.I8.size == 1
        assert DataType.I16.size == 2
        assert DataType.I32.size == 4
        assert DataType.I64.size == 8
        assert DataType.U8.size == 1
        assert DataType.U16.size == 2
        assert DataType.U32.size == 4
        assert DataType.U64.size == 8
        assert DataType.F32.size == 4
        assert DataType.F64.size == 8

    def test_struct_formats(self) -> None:
        """Verify struct format characters."""
        assert DataType.I8.struct_format == "b"
        assert DataType.I16.struct_format == "h"
        assert DataType.I32.struct_format == "i"
        assert DataType.I64.struct_format == "q"
        assert DataType.U8.struct_format == "B"
        assert DataType.U16.struct_format == "H"
        assert DataType.U32.struct_format == "I"
        assert DataType.U64.struct_format == "Q"
        assert DataType.F32.struct_format == "f"
        assert DataType.F64.struct_format == "d"

    def test_is_signed(self) -> None:
        """Verify signed type detection."""
        assert DataType.I8.is_signed is True
        assert DataType.I16.is_signed is True
        assert DataType.I32.is_signed is True
        assert DataType.I64.is_signed is True
        assert DataType.U8.is_signed is False
        assert DataType.F32.is_signed is False

    def test_is_unsigned(self) -> None:
        """Verify unsigned type detection."""
        assert DataType.U8.is_unsigned is True
        assert DataType.U16.is_unsigned is True
        assert DataType.U32.is_unsigned is True
        assert DataType.U64.is_unsigned is True
        assert DataType.I8.is_unsigned is False
        assert DataType.F32.is_unsigned is False

    def test_is_float(self) -> None:
        """Verify float type detection."""
        assert DataType.F32.is_float is True
        assert DataType.F64.is_float is True
        assert DataType.I32.is_float is False
        assert DataType.U64.is_float is False


class TestInstrumentIdentity:
    """Tests for the InstrumentIdentity class."""

    def test_fields(self) -> None:
        identity = InstrumentIdentity(
            manufacturer="B&K Precision",
            model="9115",
            serial="SN000001",
            firmware="V1.00-V1.00",
        )
        assert identity.manufacturer == "B&K Precision"
        assert identity.model == "9115"
        assert identity.serial == "SN000001"
        assert identity.firmware == "V1.00-V1.00"

    def test_immutable(self) -> None:
        identity = InstrumentIdentity(manufacturer="Test", model="M1", serial="S1", firmware="F1")
        with pytest.raises(AttributeError):
            identity.model = "M2"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = InstrumentIdentity("Mfr", "Model", "SN1", "FW1")
        b = InstrumentIdentity("Mfr", "Model", "SN1", "FW1")
        assert a == b

    def test_inequality(self) -> None:
        a = InstrumentIdentity("Mfr", "Model", "SN1", "FW1")
        b = InstrumentIdentity("Mfr", "Model", "SN2", "FW1")
        assert a != b


class TestTimestamp:
    """Tests for the Timestamp class."""

    def test_now(self) -> None:
        """Test creating a timestamp for current time."""
        ts = Timestamp.now()
        assert ts.unix_ns > 0
        assert ts.source == "local"

    def test_now_with_source(self) -> None:
        """Test creating a timestamp with custom source."""
        ts = Timestamp.now(source="ptp")
        assert ts.source == "ptp"

    def test_from_datetime(self) -> None:
        """Test creating a timestamp from datetime."""
        dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        ts = Timestamp.from_datetime(dt)
        assert ts.unix_ns == 1705320000000000000
        assert ts.source == "local"

    def test_to_datetime(self) -> None:
        """Test converting timestamp to datetime."""
        ts = Timestamp(unix_ns=1705320000000000000)
        dt = ts.to_datetime()
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15
        assert dt.hour == 12
        assert dt.minute == 0
        assert dt.second == 0

    def test_unix_seconds(self) -> None:
        """Test unix_seconds property."""
        ts = Timestamp(unix_ns=1705320000500000000)
        assert ts.unix_seconds == pytest.approx(1705320000.5)

    def test_unix_ms(self) -> None:
        """Test unix_ms property."""
        ts = Timestamp(unix_ns=1705320000500000000)
        assert ts.unix_ms == 1705320000500

    def test_unix_us(self) -> None:
        """Test unix_us property."""
        ts = Timestamp(unix_ns=1705320000500123000)
        assert ts.unix_us == 1705320000500123

    def test_immutable(self) -> None:
        """Test that Timestamp is immutable."""
        ts = Timestamp(unix_ns=1000, source="test")
        with pytest.raises(AttributeError):
            ts.unix_ns = 2000  # type: ignore[misc]
