"""Exception types for hwtest-core."""


class HwtestError(Exception):
    """Base exception for all hwtest errors."""


class TelemetryConnectionError(HwtestError):
    """Failed to connect to telemetry server."""


class SerializationError(HwtestError):
    """Failed to serialize or deserialize a message."""


class SchemaError(HwtestError):
    """Schema-related error (mismatch, invalid, etc.)."""


class StateError(HwtestError):
    """Invalid state or state transition."""


class ThresholdError(HwtestError):
    """Invalid threshold definition."""
