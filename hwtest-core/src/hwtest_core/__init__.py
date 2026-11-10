"""Core library for hardware test automation.

This package provides the foundational data types and error types for the
hwtest hardware test automation framework. Everything is implemented in Rust
(``crates/``) and exposed through the ``hwtest_core._native`` extension; only
enums and exceptions are plain Python. Test scripts use the ``hwtest``
package on top of this. It has no Python runtime dependencies.

Key components:
    - Types: Common types (Timestamp, SourceId, ChannelId), telemetry types
      (TelemetryValue, TelemetryMessage), streaming protocol types (StreamSchema,
      StreamData), environmental state types, threshold definitions, and monitor
      result types.
    - Errors: Hierarchy of exception types for various failure modes.

Example:
    >>> from hwtest_core import Timestamp, DataType, StreamSchema, StreamField
    >>> schema = StreamSchema(
    ...     source_id=SourceId("voltage_sensor"),
    ...     fields=(StreamField("voltage", DataType.F64, "V"),)
    ... )
    >>> print(f"Schema ID: {schema.schema_id:#x}")
"""

from hwtest_core.errors import (
    HwtestError,
    SchemaError,
    SerializationError,
    StateError,
    TelemetryConnectionError,
    ThresholdError,
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
    # Errors
    "HwtestError",
    "SchemaError",
    "SerializationError",
    "StateError",
    "TelemetryConnectionError",
    "ThresholdError",
]
