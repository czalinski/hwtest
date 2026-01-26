"""Tests for bound check types."""

import pytest

from hwtest_core.errors import ThresholdError
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


class TestWithinTolerance:
    """Tests for WithinTolerance bound check."""

    def test_check_within(self) -> None:
        """Test value within tolerance passes."""
        bt = WithinTolerance(center=10.0, fraction=0.1)
        assert bt.check(10.0) is True
        assert bt.check(9.0) is True  # 10 * 0.9 = 9.0
        assert bt.check(11.0) is True  # 10 * 1.1 = 11.0
        assert bt.check(9.5) is True

    def test_check_outside(self) -> None:
        """Test value outside tolerance fails."""
        bt = WithinTolerance(center=10.0, fraction=0.1)
        assert bt.check(8.9) is False
        assert bt.check(11.1) is False

    def test_negative_center(self) -> None:
        """Test tolerance with negative center handles min/max correctly."""
        bt = WithinTolerance(center=-10.0, fraction=0.1)
        # a = -10 * 0.9 = -9, b = -10 * 1.1 = -11
        # min(-9, -11) = -11, max(-9, -11) = -9
        assert bt.check(-10.0) is True
        assert bt.check(-11.0) is True
        assert bt.check(-9.0) is True
        assert bt.check(-8.9) is False
        assert bt.check(-11.1) is False

    def test_zero_center(self) -> None:
        """Test tolerance with zero center (degenerate: only 0 passes)."""
        bt = WithinTolerance(center=0.0, fraction=0.1)
        assert bt.check(0.0) is True
        assert bt.check(0.001) is False

    def test_zero_fraction(self) -> None:
        """Test zero fraction (exact match only)."""
        bt = WithinTolerance(center=5.0, fraction=0.0)
        assert bt.check(5.0) is True
        assert bt.check(5.001) is False

    def test_negative_fraction_raises(self) -> None:
        """Test that negative fraction raises ThresholdError."""
        with pytest.raises(ThresholdError, match="fraction must be >= 0"):
            WithinTolerance(center=10.0, fraction=-0.1)

    def test_to_dict(self) -> None:
        """Test serialization."""
        bt = WithinTolerance(center=10.0, fraction=0.1)
        assert bt.to_dict() == {"within_tolerance": [10.0, 0.1]}

    def test_from_dict(self) -> None:
        """Test deserialization."""
        bt = WithinTolerance.from_dict({"within_tolerance": [10.0, 0.1]})
        assert bt.center == 10.0
        assert bt.fraction == 0.1

    def test_roundtrip(self) -> None:
        """Test serialization roundtrip."""
        original = WithinTolerance(center=-5.0, fraction=0.2)
        restored = WithinTolerance.from_dict(original.to_dict())
        assert restored == original


class TestWithinRange:
    """Tests for WithinRange bound check."""

    def test_check_within(self) -> None:
        """Test value within range passes."""
        br = WithinRange(center=100.0, delta=5.0)
        assert br.check(100.0) is True
        assert br.check(95.0) is True
        assert br.check(105.0) is True
        assert br.check(99.0) is True

    def test_check_outside(self) -> None:
        """Test value outside range fails."""
        br = WithinRange(center=100.0, delta=5.0)
        assert br.check(94.9) is False
        assert br.check(105.1) is False

    def test_zero_delta(self) -> None:
        """Test zero delta (exact match only)."""
        br = WithinRange(center=42.0, delta=0.0)
        assert br.check(42.0) is True
        assert br.check(42.001) is False

    def test_negative_delta_raises(self) -> None:
        """Test that negative delta raises ThresholdError."""
        with pytest.raises(ThresholdError, match="delta must be >= 0"):
            WithinRange(center=10.0, delta=-1.0)

    def test_to_dict(self) -> None:
        """Test serialization."""
        br = WithinRange(center=100.0, delta=5.0)
        assert br.to_dict() == {"within_range": [100.0, 5.0]}

    def test_from_dict(self) -> None:
        """Test deserialization."""
        br = WithinRange.from_dict({"within_range": [100.0, 5.0]})
        assert br.center == 100.0
        assert br.delta == 5.0

    def test_roundtrip(self) -> None:
        """Test serialization roundtrip."""
        original = WithinRange(center=3.3, delta=0.15)
        restored = WithinRange.from_dict(original.to_dict())
        assert restored == original


