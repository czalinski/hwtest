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
    Monitor,
    StatePublisher,
    StateSubscriber,
    StreamPublisher,
    StreamSubscriber,
    TelemetryPublisher,
    TelemetrySubscriber,
    ThresholdProvider,
)
from hwtest_core.types import (
    BadInterval,
    BadValues,
    BoundCheck,
    BoundType,
    ChannelId,
    DataType,
    EnvironmentalState,
    GoodInterval,
    GoodValues,
    GreaterThan,
    InstrumentIdentity,
    LessThan,
    MonitorId,
    MonitorResult,
    MonitorVerdict,
    SourceId,
    Special,
    StateId,
    StateThresholds,
    StateTransition,
    StreamData,
    StreamField,
    StreamSchema,
    TelemetryMessage,
    TelemetryValue,
    Threshold,
    ThresholdBound,
    ThresholdViolation,
    Timestamp,
    ValueQuality,
    WithinBaseline,
    WithinRange,
    WithinTolerance,
    bound_check_from_dict,
)

__version__ = "0.1.0"

__all__ = [
    # Version
    "__version__",
    # Common types
    "ChannelId",
    "DataType",
    "InstrumentIdentity",
    "MonitorId",
    "SourceId",
    "StateId",
    "Timestamp",
    # Streaming types
    "StreamData",
    "StreamField",
    "StreamSchema",
    # Telemetry types
    "TelemetryMessage",
    "TelemetryValue",
    "ValueQuality",
    # State types
    "EnvironmentalState",
    "StateTransition",
    # Threshold types
    "BoundType",
    "StateThresholds",
    "Threshold",
    "ThresholdBound",
    # Bound check types
    "BadInterval",
    "BadValues",
    "BoundCheck",
    "GoodInterval",
    "GoodValues",
    "GreaterThan",
    "LessThan",
    "Special",
    "WithinBaseline",
    "WithinRange",
    "WithinTolerance",
    "bound_check_from_dict",
    # Monitor types
    "MonitorResult",
    "MonitorVerdict",
    "ThresholdViolation",
    # Streaming interfaces
    "StreamPublisher",
    "StreamSubscriber",
    # Telemetry interfaces
    "TelemetryPublisher",
    "TelemetrySubscriber",
    # State interfaces
    "StatePublisher",
    "StateSubscriber",
    # Monitor interfaces
    "Monitor",
    "ThresholdProvider",
    # Errors
    "HwtestError",
    "SchemaError",
    "SerializationError",
    "StateError",
    "TelemetryConnectionError",
    "ThresholdError",
]
