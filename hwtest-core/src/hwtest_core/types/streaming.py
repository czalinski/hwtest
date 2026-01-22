"""Streaming data protocol types."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import Iterator

from hwtest_core.types.common import DataType, SourceId

# Message type constants
MSG_TYPE_SCHEMA = 0x01
MSG_TYPE_DATA = 0x02


def _encode_string(s: str) -> bytes:
    """Encode a string as length-prefixed UTF-8 (u8 length + data)."""
    encoded = s.encode("utf-8")
    if len(encoded) > 255:
        raise ValueError(f"String too long for encoding: {len(encoded)} bytes (max 255)")
    return struct.pack("!B", len(encoded)) + encoded


def _decode_string(data: bytes, offset: int) -> tuple[str, int]:
    """Decode a length-prefixed string, returning (string, new_offset)."""
    length = data[offset]
    start = offset + 1
    end = start + length
    return data[start:end].decode("utf-8"), end


@dataclass(frozen=True)
class StreamField:
    """Definition of a single field in a stream schema."""

    name: str
    dtype: DataType
    unit: str = ""

    def to_bytes(self) -> bytes:
        """Serialize the field definition."""
        return (
            _encode_string(self.name)
            + struct.pack("!B", self.dtype.value)
            + _encode_string(self.unit)
        )

    @classmethod
    def from_bytes(cls, data: bytes, offset: int) -> tuple[StreamField, int]:
        """Deserialize a field definition, returning (field, new_offset)."""
        name, offset = _decode_string(data, offset)
        dtype = DataType(data[offset])
        offset += 1
        unit, offset = _decode_string(data, offset)
        return cls(name=name, dtype=dtype, unit=unit), offset

    def _crc_data(self) -> bytes:
        """Return bytes used for CRC32 computation."""
        return (
            self.name.encode("utf-8")
            + struct.pack("!B", self.dtype.value)
            + self.unit.encode("utf-8")
        )


@dataclass(frozen=True)
class StreamSchema:
    """Schema defining the structure of a data stream."""

    source_id: SourceId
    fields: tuple[StreamField, ...]
    schema_id: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        """Compute the schema_id from field definitions."""
        # pylint: disable=protected-access  # _crc_data is internal to this module
        crc_data = b"".join(f._crc_data() for f in self.fields)
        computed_id = zlib.crc32(crc_data) & 0xFFFFFFFF
        object.__setattr__(self, "schema_id", computed_id)

    @property
    def sample_size(self) -> int:
        """Return the size in bytes of a single sample (all fields)."""
        return sum(f.dtype.size for f in self.fields)

    def get_field_offset(self, field_name: str) -> int | None:
        """Get the byte offset of a field within a sample, or None if not found."""
        offset = 0
        for f in self.fields:
            if f.name == field_name:
                return offset
            offset += f.dtype.size
        return None

    def get_field(self, field_name: str) -> StreamField | None:
        """Get a field by name, or None if not found."""
        for f in self.fields:
            if f.name == field_name:
                return f
        return None

    def to_bytes(self) -> bytes:
        """Serialize the schema to binary format."""
        result = struct.pack("!B", MSG_TYPE_SCHEMA)
        result += struct.pack("!I", self.schema_id)
        result += _encode_string(self.source_id)
        result += struct.pack("!H", len(self.fields))
        for f in self.fields:
            result += f.to_bytes()
        return result

    @classmethod
    def from_bytes(cls, data: bytes) -> StreamSchema:
        """Deserialize a schema from binary format."""
        offset = 0

        msg_type = data[offset]
        offset += 1
        if msg_type != MSG_TYPE_SCHEMA:
            raise ValueError(f"Invalid message type: expected {MSG_TYPE_SCHEMA}, got {msg_type}")

        expected_schema_id = struct.unpack("!I", data[offset : offset + 4])[0]
        offset += 4

        source_id, offset = _decode_string(data, offset)

        field_count = struct.unpack("!H", data[offset : offset + 2])[0]
        offset += 2

        fields = []
        for _ in range(field_count):
            field_obj, offset = StreamField.from_bytes(data, offset)
            fields.append(field_obj)

        schema = cls(source_id=SourceId(source_id), fields=tuple(fields))

        if schema.schema_id != expected_schema_id:
            raise ValueError(
                f"Schema ID mismatch: computed {schema.schema_id:#x}, "
                f"expected {expected_schema_id:#x}"
            )

        return schema


@dataclass(frozen=True)
class StreamData:
    """A batch of time-series samples."""

    schema_id: int
    timestamp_ns: int
    period_ns: int
    samples: tuple[tuple[int | float, ...], ...]

    @property
    def sample_count(self) -> int:
        """Return the number of samples in this batch."""
        return len(self.samples)

    def get_timestamp(self, sample_index: int) -> int:
        """Get the timestamp in nanoseconds for the sample at the given index."""
        return self.timestamp_ns + (sample_index * self.period_ns)

    def timestamps(self) -> Iterator[int]:
        """Iterate over timestamps for all samples."""
        for i in range(len(self.samples)):
            yield self.get_timestamp(i)

    def to_bytes(self, schema: StreamSchema) -> bytes:
        """Serialize the data to binary format using the provided schema."""
        if schema.schema_id != self.schema_id:
            raise ValueError(
                f"Schema ID mismatch: data has {self.schema_id:#x}, "
                f"schema has {schema.schema_id:#x}"
            )

        if len(schema.fields) == 0:
            raise ValueError("Schema has no fields")

        result = struct.pack("!B", MSG_TYPE_DATA)
        result += struct.pack("!I", self.schema_id)
        result += struct.pack("!Q", self.timestamp_ns)
        result += struct.pack("!Q", self.period_ns)
        result += struct.pack("!H", len(self.samples))

        # Build format string for one sample (big-endian)
        sample_format = "!" + "".join(f.dtype.struct_format for f in schema.fields)

        for sample in self.samples:
            if len(sample) != len(schema.fields):
                raise ValueError(
                    f"Sample has {len(sample)} values, schema has {len(schema.fields)} fields"
                )
            result += struct.pack(sample_format, *sample)

        return result

    @classmethod
    def from_bytes(cls, data: bytes, schema: StreamSchema) -> StreamData:
        """Deserialize data from binary format using the provided schema."""
        offset = 0

        msg_type = data[offset]
        offset += 1
        if msg_type != MSG_TYPE_DATA:
            raise ValueError(f"Invalid message type: expected {MSG_TYPE_DATA}, got {msg_type}")

        schema_id = struct.unpack("!I", data[offset : offset + 4])[0]
        offset += 4

        if schema_id != schema.schema_id:
            raise ValueError(
                f"Schema ID mismatch: data has {schema_id:#x}, schema has {schema.schema_id:#x}"
            )

        timestamp_ns = struct.unpack("!Q", data[offset : offset + 8])[0]
        offset += 8

        period_ns = struct.unpack("!Q", data[offset : offset + 8])[0]
        offset += 8

        sample_count = struct.unpack("!H", data[offset : offset + 2])[0]
        offset += 2

        # Build format string for one sample (big-endian)
        sample_format = "!" + "".join(f.dtype.struct_format for f in schema.fields)
        sample_size = schema.sample_size

        samples = []
        for _ in range(sample_count):
            values = struct.unpack(sample_format, data[offset : offset + sample_size])
            samples.append(values)
            offset += sample_size

        return cls(
            schema_id=schema_id,
            timestamp_ns=timestamp_ns,
            period_ns=period_ns,
            samples=tuple(samples),
        )