class TestWithinBaseline:
    """Tests for WithinBaseline bound check."""

    def test_phase1_pass_locks_baseline(self) -> None:
        """Test that first passing value locks the baseline."""
        bb = WithinBaseline(nominal=20.0, init_delta=4.0, tight_delta=2.0)
        assert bb.is_locked is False
        assert bb.baseline_value is None

        assert bb.check(19.0) is True  # Within 20 ± 4 → locks to 19.0
        assert bb.is_locked is True
        assert bb.baseline_value == 19.0

    def test_phase1_fail(self) -> None:
        """Test that out-of-range value in phase 1 fails and doesn't lock."""
        bb = WithinBaseline(nominal=20.0, init_delta=4.0, tight_delta=2.0)
        assert bb.check(25.0) is False
        assert bb.is_locked is False

    def test_phase2_tight_range(self) -> None:
        """Test phase 2 uses tight delta around baseline."""
        bb = WithinBaseline(nominal=20.0, init_delta=4.0, tight_delta=2.0)
        bb.check(19.0)  # Lock to 19.0

        assert bb.check(19.0) is True  # Exact baseline
        assert bb.check(17.0) is True  # 19 - 2
        assert bb.check(21.0) is True  # 19 + 2
        assert bb.check(16.9) is False  # Below tight range
        assert bb.check(21.1) is False  # Above tight range

    def test_reset(self) -> None:
        """Test reset returns to unlocked state."""
        bb = WithinBaseline(nominal=20.0, init_delta=4.0, tight_delta=2.0)
        bb.check(19.0)  # Lock
        assert bb.is_locked is True

        bb.reset()
        assert bb.is_locked is False
        assert bb.baseline_value is None

    def test_negative_init_delta_raises(self) -> None:
        """Test that negative init_delta raises ThresholdError."""
        with pytest.raises(ThresholdError, match="init_delta must be >= 0"):
            WithinBaseline(nominal=20.0, init_delta=-1.0, tight_delta=2.0)

    def test_negative_tight_delta_raises(self) -> None:
        """Test that negative tight_delta raises ThresholdError."""
        with pytest.raises(ThresholdError, match="tight_delta must be >= 0"):
            WithinBaseline(nominal=20.0, init_delta=4.0, tight_delta=-1.0)

    def test_to_dict_unlocked(self) -> None:
        """Test serialization when unlocked."""
        bb = WithinBaseline(nominal=20.0, init_delta=4.0, tight_delta=2.0)
        assert bb.to_dict() == {"within_baseline": [20.0, 4.0, 2.0]}

    def test_to_dict_locked(self) -> None:
        """Test serialization when locked includes baseline."""
        bb = WithinBaseline(nominal=20.0, init_delta=4.0, tight_delta=2.0)
        bb.check(19.8)
        assert bb.to_dict() == {"within_baseline": [20.0, 4.0, 2.0, 19.8]}

    def test_from_dict_unlocked(self) -> None:
        """Test deserialization without baseline."""
        bb = WithinBaseline.from_dict({"within_baseline": [20, 4, 2]})
        assert bb.nominal == 20.0
        assert bb.init_delta == 4.0
        assert bb.tight_delta == 2.0
        assert bb.is_locked is False

    def test_from_dict_locked(self) -> None:
        """Test deserialization with baseline."""
        bb = WithinBaseline.from_dict({"within_baseline": [20, 4, 2, 19.8]})
        assert bb.is_locked is True
        assert bb.baseline_value == 19.8

    def test_roundtrip_unlocked(self) -> None:
        """Test roundtrip for unlocked state."""
        original = WithinBaseline(nominal=20.0, init_delta=4.0, tight_delta=2.0)
        restored = WithinBaseline.from_dict(original.to_dict())
        assert restored.nominal == original.nominal
        assert restored.init_delta == original.init_delta
        assert restored.tight_delta == original.tight_delta
        assert restored.is_locked is False

    def test_roundtrip_locked(self) -> None:
        """Test roundtrip for locked state."""
        original = WithinBaseline(nominal=20.0, init_delta=4.0, tight_delta=2.0)
        original.check(19.5)
        restored = WithinBaseline.from_dict(original.to_dict())
        assert restored.is_locked is True
        assert restored.baseline_value == 19.5


