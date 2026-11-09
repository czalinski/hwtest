"""Telemetry value types for JSON transport (implemented in Rust).

Classes:
    ValueQuality: Quality indicator for measurement values.
    TelemetryValue: A single measurement with metadata.
    TelemetryMessage: A batch of telemetry values from a source.

Non-finite values (NaN, inf) are written to JSON as ``null`` and read back as
NaN, so messages stay valid JSON for non-Python consumers.
"""

from hwtest_core._enums import ValueQuality
from hwtest_core._native import TelemetryMessage, TelemetryValue

__all__ = ["TelemetryMessage", "TelemetryValue", "ValueQuality"]
