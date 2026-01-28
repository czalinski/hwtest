"""Tests for streaming protocol types."""

import struct

import pytest

from hwtest_core.types.common import DataType, SourceId
from hwtest_core.types.streaming import (
    MSG_TYPE_DATA,
    MSG_TYPE_SCHEMA,
    StreamData,
    StreamField,
    StreamSchema,
    _decode_string,
    _encode_string,
)


class TestStringEncoding:
    """Tests for string encoding/decoding helpers."""

    def test_encode_empty_string(self) -> None:
        """Test encoding an empty string."""
        result = _encode_string("")
        assert result == b"\x00"

    def test_encode_simple_string(self) -> None:
        """Test encoding a simple ASCII string."""
        result = _encode_string("hello")
        assert result == b"\x05hello"

    def test_encode_unicode_string(self) -> None:
        """Test encoding a UTF-8 string."""
        result = _encode_string("°C")
        encoded = "°C".encode("utf-8")
        assert result == bytes([len(encoded)]) + encoded

    def test_encode_max_length_string(self) -> None:
        """Test encoding a string at max length (255 bytes)."""
        s = "a" * 255
        result = _encode_string(s)
        assert result[0] == 255
        assert len(result) == 256

    def test_encode_too_long_string(self) -> None:
        """Test that encoding a too-long string raises ValueError."""
        s = "a" * 256
        with pytest.raises(ValueError, match="String too long"):
            _encode_string(s)

    def test_decode_string(self) -> None:
        """Test decoding a string."""
        data = b"\x05hello\x03end"
        result, offset = _decode_string(data, 0)
        assert result == "hello"
        assert offset == 6

    def test_decode_string_at_offset(self) -> None:
        """Test decoding a string at a non-zero offset."""
        data = b"xxx\x05hello"
        result, offset = _decode_string(data, 3)
        assert result == "hello"
        assert offset == 9

    def test_roundtrip_string(self) -> None:
        """Test encoding and decoding a string."""
        original = "test_channel"
        encoded = _encode_string(original)
        decoded, _ = _decode_string(encoded, 0)
        assert decoded == original


class TestStreamField:
    """Tests for StreamField."""

    def test_create_field(self) -> None:
        """Test creating a field."""
        field = StreamField(name="voltage", dtype=DataType.F32, unit="V")
        assert field.name == "voltage"
        assert field.dtype == DataType.F32
        assert field.unit == "V"

    def test_create_field_no_unit(self) -> None:
        """Test creating a field without a unit."""
        field = StreamField(name="count", dtype=DataType.U32)
        assert field.unit == ""

    def test_to_bytes(self) -> None:
        """Test serializing a field."""
        field = StreamField(name="ch0", dtype=DataType.F32, unit="V")
        data = field.to_bytes()

        # Verify structure: name (length + data) + dtype + unit (length + data)
        assert data[0] == 3  # "ch0" length
        assert data[1:4] == b"ch0"
        assert data[4] == DataType.F32.value
        assert data[5] == 1  # "V" length
        assert data[6:7] == b"V"

    def test_from_bytes(self) -> None:
        """Test deserializing a field."""
        original = StreamField(name="temperature", dtype=DataType.F64, unit="°C")
        data = original.to_bytes()

        restored, offset = StreamField.from_bytes(data, 0)
        assert restored.name == original.name
        assert restored.dtype == original.dtype
        assert restored.unit == original.unit

    def test_roundtrip(self) -> None:
        """Test serialization roundtrip."""
        field = StreamField(name="current", dtype=DataType.F32, unit="mA")
        data = field.to_bytes()
        restored, _ = StreamField.from_bytes(data, 0)
        assert restored == field

    def test_immutable(self) -> None:
        """Test that StreamField is immutable."""
        field = StreamField(name="test", dtype=DataType.I32)
        with pytest.raises(AttributeError):
            field.name = "changed"  # type: ignore[misc]


