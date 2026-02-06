"""Bound check types for YAML-configured field monitoring.

This module provides a suite of bound check classes for evaluating whether
measurement values fall within acceptable ranges. These are designed for
YAML-based configuration of per-field thresholds.

Each bound check type implements the BoundCheck protocol and supports
serialization to/from tagged dictionaries for YAML persistence.

Bound Check Types:
    WithinTolerance: Value within fractional tolerance of center.
    WithinRange: Value within absolute delta of center.
    WithinBaseline: Two-phase check with initial and tight bounds.
    LessThan: Value strictly less than limit.
    GreaterThan: Value strictly greater than limit.
    GoodInterval: Value within inclusive [low, high] interval.
    BadInterval: Value outside inclusive [low, high] interval.
    GoodValues: Rounded value in a set of allowed integers.
    BadValues: Rounded value not in a set of forbidden integers.
    Special: Special checks (e.g., "any" always passes).

Example:
    >>> check = WithinTolerance(center=5.0, fraction=0.1)
    >>> check.check(5.4)  # Within 10% of 5.0
    True
    >>> check.check(6.0)  # Outside tolerance
    False
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from hwtest_core.errors import ThresholdError


@runtime_checkable
class BoundCheck(Protocol):
    """Protocol defining the interface for all bound check types.

    Bound checks evaluate whether a measurement value satisfies some
    constraint. All implementations must be serializable to/from tagged
    dictionaries for YAML configuration support.
    """

    def check(self, value: float) -> bool:
        """Evaluate whether a value satisfies this bound check.

        Args:
            value: The measurement value to check.

        Returns:
            True if the value satisfies the constraint, False otherwise.
        """

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a tagged dictionary.

        Returns:
            A dictionary with a single key identifying the bound type,
            mapping to the bound's configuration data.
        """

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BoundCheck:
        """Deserialize from a tagged dictionary.

        Args:
            data: Dictionary with a single key identifying the bound type.

        Returns:
            A bound check instance.
        """


