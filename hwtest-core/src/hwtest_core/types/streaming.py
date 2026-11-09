"""Binary streaming protocol (implemented in Rust).

Big-endian wire format; strings are a u8 length plus UTF-8 bytes (max 255).

Schema message (``MSG_TYPE_SCHEMA``, retransmitted periodically)::

    u8 type | u32 schema_id | str source_id | u16 field_count
    | field_count * (str name | u8 dtype | str unit)

Data message (``MSG_TYPE_DATA``)::

    u8 type | u32 schema_id | u64 timestamp_ns | u64 period_ns | u16 sample_count
    | sample_count * (one packed value per field)

``schema_id`` is the CRC32 of each field's ``name || dtype || unit``.

Classes:
    StreamField: A field definition (name, ``DataType``, unit).
    StreamSchema: Field layout with computed CRC32 ``schema_id``.
    StreamData: A batch of evenly spaced samples.
"""

from hwtest_core._native import MSG_TYPE_DATA, MSG_TYPE_SCHEMA
from hwtest_core._native import StreamData, StreamField, StreamSchema

# Private helpers re-exported for backward compatibility (used by tests).
from hwtest_core._native import (  # pylint: disable=unused-import
    _decode_string,
    _encode_string,
)

__all__ = [
    "MSG_TYPE_DATA",
    "MSG_TYPE_SCHEMA",
    "StreamData",
    "StreamField",
    "StreamSchema",
]
