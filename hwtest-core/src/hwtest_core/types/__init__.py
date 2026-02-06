"""Core data types for hwtest.

This package provides all fundamental data types used throughout the hwtest
framework. Types are organized into submodules by domain:

Submodules:
    common: Base types (Timestamp, SourceId, ChannelId, DataType, InstrumentIdentity)
    telemetry: Telemetry data types (TelemetryValue, TelemetryMessage, ValueQuality)
    streaming: Binary streaming protocol types (StreamField, StreamSchema, StreamData)
    state: Environmental state types (EnvironmentalState, StateTransition)
    threshold: Threshold types (Threshold, ThresholdBound, StateThresholds)
    bounds: Bound check types for field monitoring (WithinTolerance, WithinRange, etc.)
    monitor: Monitor result types (MonitorResult, MonitorVerdict, ThresholdViolation)

All types are exported from this package for convenience.
"""

from hwtest_core.types.bounds import (
    BadInterval,
    BadValues,
    BoundCheck,
    GoodInterval,
    GoodValues,
    GreaterThan,
    LessThan,
    Special,
    WithinBaseline,
    WithinRange,
    WithinTolerance,
    bound_check_from_dict,
)
from hwtest_core.types.common import (
    ChannelId,
    DataType,
    InstrumentIdentity,
    MonitorId,
    SourceId,
    StateId,
    Timestamp,
)
from hwtest_core.types.monitor import (
    MonitorResult,
    MonitorVerdict,
    ThresholdViolation,
)
from hwtest_core.types.state import (
    EnvironmentalState,
    StateTransition,
)
from hwtest_core.types.streaming import (
    StreamData,
    StreamField,
    StreamSchema,
)
from hwtest_core.types.telemetry import (
    TelemetryMessage,
    TelemetryValue,
    ValueQuality,
)
from hwtest_core.types.threshold import (
    BoundType,
    StateThresholds,
    Threshold,
    ThresholdBound,
)

__all__ = [
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
]
