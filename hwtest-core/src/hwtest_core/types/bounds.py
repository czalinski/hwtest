"""Bound check types for YAML-configured field monitoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from hwtest_core.errors import ThresholdError


@runtime_checkable
class BoundCheck(Protocol):
    """Protocol for all bound check types."""

    def check(self, value: float) -> bool:
        """Return True if value satisfies the bound check."""

    def to_dict(self) -> dict[str, Any]:
        """Convert to tagged dictionary for serialization."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BoundCheck:
        """Create from tagged dictionary."""


@dataclass(frozen=True)
class WithinTolerance:
    """Check that value is within a fractional tolerance of a center value.

    Handles negative centers correctly by using min/max of the two bounds.
    """

    center: float
    fraction: float

    def __post_init__(self) -> None:
        if self.fraction < 0:
            raise ThresholdError(f"WithinTolerance fraction must be >= 0, got {self.fraction}")

    def check(self, value: float) -> bool:
        """Return True if value is within center ± center*fraction."""
        a = self.center * (1 - self.fraction)
        b = self.center * (1 + self.fraction)
        return min(a, b) <= value <= max(a, b)

    def to_dict(self) -> dict[str, Any]:
        """Convert to tagged dictionary."""
        return {"within_tolerance": [self.center, self.fraction]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WithinTolerance:
        """Create from tagged dictionary."""
        args = data["within_tolerance"]
        return cls(center=float(args[0]), fraction=float(args[1]))


@dataclass(frozen=True)
class WithinRange:
    """Check that value is within an absolute delta of a center value."""

    center: float
    delta: float

    def __post_init__(self) -> None:
        if self.delta < 0:
            raise ThresholdError(f"WithinRange delta must be >= 0, got {self.delta}")

    def check(self, value: float) -> bool:
        """Return True if center-delta <= value <= center+delta."""
        return self.center - self.delta <= value <= self.center + self.delta

    def to_dict(self) -> dict[str, Any]:
        """Convert to tagged dictionary."""
        return {"within_range": [self.center, self.delta]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WithinRange:
        """Create from tagged dictionary."""
        args = data["within_range"]
        return cls(center=float(args[0]), delta=float(args[1]))


@dataclass
class WithinBaseline:
    """Two-phase bound check: initial range then tightened around locked baseline.

    Phase 1 (unlocked): value must be within nominal ± init_delta.
    On the first passing check, the baseline locks to that value.
    Phase 2 (locked): value must be within baseline ± tight_delta.
    """

    nominal: float
    init_delta: float
    tight_delta: float
    _baseline: float | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.init_delta < 0:
            raise ThresholdError(f"WithinBaseline init_delta must be >= 0, got {self.init_delta}")
        if self.tight_delta < 0:
            raise ThresholdError(f"WithinBaseline tight_delta must be >= 0, got {self.tight_delta}")

    @property
    def is_locked(self) -> bool:
        """Return True if the baseline has been locked."""
        return self._baseline is not None

    @property
    def baseline_value(self) -> float | None:
        """Return the locked baseline value, or None if unlocked."""
        return self._baseline

    def reset(self) -> None:
        """Reset to unlocked state."""
        self._baseline = None

    def check(self, value: float) -> bool:
        """Check value against the current phase bounds."""
        if self._baseline is None:
            # Phase 1: initial range
            if self.nominal - self.init_delta <= value <= self.nominal + self.init_delta:
                self._baseline = value
                return True
            return False
        # Phase 2: tight range around baseline
        return self._baseline - self.tight_delta <= value <= self._baseline + self.tight_delta

    def to_dict(self) -> dict[str, Any]:
        """Convert to tagged dictionary."""
        args: list[float] = [self.nominal, self.init_delta, self.tight_delta]
        if self._baseline is not None:
            args.append(self._baseline)
        return {"within_baseline": args}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WithinBaseline:
        """Create from tagged dictionary."""
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
    """Check that value is strictly less than a limit."""

    limit: float

    def check(self, value: float) -> bool:
        """Return True if value < limit."""
        return value < self.limit

    def to_dict(self) -> dict[str, Any]:
        """Convert to tagged dictionary."""
        return {"less_than": self.limit}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LessThan:
        """Create from tagged dictionary."""
        return cls(limit=float(data["less_than"]))


@dataclass(frozen=True)
class GreaterThan:
    """Check that value is strictly greater than a limit."""

    limit: float

    def check(self, value: float) -> bool:
        """Return True if value > limit."""
        return value > self.limit

    def to_dict(self) -> dict[str, Any]:
        """Convert to tagged dictionary."""
        return {"greater_than": self.limit}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GreaterThan:
        """Create from tagged dictionary."""
        return cls(limit=float(data["greater_than"]))


@dataclass(frozen=True)
class GoodInterval:
    """Check that value is within an inclusive interval [low, high]."""

    low: float
    high: float

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise ThresholdError(
                f"GoodInterval requires low <= high, got low={self.low}, high={self.high}"
            )

    def check(self, value: float) -> bool:
        """Return True if low <= value <= high."""
        return self.low <= value <= self.high

    def to_dict(self) -> dict[str, Any]:
        """Convert to tagged dictionary."""
        return {"good_interval": [self.low, self.high]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoodInterval:
        """Create from tagged dictionary."""
        args = data["good_interval"]
        return cls(low=float(args[0]), high=float(args[1]))


@dataclass(frozen=True)
class BadInterval:
    """Check that value is outside an inclusive interval [low, high]."""

    low: float
    high: float

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise ThresholdError(
                f"BadInterval requires low <= high, got low={self.low}, high={self.high}"
            )

    def check(self, value: float) -> bool:
        """Return True if value is NOT in [low, high]."""
        return value < self.low or value > self.high

    def to_dict(self) -> dict[str, Any]:
        """Convert to tagged dictionary."""
        return {"bad_interval": [self.low, self.high]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BadInterval:
        """Create from tagged dictionary."""
        args = data["bad_interval"]
        return cls(low=float(args[0]), high=float(args[1]))


@dataclass(frozen=True)
class GoodValues:
    """Check that rounded value is in a set of allowed integer values."""

    values: frozenset[int]

    def check(self, value: float) -> bool:
        """Return True if round(value) is in the allowed set."""
        return round(value) in self.values

    def to_dict(self) -> dict[str, Any]:
        """Convert to tagged dictionary."""
        return {"good_values": sorted(self.values)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoodValues:
        """Create from tagged dictionary."""
        return cls(values=frozenset(int(v) for v in data["good_values"]))


@dataclass(frozen=True)
class BadValues:
    """Check that rounded value is NOT in a set of forbidden integer values."""

    values: frozenset[int]

    def check(self, value: float) -> bool:
        """Return True if round(value) is NOT in the forbidden set."""
        return round(value) not in self.values

    def to_dict(self) -> dict[str, Any]:
        """Convert to tagged dictionary."""
        return {"bad_values": sorted(self.values)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BadValues:
        """Create from tagged dictionary."""
        return cls(values=frozenset(int(v) for v in data["bad_values"]))


@dataclass(frozen=True)
class Special:
    """Special bound check (e.g., kind="any" always passes)."""

    kind: str

    def check(self, value: float) -> bool:  # pylint: disable=unused-argument
        """Return True (always passes for kind='any')."""
        return True

    def to_dict(self) -> dict[str, Any]:
        """Convert to tagged dictionary."""
        return {"special": self.kind}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Special:
        """Create from tagged dictionary."""
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
