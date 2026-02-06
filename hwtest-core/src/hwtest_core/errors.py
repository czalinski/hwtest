"""Exception types for hwtest-core.

This module defines the exception hierarchy used throughout the hwtest framework.
All hwtest exceptions inherit from HwtestError, allowing consumers to catch all
framework-specific errors with a single except clause.

Exception hierarchy:
    HwtestError (base)
    +-- TelemetryConnectionError: Connection failures
    +-- SerializationError: Serialization/deserialization failures
    +-- SchemaError: Schema validation failures
    +-- StateError: State machine violations
    +-- ThresholdError: Threshold configuration errors
"""


class HwtestError(Exception):
    """Base exception for all hwtest errors.

    This is the root of the hwtest exception hierarchy. Catch this to handle
    any framework-specific error.
    """


class TelemetryConnectionError(HwtestError):
    """Raised when connection to a telemetry server fails.

    This may occur during initial connection, reconnection attempts, or when
    a connection is unexpectedly lost during operation.
    """


class SerializationError(HwtestError):
    """Raised when serialization or deserialization fails.

    Common causes include malformed data, unexpected data types, or protocol
    version mismatches.
    """


class SchemaError(HwtestError):
    """Raised for schema-related errors.

    This includes schema ID mismatches between data and expected schema,
    invalid schema definitions, or missing required schema information.
    """


class StateError(HwtestError):
    """Raised for invalid state or state transition errors.

    This may occur when attempting an invalid state transition, querying
    state before initialization, or other state machine violations.
    """


class ThresholdError(HwtestError):
    """Raised for invalid threshold definitions.

    This includes invalid bound configurations, negative tolerances, or
    other threshold specification errors.
    """
