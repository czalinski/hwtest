"""Bound check types for YAML-configured field monitoring (implemented in Rust).

Each check serializes to a single-key dict, which is also its YAML form::

    voltage:     {within_range: [3.3, 0.1]}        # center, delta
    ripple:      {within_tolerance: [5.0, 0.02]}   # center, fraction
    temperature: {within_baseline: [25, 5, 1]}     # nominal, init_delta, tight_delta
    current:     {less_than: 2.0}
    fan_rpm:     {greater_than: 1000}
    supply:      {good_interval: [4.75, 5.25]}
    resonance:   {bad_interval: [120, 130]}
    mode:        {good_values: [0, 1, 2]}          # value rounded half-to-even
    status:      {bad_values: [255]}
    debug:       {special: any}

Non-finite measurements (NaN, inf) fail every check except ``special: any``.
Unknown ``special`` kinds are rejected with ``ThresholdError``.

Example:
    >>> check = bound_check_from_dict({"within_tolerance": [5.0, 0.1]})
    >>> check.check(5.4)
    True
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from hwtest_core._native import (
    BadInterval,
    BadValues,
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


@runtime_checkable
class BoundCheck(Protocol):
    """Interface implemented by all bound check types."""

    def check(self, value: float) -> bool:
        """Return True if ``value`` satisfies the constraint."""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a single-key tagged dict."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BoundCheck:
        """Deserialize from a single-key tagged dict."""


__all__ = [
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
]
