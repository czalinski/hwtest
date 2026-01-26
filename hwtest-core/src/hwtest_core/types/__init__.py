"""Core data types for hwtest."""

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