@dataclass(frozen=True)
class WithinTolerance:
    """Check that value is within a fractional tolerance of a center value.

    The tolerance band is calculated as [center * (1 - fraction), center * (1 + fraction)].
    Handles negative centers correctly by using min/max of the two bounds.

    Attributes:
        center: The nominal/expected value.
        fraction: Fractional tolerance (e.g., 0.1 for 10%). Must be >= 0.

    Raises:
        ThresholdError: If fraction is negative.

    Example:
        >>> check = WithinTolerance(center=100.0, fraction=0.05)
        >>> check.check(102.0)  # Within 5%
        True
        >>> check.check(110.0)  # Outside 5%
        False
    """

    center: float
    fraction: float

    def __post_init__(self) -> None:
        """Validate that fraction is non-negative."""
        if self.fraction < 0:
            raise ThresholdError(f"WithinTolerance fraction must be >= 0, got {self.fraction}")

    def check(self, value: float) -> bool:
        """Check if value is within the tolerance band.

        Args:
            value: The value to check.

        Returns:
            True if center * (1 - fraction) <= value <= center * (1 + fraction).
        """
        a = self.center * (1 - self.fraction)
        b = self.center * (1 + self.fraction)
        return min(a, b) <= value <= max(a, b)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a tagged dictionary.

        Returns:
            Dictionary with key "within_tolerance" and [center, fraction] value.
        """
        return {"within_tolerance": [self.center, self.fraction]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WithinTolerance:
        """Deserialize from a tagged dictionary.

        Args:
            data: Dictionary with "within_tolerance" key.

        Returns:
            A WithinTolerance instance.
        """
        args = data["within_tolerance"]
        return cls(center=float(args[0]), fraction=float(args[1]))


@dataclass(frozen=True)
class WithinRange:
    """Check that value is within an absolute delta of a center value.

    The acceptable range is [center - delta, center + delta].

    Attributes:
        center: The nominal/expected value.
        delta: Absolute tolerance (must be >= 0).

    Raises:
        ThresholdError: If delta is negative.

    Example:
        >>> check = WithinRange(center=3.3, delta=0.1)
        >>> check.check(3.35)  # Within +/- 0.1
        True
        >>> check.check(3.5)  # Outside range
        False
    """

    center: float
    delta: float

    def __post_init__(self) -> None:
        """Validate that delta is non-negative."""
        if self.delta < 0:
            raise ThresholdError(f"WithinRange delta must be >= 0, got {self.delta}")

    def check(self, value: float) -> bool:
        """Check if value is within the absolute delta of center.

        Args:
            value: The value to check.

        Returns:
            True if center - delta <= value <= center + delta.
        """
        return self.center - self.delta <= value <= self.center + self.delta

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a tagged dictionary.

        Returns:
            Dictionary with key "within_range" and [center, delta] value.
        """
        return {"within_range": [self.center, self.delta]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WithinRange:
        """Deserialize from a tagged dictionary.

        Args:
            data: Dictionary with "within_range" key.

        Returns:
            A WithinRange instance.
        """
        args = data["within_range"]
        return cls(center=float(args[0]), delta=float(args[1]))


@dataclass
class WithinBaseline:
    """Two-phase bound check with initial acquisition and tight tracking.

    This check operates in two phases:
    - Phase 1 (unlocked): Value must be within nominal +/- init_delta.
      On the first passing check, the actual value becomes the locked baseline.
    - Phase 2 (locked): Value must be within baseline +/- tight_delta.

    This is useful for measurements that should stabilize around an initial
    reading and then track closely to that value.

    Attributes:
        nominal: Expected nominal value for initial acquisition.
        init_delta: Initial acquisition tolerance (must be >= 0).
        tight_delta: Tight tracking tolerance after lock (must be >= 0).

    Raises:
        ThresholdError: If init_delta or tight_delta is negative.

    Example:
        >>> check = WithinBaseline(nominal=5.0, init_delta=0.5, tight_delta=0.1)
        >>> check.check(5.2)  # Locks baseline to 5.2
        True
        >>> check.check(5.25)  # Within tight_delta of 5.2
        True
        >>> check.check(5.5)  # Outside tight_delta
        False
    """

    nominal: float
    init_delta: float
    tight_delta: float
    _baseline: float | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Validate that deltas are non-negative."""
        if self.init_delta < 0:
            raise ThresholdError(f"WithinBaseline init_delta must be >= 0, got {self.init_delta}")
        if self.tight_delta < 0:
            raise ThresholdError(f"WithinBaseline tight_delta must be >= 0, got {self.tight_delta}")

    @property
    def is_locked(self) -> bool:
        """Check if the baseline has been locked.

        Returns:
            True if a baseline value has been captured.
        """
        return self._baseline is not None

    @property
    def baseline_value(self) -> float | None:
        """Get the locked baseline value.

        Returns:
            The locked baseline value, or None if still in Phase 1.
        """
        return self._baseline

    def reset(self) -> None:
        """Reset to unlocked state (Phase 1).

        Clears the locked baseline, requiring a new acquisition.
        """
        self._baseline = None

    def check(self, value: float) -> bool:
        """Check value against current phase bounds.

        In Phase 1, checks against nominal +/- init_delta and locks on success.
        In Phase 2, checks against baseline +/- tight_delta.

        Args:
            value: The value to check.

        Returns:
            True if value passes the current phase check.
        """
        if self._baseline is None:
            # Phase 1: initial range
            if self.nominal - self.init_delta <= value <= self.nominal + self.init_delta:
                self._baseline = value
                return True
            return False
        # Phase 2: tight range around baseline
        return self._baseline - self.tight_delta <= value <= self._baseline + self.tight_delta

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a tagged dictionary.

        Returns:
            Dictionary with key "within_baseline" and configuration list.
            If locked, includes the baseline value as a fourth element.
        """
        args: list[float] = [self.nominal, self.init_delta, self.tight_delta]
        if self._baseline is not None:
            args.append(self._baseline)
        return {"within_baseline": args}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WithinBaseline:
        """Deserialize from a tagged dictionary.

        Args:
            data: Dictionary with "within_baseline" key.

        Returns:
            A WithinBaseline instance, optionally with restored baseline.
        """
        args = data["within_baseline"]
        instance = cls(
            nominal=float(args[0]),
            init_delta=float(args[1]),
            tight_delta=float(args[2]),
        )
        if len(args) >= 4:
            instance._baseline = float(args[3])  # noqa: SLF001
        return instance


@dataclass(frozen=True)
class LessThan:
    """Check that value is strictly less than a limit.

    Attributes:
        limit: The upper limit (exclusive).

    Example:
        >>> check = LessThan(limit=100.0)
        >>> check.check(99.9)
        True
        >>> check.check(100.0)
        False
    """

    limit: float

    def check(self, value: float) -> bool:
        """Check if value is strictly less than the limit.

        Args:
            value: The value to check.

        Returns:
            True if value < limit.
        """
        return value < self.limit

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a tagged dictionary.

        Returns:
            Dictionary with key "less_than" and the limit value.
        """
        return {"less_than": self.limit}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LessThan:
        """Deserialize from a tagged dictionary.

        Args:
            data: Dictionary with "less_than" key.

        Returns:
            A LessThan instance.
        """
        return cls(limit=float(data["less_than"]))


@dataclass(frozen=True)
class GreaterThan:
    """Check that value is strictly greater than a limit.

    Attributes:
        limit: The lower limit (exclusive).

    Example:
        >>> check = GreaterThan(limit=0.0)
        >>> check.check(0.1)
        True
        >>> check.check(0.0)
        False
    """

    limit: float

    def check(self, value: float) -> bool:
        """Check if value is strictly greater than the limit.

        Args:
            value: The value to check.

        Returns:
            True if value > limit.
        """
        return value > self.limit

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a tagged dictionary.

        Returns:
            Dictionary with key "greater_than" and the limit value.
        """
        return {"greater_than": self.limit}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GreaterThan:
        """Deserialize from a tagged dictionary.

        Args:
            data: Dictionary with "greater_than" key.

        Returns:
            A GreaterThan instance.
        """
        return cls(limit=float(data["greater_than"]))


@dataclass(frozen=True)
class GoodInterval:
    """Check that value is within an inclusive interval [low, high].

    Attributes:
        low: Lower bound (inclusive).
        high: Upper bound (inclusive). Must be >= low.

    Raises:
        ThresholdError: If low > high.

    Example:
        >>> check = GoodInterval(low=0.0, high=5.0)
        >>> check.check(2.5)
        True
        >>> check.check(5.1)
        False
    """

    low: float
    high: float

    def __post_init__(self) -> None:
        """Validate that low <= high."""
        if self.low > self.high:
            raise ThresholdError(
                f"GoodInterval requires low <= high, got low={self.low}, high={self.high}"
            )

    def check(self, value: float) -> bool:
        """Check if value is within the interval.

        Args:
            value: The value to check.

        Returns:
            True if low <= value <= high.
        """
        return self.low <= value <= self.high

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a tagged dictionary.

        Returns:
            Dictionary with key "good_interval" and [low, high] value.
        """
        return {"good_interval": [self.low, self.high]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoodInterval:
        """Deserialize from a tagged dictionary.

        Args:
            data: Dictionary with "good_interval" key.

        Returns:
            A GoodInterval instance.
        """
        args = data["good_interval"]
        return cls(low=float(args[0]), high=float(args[1]))


@dataclass(frozen=True)
class BadInterval:
    """Check that value is outside an inclusive interval [low, high].

    Values inside the interval [low, high] are considered bad (failing).
    This is the inverse of GoodInterval.

    Attributes:
        low: Lower bound of the forbidden interval.
        high: Upper bound of the forbidden interval. Must be >= low.

    Raises:
        ThresholdError: If low > high.

    Example:
        >>> check = BadInterval(low=1.0, high=2.0)
        >>> check.check(0.5)  # Outside forbidden interval
        True
        >>> check.check(1.5)  # Inside forbidden interval
        False
    """

    low: float
    high: float

    def __post_init__(self) -> None:
        """Validate that low <= high."""
        if self.low > self.high:
            raise ThresholdError(
                f"BadInterval requires low <= high, got low={self.low}, high={self.high}"
            )

    def check(self, value: float) -> bool:
        """Check if value is outside the forbidden interval.

        Args:
            value: The value to check.

        Returns:
            True if value < low or value > high (outside the interval).
        """
        return value < self.low or value > self.high

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a tagged dictionary.

        Returns:
            Dictionary with key "bad_interval" and [low, high] value.
        """
        return {"bad_interval": [self.low, self.high]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BadInterval:
        """Deserialize from a tagged dictionary.

        Args:
            data: Dictionary with "bad_interval" key.

        Returns:
            A BadInterval instance.
        """
        args = data["bad_interval"]
        return cls(low=float(args[0]), high=float(args[1]))


@dataclass(frozen=True)
class GoodValues:
    """Check that rounded value is in a set of allowed integer values.

    The value is rounded to the nearest integer before checking membership.
    Useful for discrete state or mode validation.

    Attributes:
        values: Frozenset of allowed integer values.

    Example:
        >>> check = GoodValues(values=frozenset({0, 1, 2}))
        >>> check.check(1.4)  # Rounds to 1
        True
        >>> check.check(2.6)  # Rounds to 3
        False
    """

    values: frozenset[int]

    def check(self, value: float) -> bool:
        """Check if rounded value is in the allowed set.

        Args:
            value: The value to check (will be rounded to nearest integer).

        Returns:
            True if round(value) is in the allowed set.
        """
        return round(value) in self.values

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a tagged dictionary.

        Returns:
            Dictionary with key "good_values" and sorted list of integers.
        """
        return {"good_values": sorted(self.values)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoodValues:
        """Deserialize from a tagged dictionary.

        Args:
            data: Dictionary with "good_values" key.

        Returns:
            A GoodValues instance.
        """
        return cls(values=frozenset(int(v) for v in data["good_values"]))


@dataclass(frozen=True)
class BadValues:
    """Check that rounded value is NOT in a set of forbidden integer values.

    The value is rounded to the nearest integer before checking membership.
    This is the inverse of GoodValues.

    Attributes:
        values: Frozenset of forbidden integer values.

    Example:
        >>> check = BadValues(values=frozenset({-1, 255}))
        >>> check.check(0.0)  # Not in forbidden set
        True
        >>> check.check(-0.6)  # Rounds to -1, which is forbidden
        False
    """

    values: frozenset[int]

    def check(self, value: float) -> bool:
        """Check if rounded value is NOT in the forbidden set.

        Args:
            value: The value to check (will be rounded to nearest integer).

        Returns:
            True if round(value) is NOT in the forbidden set.
        """
        return round(value) not in self.values

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a tagged dictionary.

        Returns:
            Dictionary with key "bad_values" and sorted list of integers.
        """
        return {"bad_values": sorted(self.values)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BadValues:
        """Deserialize from a tagged dictionary.

        Args:
            data: Dictionary with "bad_values" key.

        Returns:
            A BadValues instance.
        """
        return cls(values=frozenset(int(v) for v in data["bad_values"]))


@dataclass(frozen=True)
class Special:
    """Special bound check with predefined behaviors.

    Currently supports:
    - kind="any": Always passes (accepts any value).

    Attributes:
        kind: The special check type identifier.

    Example:
        >>> check = Special(kind="any")
        >>> check.check(float('inf'))
        True
    """

    kind: str

    def check(self, value: float) -> bool:  # pylint: disable=unused-argument
        """Evaluate the special bound check.

        Args:
            value: The value to check (ignored for kind="any").

        Returns:
            True for kind="any" (always passes).
        """
        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a tagged dictionary.

        Returns:
            Dictionary with key "special" and the kind string.
        """
        return {"special": self.kind}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Special:
        """Deserialize from a tagged dictionary.

        Args:
            data: Dictionary with "special" key.

        Returns:
            A Special instance.
        """
        return cls(kind=str(data["special"]))


_BOUND_REGISTRY: dict[
    str,
    type[
        WithinTolerance
        | WithinRange
        | WithinBaseline
        | LessThan
        | GreaterThan
        | GoodInterval
        | BadInterval
        | GoodValues
        | BadValues
        | Special
    ],
] = {
    "within_tolerance": WithinTolerance,
    "within_range": WithinRange,
    "within_baseline": WithinBaseline,
    "less_than": LessThan,
    "greater_than": GreaterThan,
    "good_interval": GoodInterval,
    "bad_interval": BadInterval,
    "good_values": GoodValues,
    "bad_values": BadValues,
    "special": Special,
}


def bound_check_from_dict(data: dict[str, Any]) -> BoundCheck:
    """Create a BoundCheck from a tagged dictionary.

    Args:
        data: Dictionary with exactly one key identifying the bound type.

    Returns:
        A BoundCheck instance.

    Raises:
        ThresholdError: If the dictionary is malformed or the key is unknown.
    """
    if len(data) != 1:
        raise ThresholdError(
            f"Bound check dict must have exactly one key, got {len(data)}: {list(data.keys())}"
        )
    key = next(iter(data))
    bound_cls = _BOUND_REGISTRY.get(key)
    if bound_cls is None:
        raise ThresholdError(f"Unknown bound check type: {key!r}")
    return bound_cls.from_dict(data)
