"""hwtest-core: Core library with data types and interface definitions."""

from hwtest_core.errors import (
    HwtestError,
    SchemaError,
    SerializationError,
    StateError,
    TelemetryConnectionError,
    ThresholdError,
)
from hwtest_core.interfaces import (
    StreamPublisher,
    StreamSubscriber,
)
from hwtest_core.types import (
    ChannelId,
    DataType,
    MonitorId,
    SourceId,
    StateId,
    StreamData,
    StreamField,
    StreamSchema,
    Timestamp,
)

__version__ = "0.1.0"

__all__ = [
    # Version
    "__version__",
    # Types
    "ChannelId",
    "DataType",
    "MonitorId",
    "SourceId",
    "StateId",
    "StreamData",
    "StreamField",
    "StreamSchema",
    "Timestamp",
    # Interfaces
    "StreamPublisher",
    "StreamSubscriber",
    # Errors
    "HwtestError",
    "SchemaError",
    "SerializationError",
    "StateError",
    "TelemetryConnectionError",
    "ThresholdError",
]
