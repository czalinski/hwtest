"""Binary streaming data protocol types.

This module implements a compact binary protocol for high-throughput telemetry
streaming. It is designed for low-latency (<25ms software budget) transmission
of time-series data from instruments to monitors and loggers.

Protocol Overview:
    - Big-endian byte order throughout
    - Schema messages (0x01): Describe the data format, retransmitted every 1 second
    - Data messages (0x02): Packed samples with implicit timestamps

Schema ID:
    Each schema has a CRC32-based ID computed from its field definitions.
    This allows receivers to detect schema changes without transmitting
    the full schema with every data message.

Classes:
    StreamField: Definition of a single field in a schema.
    StreamSchema: Complete schema defining a data stream's structure.
    StreamData: A batch of time-series samples.

Constants:
    MSG_TYPE_SCHEMA: Message type code for schema messages (0x01).
    MSG_TYPE_DATA: Message type code for data messages (0x02).

Example:
    >>> from hwtest_core.types import DataType, SourceId
    >>> field = StreamField(name="voltage", dtype=DataType.F64, unit="V")
    >>> schema = StreamSchema(
    ...     source_id=SourceId("sensor"),
    ...     fields=(field,)
    ... )
    >>> data = StreamData(
    ...     schema_id=schema.schema_id,
    ...     timestamp_ns=time.time_ns(),
    ...     period_ns=1_000_000,  # 1ms sample period
    ...     samples=((3.3,), (3.31,), (3.29,))
    ... )
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import Iterator

from hwtest_core.types.common import DataType, SourceId

# Message type constants
MSG_TYPE_SCHEMA = 0x01
"""Message type code for schema messages."""

MSG_TYPE_DATA = 0x02
"""Message type code for data messages."""


def _encode_string(s: str) -> bytes:
    """Encode a string as length-prefixed UTF-8 (u8 length + data).

    Args:
        s: The string to encode.

    Returns:
        Length byte followed by UTF-8 encoded string data.

    Raises:
        ValueError: If encoded string exceeds 255 bytes.
    """
    encoded = s.encode("utf-8")
    if len(encoded) > 255:
        raise ValueError(f"String too long for encoding: {len(encoded)} bytes (max 255)")
    return struct.pack("!B", len(encoded)) + encoded


def _decode_string(data: bytes, offset: int) -> tuple[str, int]:
    """Decode a length-prefixed string from a byte buffer.

    Args:
        data: The byte buffer containing the encoded string.
        offset: Starting position in the buffer.

    Returns:
        Tuple of (decoded string, new offset after the string).
    """
    length = data[offset]
    start = offset + 1
    end = start + length
    return data[start:end].decode("utf-8"), end


@dataclass(frozen=True)
class StreamField:
    """Definition of a single field in a stream schema.

    Each field has a name, data type, and optional unit. Fields are
    serialized in order within each sample of the data stream.

    Attributes:
        name: Field name (max 255 bytes when UTF-8 encoded).
        dtype: Data type for binary encoding.
        unit: Unit of measurement (e.g., "V", "A", "degC").
    """

    name: str
    dtype: DataType
    unit: str = ""

    def to_bytes(self) -> bytes:
        """Serialize the field definition to binary format.

        Returns:
            Binary representation: name + dtype + unit.
        """
        return (
            _encode_string(self.name)
            + struct.pack("!B", self.dtype.value)
            + _encode_string(self.unit)
        )

    @classmethod
    def from_bytes(cls, data: bytes, offset: int) -> tuple[StreamField, int]:
        """Deserialize a field definition from a byte buffer.

        Args:
            data: The byte buffer containing the field definition.
            offset: Starting position in the buffer.

        Returns:
            Tuple of (StreamField instance, new offset after the field).
        """
        name, offset = _decode_string(data, offset)
        dtype = DataType(data[offset])
        offset += 1
        unit, offset = _decode_string(data, offset)
        return cls(name=name, dtype=dtype, unit=unit), offset

    def _crc_data(self) -> bytes:
        """Return bytes used for schema ID CRC32 computation.

        Returns:
            Concatenated name, dtype, and unit bytes (without length prefixes).
        """
        return (
            self.name.encode("utf-8")
            + struct.pack("!B", self.dtype.value)
            + self.unit.encode("utf-8")
        )


@dataclass(frozen=True)
class StreamSchema:
    """Schema defining the structure and metadata of a data stream.

    The schema describes the fields in each sample, their data types,
    and units. A CRC32-based schema_id is automatically computed from
    the field definitions for change detection.

    Attributes:
        source_id: Identifier of the data source.
        fields: Tuple of field definitions in sample order.
        schema_id: Auto-computed CRC32 ID for the schema (read-only).

    Example:
        >>> schema = StreamSchema(
        ...     source_id=SourceId("adc"),
        ...     fields=(
        ...         StreamField("ch0", DataType.F64, "V"),
        ...         StreamField("ch1", DataType.F64, "V"),
        ...     )
        ... )
        >>> print(f"Schema ID: {schema.schema_id:#x}")
        >>> print(f"Sample size: {schema.sample_size} bytes")
    """

    source_id: SourceId
    fields: tuple[StreamField, ...]
    schema_id: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        """Compute the schema_id from field definitions using CRC32."""
        # pylint: disable=protected-access  # _crc_data is internal to this module
        crc_data = b"".join(f._crc_data() for f in self.fields)
        computed_id = zlib.crc32(crc_data) & 0xFFFFFFFF
        object.__setattr__(self, "schema_id", computed_id)

    @property
    def sample_size(self) -> int:
        """Calculate the total size in bytes of a single sample.

        Returns:
            Sum of all field sizes.
        """
        return sum(f.dtype.size for f in self.fields)

    def get_field_offset(self, field_name: str) -> int | None:
        """Get the byte offset of a field within a sample.

        Args:
            field_name: Name of the field to find.

        Returns:
            Byte offset from the start of a sample, or None if not found.
        """
        offset = 0
        for f in self.fields:
            if f.name == field_name:
                return offset
            offset += f.dtype.size
        return None

    def get_field(self, field_name: str) -> StreamField | None:
        """Get a field definition by name.

        Args:
            field_name: Name of the field to find.

        Returns:
            The StreamField, or None if not found.
        """
        for f in self.fields:
            if f.name == field_name:
                return f
        return None

    def to_bytes(self) -> bytes:
        """Serialize the schema to binary format.

        Format: msg_type(1) + schema_id(4) + source_id + field_count(2) + fields...

        Returns:
            Binary schema message.
        """
        result = struct.pack("!B", MSG_TYPE_SCHEMA)
        result += struct.pack("!I", self.schema_id)
        result += _encode_string(self.source_id)
        result += struct.pack("!H", len(self.fields))
        for f in self.fields:
            result += f.to_bytes()
        return result

    @classmethod
    def from_bytes(cls, data: bytes) -> StreamSchema:
        """Deserialize a schema from binary format.

        Args:
            data: Binary schema message.

        Returns:
            A StreamSchema instance.

        Raises:
            ValueError: If message type is wrong or schema ID doesn't match.
        """
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
    """A batch of time-series samples with implicit timestamps.

    Contains multiple samples from a single schema. Timestamps for individual
    samples are computed from the base timestamp and sample period, avoiding
    the overhead of storing timestamps with each sample.

    Attributes:
        schema_id: CRC32 ID of the schema describing this data.
        timestamp_ns: Timestamp of the first sample (nanoseconds since epoch).
        period_ns: Time between consecutive samples (nanoseconds).
        samples: Tuple of samples, each a tuple of field values.

    Example:
        >>> data = StreamData(
        ...     schema_id=schema.schema_id,
        ...     timestamp_ns=time.time_ns(),
        ...     period_ns=1_000_000,  # 1ms period = 1kHz
        ...     samples=((3.3, 0.1), (3.31, 0.11), (3.29, 0.09))
        ... )
        >>> for ts in data.timestamps():
        ...     print(f"Sample at {ts} ns")
    """

    schema_id: int
    timestamp_ns: int
    period_ns: int
    samples: tuple[tuple[int | float, ...], ...]

    @property
    def sample_count(self) -> int:
        """Get the number of samples in this batch.

        Returns:
            Number of samples.
        """
        return len(self.samples)

    def get_timestamp(self, sample_index: int) -> int:
        """Calculate the timestamp for a specific sample.

        Args:
            sample_index: Zero-based index of the sample.

        Returns:
            Timestamp in nanoseconds since epoch.
        """
        return self.timestamp_ns + (sample_index * self.period_ns)

    def timestamps(self) -> Iterator[int]:
        """Iterate over timestamps for all samples.

        Yields:
            Timestamp in nanoseconds for each sample in order.
        """
        for i in range(len(self.samples)):
            yield self.get_timestamp(i)

    def to_bytes(self, schema: StreamSchema) -> bytes:
        """Serialize the data to binary format.

        Format: msg_type(1) + schema_id(4) + timestamp(8) + period(8) +
                sample_count(2) + packed_samples...

        Args:
            schema: The schema describing the data structure.

        Returns:
            Binary data message.

        Raises:
            ValueError: If schema_id doesn't match, schema has no fields,
                       or sample field count doesn't match schema.
        """
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
        """Deserialize data from binary format.

        Args:
            data: Binary data message.
            schema: The schema describing the data structure.

        Returns:
            A StreamData instance.

        Raises:
            ValueError: If message type is wrong or schema_id doesn't match.
        """
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