class TestStreamSchema:
    """Tests for StreamSchema."""

    def test_create_schema(self) -> None:
        """Test creating a schema."""
        fields = (
            StreamField(name="ch0", dtype=DataType.F32, unit="V"),
            StreamField(name="ch1", dtype=DataType.F32, unit="V"),
        )
        schema = StreamSchema(source_id=SourceId("sensor1"), fields=fields)

        assert schema.source_id == "sensor1"
        assert len(schema.fields) == 2
        assert schema.schema_id != 0  # CRC32 computed

    def test_schema_id_computed(self) -> None:
        """Test that schema_id is computed from fields."""
        fields1 = (StreamField(name="a", dtype=DataType.F32, unit="V"),)
        fields2 = (StreamField(name="b", dtype=DataType.F32, unit="V"),)

        schema1 = StreamSchema(source_id=SourceId("s"), fields=fields1)
        schema2 = StreamSchema(source_id=SourceId("s"), fields=fields2)

        assert schema1.schema_id != schema2.schema_id

    def test_schema_id_ignores_source_id(self) -> None:
        """Test that schema_id depends only on fields, not source_id."""
        fields = (StreamField(name="x", dtype=DataType.I32),)

        schema1 = StreamSchema(source_id=SourceId("sensor1"), fields=fields)
        schema2 = StreamSchema(source_id=SourceId("sensor2"), fields=fields)

        assert schema1.schema_id == schema2.schema_id

    def test_sample_size(self) -> None:
        """Test sample_size calculation."""
        fields = (
            StreamField(name="a", dtype=DataType.F32),  # 4 bytes
            StreamField(name="b", dtype=DataType.I64),  # 8 bytes
            StreamField(name="c", dtype=DataType.U8),  # 1 byte
        )
        schema = StreamSchema(source_id=SourceId("s"), fields=fields)
        assert schema.sample_size == 13

    def test_get_field_offset(self) -> None:
        """Test getting field offset."""
        fields = (
            StreamField(name="a", dtype=DataType.I32),  # offset 0, size 4
            StreamField(name="b", dtype=DataType.F64),  # offset 4, size 8
            StreamField(name="c", dtype=DataType.U8),  # offset 12, size 1
        )
        schema = StreamSchema(source_id=SourceId("s"), fields=fields)

        assert schema.get_field_offset("a") == 0
        assert schema.get_field_offset("b") == 4
        assert schema.get_field_offset("c") == 12
        assert schema.get_field_offset("nonexistent") is None

    def test_get_field(self) -> None:
        """Test getting field by name."""
        field_a = StreamField(name="a", dtype=DataType.I32)
        field_b = StreamField(name="b", dtype=DataType.F64)
        schema = StreamSchema(source_id=SourceId("s"), fields=(field_a, field_b))

        assert schema.get_field("a") == field_a
        assert schema.get_field("b") == field_b
        assert schema.get_field("c") is None

    def test_to_bytes(self) -> None:
        """Test serializing a schema."""
        fields = (StreamField(name="v", dtype=DataType.F32, unit="V"),)
        schema = StreamSchema(source_id=SourceId("src"), fields=fields)
        data = schema.to_bytes()

        # Verify message type
        assert data[0] == MSG_TYPE_SCHEMA

        # Verify schema_id (4 bytes, big-endian)
        schema_id = struct.unpack("!I", data[1:5])[0]
        assert schema_id == schema.schema_id

    def test_from_bytes(self) -> None:
        """Test deserializing a schema."""
        fields = (
            StreamField(name="ch0", dtype=DataType.F32, unit="V"),
            StreamField(name="ch1", dtype=DataType.I16, unit="mA"),
        )
        original = StreamSchema(source_id=SourceId("device1"), fields=fields)
        data = original.to_bytes()

        restored = StreamSchema.from_bytes(data)
        assert restored.source_id == original.source_id
        assert restored.schema_id == original.schema_id
        assert len(restored.fields) == len(original.fields)
        assert restored.fields == original.fields

    def test_from_bytes_invalid_type(self) -> None:
        """Test that from_bytes rejects wrong message type."""
        data = bytes([MSG_TYPE_DATA]) + b"\x00" * 20
        with pytest.raises(ValueError, match="Invalid message type"):
            StreamSchema.from_bytes(data)

    def test_roundtrip(self) -> None:
        """Test serialization roundtrip."""
        fields = (
            StreamField(name="voltage", dtype=DataType.F32, unit="V"),
            StreamField(name="current", dtype=DataType.F32, unit="A"),
            StreamField(name="temp", dtype=DataType.F64, unit="°C"),
        )
        original = StreamSchema(source_id=SourceId("multimeter"), fields=fields)
        data = original.to_bytes()
        restored = StreamSchema.from_bytes(data)

        assert restored == original