class TestLessThan:
    """Tests for LessThan bound check."""

    def test_check_below(self) -> None:
        """Test value below limit passes."""
        lt = LessThan(limit=10.0)
        assert lt.check(9.9) is True
        assert lt.check(-100.0) is True

    def test_check_equal(self) -> None:
        """Test value equal to limit fails (strictly less than)."""
        lt = LessThan(limit=10.0)
        assert lt.check(10.0) is False

    def test_check_above(self) -> None:
        """Test value above limit fails."""
        lt = LessThan(limit=10.0)
        assert lt.check(10.1) is False

    def test_to_dict(self) -> None:
        """Test serialization."""
        lt = LessThan(limit=11.0)
        assert lt.to_dict() == {"less_than": 11.0}

    def test_from_dict(self) -> None:
        """Test deserialization."""
        lt = LessThan.from_dict({"less_than": 11.0})
        assert lt.limit == 11.0

    def test_roundtrip(self) -> None:
        """Test serialization roundtrip."""
        original = LessThan(limit=42.5)
        restored = LessThan.from_dict(original.to_dict())
        assert restored == original


class TestGreaterThan:
    """Tests for GreaterThan bound check."""

    def test_check_above(self) -> None:
        """Test value above limit passes."""
        gt = GreaterThan(limit=5.0)
        assert gt.check(5.1) is True
        assert gt.check(1000.0) is True

    def test_check_equal(self) -> None:
        """Test value equal to limit fails (strictly greater than)."""
        gt = GreaterThan(limit=5.0)
        assert gt.check(5.0) is False

    def test_check_below(self) -> None:
        """Test value below limit fails."""
        gt = GreaterThan(limit=5.0)
        assert gt.check(4.9) is False

    def test_to_dict(self) -> None:
        """Test serialization."""
        gt = GreaterThan(limit=3.0)
        assert gt.to_dict() == {"greater_than": 3.0}

    def test_from_dict(self) -> None:
        """Test deserialization."""
        gt = GreaterThan.from_dict({"greater_than": 3.0})
        assert gt.limit == 3.0

    def test_roundtrip(self) -> None:
        """Test serialization roundtrip."""
        original = GreaterThan(limit=-10.0)
        restored = GreaterThan.from_dict(original.to_dict())
        assert restored == original


class TestGoodInterval:
    """Tests for GoodInterval bound check."""

    def test_check_within(self) -> None:
        """Test value within interval passes."""
        gi = GoodInterval(low=1.0, high=10.0)
        assert gi.check(1.0) is True  # At low
        assert gi.check(10.0) is True  # At high
        assert gi.check(5.0) is True  # Middle

    def test_check_outside(self) -> None:
        """Test value outside interval fails."""
        gi = GoodInterval(low=1.0, high=10.0)
        assert gi.check(0.9) is False
        assert gi.check(10.1) is False

    def test_single_point(self) -> None:
        """Test interval where low == high."""
        gi = GoodInterval(low=5.0, high=5.0)
        assert gi.check(5.0) is True
        assert gi.check(5.001) is False

    def test_low_greater_than_high_raises(self) -> None:
        """Test that low > high raises ThresholdError."""
        with pytest.raises(ThresholdError, match="low <= high"):
            GoodInterval(low=10.0, high=1.0)

    def test_to_dict(self) -> None:
        """Test serialization."""
        gi = GoodInterval(low=1.0, high=10.0)
        assert gi.to_dict() == {"good_interval": [1.0, 10.0]}

    def test_from_dict(self) -> None:
        """Test deserialization."""
        gi = GoodInterval.from_dict({"good_interval": [1.0, 10.0]})
        assert gi.low == 1.0
        assert gi.high == 10.0

    def test_roundtrip(self) -> None:
        """Test serialization roundtrip."""
        original = GoodInterval(low=-5.0, high=5.0)
        restored = GoodInterval.from_dict(original.to_dict())
        assert restored == original


class TestBadInterval:
    """Tests for BadInterval bound check."""

    def test_check_outside(self) -> None:
        """Test value outside interval passes."""
        bi = BadInterval(low=3.0, high=7.0)
        assert bi.check(2.9) is True
        assert bi.check(7.1) is True

    def test_check_within(self) -> None:
        """Test value within interval fails."""
        bi = BadInterval(low=3.0, high=7.0)
        assert bi.check(3.0) is False
        assert bi.check(5.0) is False
        assert bi.check(7.0) is False

    def test_low_greater_than_high_raises(self) -> None:
        """Test that low > high raises ThresholdError."""
        with pytest.raises(ThresholdError, match="low <= high"):
            BadInterval(low=10.0, high=1.0)

    def test_to_dict(self) -> None:
        """Test serialization."""
        bi = BadInterval(low=3.0, high=7.0)
        assert bi.to_dict() == {"bad_interval": [3.0, 7.0]}

    def test_from_dict(self) -> None:
        """Test deserialization."""
        bi = BadInterval.from_dict({"bad_interval": [3.0, 7.0]})
        assert bi.low == 3.0
        assert bi.high == 7.0

    def test_roundtrip(self) -> None:
        """Test serialization roundtrip."""
        original = BadInterval(low=0.0, high=100.0)
        restored = BadInterval.from_dict(original.to_dict())
        assert restored == original


class TestGoodValues:
    """Tests for GoodValues bound check."""

    def test_check_in_set(self) -> None:
        """Test value in allowed set passes."""
        gv = GoodValues(values=frozenset({1, 2, 3}))
        assert gv.check(1.0) is True
        assert gv.check(2.0) is True
        assert gv.check(3.0) is True

    def test_check_rounds(self) -> None:
        """Test that values are rounded before checking."""
        gv = GoodValues(values=frozenset({5}))
        assert gv.check(4.6) is True  # rounds to 5
        assert gv.check(5.4) is True  # rounds to 5
        assert gv.check(5.5) is False  # banker's rounding: round(5.5) == 6
        assert gv.check(4.4) is False  # rounds to 4

    def test_check_not_in_set(self) -> None:
        """Test value not in allowed set fails."""
        gv = GoodValues(values=frozenset({1, 2, 3}))
        assert gv.check(4.0) is False
        assert gv.check(0.0) is False

    def test_empty_set(self) -> None:
        """Test empty set rejects all values."""
        gv = GoodValues(values=frozenset())
        assert gv.check(0.0) is False
        assert gv.check(1.0) is False

    def test_to_dict(self) -> None:
        """Test serialization (sorted output)."""
        gv = GoodValues(values=frozenset({3, 1, 2}))
        assert gv.to_dict() == {"good_values": [1, 2, 3]}

    def test_from_dict(self) -> None:
        """Test deserialization."""
        gv = GoodValues.from_dict({"good_values": [1, 2, 3]})
        assert gv.values == frozenset({1, 2, 3})

    def test_roundtrip(self) -> None:
        """Test serialization roundtrip."""
        original = GoodValues(values=frozenset({10, 20, 30}))
        restored = GoodValues.from_dict(original.to_dict())
        assert restored == original


class TestBadValues:
    """Tests for BadValues bound check."""

    def test_check_not_in_set(self) -> None:
        """Test value not in forbidden set passes."""
        bv = BadValues(values=frozenset({0, 255}))
        assert bv.check(1.0) is True
        assert bv.check(128.0) is True

    def test_check_in_set(self) -> None:
        """Test value in forbidden set fails."""
        bv = BadValues(values=frozenset({0, 255}))
        assert bv.check(0.0) is False
        assert bv.check(255.0) is False

    def test_check_rounds(self) -> None:
        """Test that values are rounded before checking."""
        bv = BadValues(values=frozenset({5}))
        assert bv.check(4.6) is False  # rounds to 5
        assert bv.check(4.4) is True  # rounds to 4

    def test_empty_set(self) -> None:
        """Test empty set accepts all values."""
        bv = BadValues(values=frozenset())
        assert bv.check(0.0) is True
        assert bv.check(999.0) is True

    def test_to_dict(self) -> None:
        """Test serialization (sorted output)."""
        bv = BadValues(values=frozenset({255, 0}))
        assert bv.to_dict() == {"bad_values": [0, 255]}

    def test_from_dict(self) -> None:
        """Test deserialization."""
        bv = BadValues.from_dict({"bad_values": [0, 255]})
        assert bv.values == frozenset({0, 255})

    def test_roundtrip(self) -> None:
        """Test serialization roundtrip."""
        original = BadValues(values=frozenset({42, 99}))
        restored = BadValues.from_dict(original.to_dict())
        assert restored == original


class TestSpecial:
    """Tests for Special bound check."""

    def test_check_any_always_true(self) -> None:
        """Test that kind='any' always passes."""
        sp = Special(kind="any")
        assert sp.check(0.0) is True
        assert sp.check(-999.0) is True
        assert sp.check(float("inf")) is True
        assert sp.check(float("nan")) is True

    def test_to_dict(self) -> None:
        """Test serialization."""
        sp = Special(kind="any")
        assert sp.to_dict() == {"special": "any"}

    def test_from_dict(self) -> None:
        """Test deserialization."""
        sp = Special.from_dict({"special": "any"})
        assert sp.kind == "any"

    def test_roundtrip(self) -> None:
        """Test serialization roundtrip."""
        original = Special(kind="any")
        restored = Special.from_dict(original.to_dict())
        assert restored == original