class TestStreamData:
    """Tests for StreamData."""

    @pytest.fixture
    def sample_schema(self) -> StreamSchema:
        """Create a sample schema for testing."""
        fields = (
            StreamField(name="ch0", dtype=DataType.F32, unit="V"),
            StreamField(name="ch1", dtype=DataType.F32, unit="V"),
            StreamField(name="ch2", dtype=DataType.F32, unit="V"),
        )
        return StreamSchema(source_id=SourceId("adc"), fields=fields)

    def test_create_data(self, sample_schema: StreamSchema) -> None:
        """Test creating a data message."""
        data = StreamData(
            schema_id=sample_schema.schema_id,
            timestamp_ns=1000000000,
            period_ns=1000000,
            samples=((1.0, 2.0, 3.0), (1.1, 2.1, 3.1)),
        )
        assert data.sample_count == 2
        assert data.timestamp_ns == 1000000000
        assert data.period_ns == 1000000

    def test_get_timestamp(self, sample_schema: StreamSchema) -> None:
        """Test getting timestamp for each sample."""
        data = StreamData(
            schema_id=sample_schema.schema_id,
            timestamp_ns=1000000000,
            period_ns=1000000,  # 1ms
            samples=((1.0, 2.0, 3.0), (1.1, 2.1, 3.1), (1.2, 2.2, 3.2)),
        )

        assert data.get_timestamp(0) == 1000000000
        assert data.get_timestamp(1) == 1001000000
        assert data.get_timestamp(2) == 1002000000

    def test_timestamps_iterator(self, sample_schema: StreamSchema) -> None:
        """Test iterating over timestamps."""
        data = StreamData(
            schema_id=sample_schema.schema_id,
            timestamp_ns=1000000000,
            period_ns=500000,  # 0.5ms
            samples=((1.0, 2.0, 3.0), (1.1, 2.1, 3.1)),
        )

        timestamps = list(data.timestamps())
        assert timestamps == [1000000000, 1000500000]

    def test_to_bytes(self, sample_schema: StreamSchema) -> None:
        """Test serializing data."""
        data = StreamData(
            schema_id=sample_schema.schema_id,
            timestamp_ns=1704067200000000000,
            period_ns=1000000,
            samples=((3.3, 5.0, 12.0),),
        )
        binary = data.to_bytes(sample_schema)

        # Verify message type
        assert binary[0] == MSG_TYPE_DATA

        # Verify schema_id
        schema_id = struct.unpack("!I", binary[1:5])[0]
        assert schema_id == sample_schema.schema_id

        # Verify timestamp
        timestamp = struct.unpack("!Q", binary[5:13])[0]
        assert timestamp == 1704067200000000000

        # Verify period
        period = struct.unpack("!Q", binary[13:21])[0]
        assert period == 1000000

        # Verify sample count
        count = struct.unpack("!H", binary[21:23])[0]
        assert count == 1

    def test_to_bytes_schema_mismatch(self, sample_schema: StreamSchema) -> None:
        """Test that to_bytes rejects mismatched schema_id."""
        data = StreamData(
            schema_id=0x12345678,
            timestamp_ns=1000000000,
            period_ns=1000000,
            samples=((1.0, 2.0, 3.0),),
        )
        with pytest.raises(ValueError, match="Schema ID mismatch"):
            data.to_bytes(sample_schema)

    def test_to_bytes_wrong_field_count(self, sample_schema: StreamSchema) -> None:
        """Test that to_bytes rejects wrong number of values."""
        data = StreamData(
            schema_id=sample_schema.schema_id,
            timestamp_ns=1000000000,
            period_ns=1000000,
            samples=((1.0, 2.0),),  # Missing one field
        )
        with pytest.raises(ValueError, match="Sample has 2 values"):
            data.to_bytes(sample_schema)

    def test_from_bytes(self, sample_schema: StreamSchema) -> None:
        """Test deserializing data."""
        original = StreamData(
            schema_id=sample_schema.schema_id,
            timestamp_ns=1704067200000000000,
            period_ns=1000000,
            samples=((3.3, 5.0, 12.0), (3.29, 4.99, 11.9)),
        )
        binary = original.to_bytes(sample_schema)

        restored = StreamData.from_bytes(binary, sample_schema)
        assert restored.schema_id == original.schema_id
        assert restored.timestamp_ns == original.timestamp_ns
        assert restored.period_ns == original.period_ns
        assert restored.sample_count == original.sample_count

        # Check values (with floating point tolerance)
        for orig_sample, rest_sample in zip(original.samples, restored.samples):
            for orig_val, rest_val in zip(orig_sample, rest_sample):
                assert rest_val == pytest.approx(orig_val, rel=1e-6)

    def test_from_bytes_invalid_type(self, sample_schema: StreamSchema) -> None:
        """Test that from_bytes rejects wrong message type."""
        data = bytes([MSG_TYPE_SCHEMA]) + b"\x00" * 30
        with pytest.raises(ValueError, match="Invalid message type"):
            StreamData.from_bytes(data, sample_schema)

    def test_from_bytes_schema_mismatch(self, sample_schema: StreamSchema) -> None:
        """Test that from_bytes rejects mismatched schema_id."""
        # Create valid header with wrong schema_id
        data = struct.pack("!B", MSG_TYPE_DATA)
        data += struct.pack("!I", 0x12345678)  # Wrong schema_id
        data += struct.pack("!Q", 1000000000)
        data += struct.pack("!Q", 1000000)
        data += struct.pack("!H", 0)

        with pytest.raises(ValueError, match="Schema ID mismatch"):
            StreamData.from_bytes(data, sample_schema)

    def test_roundtrip(self, sample_schema: StreamSchema) -> None:
        """Test serialization roundtrip."""
        original = StreamData(
            schema_id=sample_schema.schema_id,
            timestamp_ns=1704067200000000000,
            period_ns=1000000,
            samples=(
                (3.30, 5.02, 12.1),
                (3.29, 5.01, 12.0),
                (3.31, 5.03, 12.2),
            ),
        )
        binary = original.to_bytes(sample_schema)
        restored = StreamData.from_bytes(binary, sample_schema)

        assert restored.schema_id == original.schema_id
        assert restored.timestamp_ns == original.timestamp_ns
        assert restored.period_ns == original.period_ns
        assert restored.sample_count == original.sample_count

    def test_roundtrip_integer_types(self) -> None:
        """Test roundtrip with integer data types."""
        fields = (
            StreamField(name="i8", dtype=DataType.I8),
            StreamField(name="u16", dtype=DataType.U16),
            StreamField(name="i32", dtype=DataType.I32),
            StreamField(name="u64", dtype=DataType.U64),
        )
        schema = StreamSchema(source_id=SourceId("ints"), fields=fields)

        original = StreamData(
            schema_id=schema.schema_id,
            timestamp_ns=1000000000,
            period_ns=1000000,
            samples=(
                (-128, 65535, -2147483648, 18446744073709551615),
                (127, 0, 2147483647, 0),
            ),
        )
        binary = original.to_bytes(schema)
        restored = StreamData.from_bytes(binary, schema)

        assert restored.samples == original.samples

    def test_network_byte_order(self, sample_schema: StreamSchema) -> None:
        """Test that data is encoded in network byte order (big-endian)."""
        data = StreamData(
            schema_id=sample_schema.schema_id,
            timestamp_ns=0x0102030405060708,
            period_ns=0x1112131415161718,
            samples=(),
        )
        binary = data.to_bytes(sample_schema)

        # Check timestamp bytes (big-endian)
        timestamp_bytes = binary[5:13]
        assert timestamp_bytes == bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08])

        # Check period bytes (big-endian)
        period_bytes = binary[13:21]
        assert period_bytes == bytes([0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18])