class TestBoundCheckProtocol:
    """Tests for BoundCheck protocol compliance."""

    def test_all_types_satisfy_protocol(self) -> None:
        """Test that all concrete types satisfy the BoundCheck protocol."""
        instances: list[BoundCheck] = [
            WithinTolerance(center=10.0, fraction=0.1),
            WithinRange(center=10.0, delta=1.0),
            WithinBaseline(nominal=20.0, init_delta=4.0, tight_delta=2.0),
            LessThan(limit=10.0),
            GreaterThan(limit=5.0),
            GoodInterval(low=1.0, high=10.0),
            BadInterval(low=3.0, high=7.0),
            GoodValues(values=frozenset({1, 2, 3})),
            BadValues(values=frozenset({0})),
            Special(kind="any"),
        ]
        for instance in instances:
            assert isinstance(instance, BoundCheck)
            # All must have check, to_dict
            assert hasattr(instance, "check")
            assert hasattr(instance, "to_dict")
            # check must return bool
            result = instance.check(5.0)
            assert isinstance(result, bool)
            # to_dict must return dict
            d = instance.to_dict()
            assert isinstance(d, dict)


class TestBoundCheckFromDict:
    """Tests for the bound_check_from_dict factory function."""

    def test_all_types(self) -> None:
        """Test factory dispatches to all known types."""
        cases: list[tuple[dict[str, object], type[object]]] = [
            ({"within_tolerance": [10.0, 0.1]}, WithinTolerance),
            ({"within_range": [100.0, 5.0]}, WithinRange),
            ({"within_baseline": [20, 4, 2]}, WithinBaseline),
            ({"less_than": 11.0}, LessThan),
            ({"greater_than": 3.0}, GreaterThan),
            ({"good_interval": [1.0, 10.0]}, GoodInterval),
            ({"bad_interval": [3.0, 7.0]}, BadInterval),
            ({"good_values": [1, 2, 3]}, GoodValues),
            ({"bad_values": [0, 255]}, BadValues),
            ({"special": "any"}, Special),
        ]
        for data, expected_type in cases:
            result = bound_check_from_dict(data)  # type: ignore[arg-type]
            assert isinstance(
                result, expected_type
            ), f"Expected {expected_type.__name__}, got {type(result).__name__}"

    def test_unknown_key_raises(self) -> None:
        """Test that unknown key raises ThresholdError."""
        with pytest.raises(ThresholdError, match="Unknown bound check type"):
            bound_check_from_dict({"unknown_type": 42})

    def test_multiple_keys_raises(self) -> None:
        """Test that multiple keys raises ThresholdError."""
        with pytest.raises(ThresholdError, match="exactly one key"):
            bound_check_from_dict({"less_than": 10.0, "greater_than": 5.0})

    def test_empty_dict_raises(self) -> None:
        """Test that empty dict raises ThresholdError."""
        with pytest.raises(ThresholdError, match="exactly one key"):
            bound_check_from_dict({})

    def test_factory_roundtrip(self) -> None:
        """Test that factory can reconstruct from any type's to_dict output."""
        originals: list[BoundCheck] = [
            WithinTolerance(center=10.0, fraction=0.1),
            WithinRange(center=100.0, delta=5.0),
            LessThan(limit=11.0),
            GreaterThan(limit=3.0),
            GoodInterval(low=1.0, high=10.0),
            BadInterval(low=3.0, high=7.0),
            GoodValues(values=frozenset({1, 2, 3})),
            BadValues(values=frozenset({0, 255})),
            Special(kind="any"),
        ]
        for original in originals:
            d = original.to_dict()
            restored = bound_check_from_dict(d)
            assert type(restored) is type(original)
            # Check that behavior is preserved
            assert restored.check(5.0) == original.check(5.0)

    def test_factory_roundtrip_within_baseline(self) -> None:
        """Test factory roundtrip for WithinBaseline (stateful, separate test)."""
        original = WithinBaseline(nominal=20.0, init_delta=4.0, tight_delta=2.0)
        original.check(19.5)  # Lock baseline
        d = original.to_dict()
        restored = bound_check_from_dict(d)
        assert isinstance(restored, WithinBaseline)
        assert restored.is_locked is True
        assert restored.baseline_value == 19.5
