"""Tests for behavior specific to the Rust-backed implementation.

These cover places where the Rust core intentionally differs from (or is
stricter than) the original pure-Python implementation.
"""

import math
import struct
from datetime import datetime, timedelta, timezone

import pytest

from hwtest_core import _native
from hwtest_core.errors import HwtestError, SerializationError, ThresholdError
from hwtest_core.types.bounds import (
    BadInterval,
    BadValues,
    GoodValues,
    GreaterThan,
    LessThan,
    Special,
    WithinRange,
    bound_check_from_dict,
)
from hwtest_core.types.common import DataType, Timestamp
from hwtest_core.types.state import EnvironmentalState
from hwtest_core.types.streaming import StreamData, StreamField, StreamSchema
from hwtest_core.types.telemetry import TelemetryMessage, TelemetryValue, ValueQuality


class TestNativeModule:
    def test_types_come_from_extension(self) -> None:
        assert StreamSchema is _native.StreamSchema
        assert StreamSchema.__module__ == "hwtest_core.types.streaming"
        assert Timestamp.__module__ == "hwtest_core.types.common"


class TestNonFiniteMeasurements:
    @pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
    @pytest.mark.parametrize(
        "check",
        [
            WithinRange(0.0, 1e308),
            LessThan(0.0),
            GreaterThan(0.0),
            BadInterval(0.0, 1.0),
            GoodValues({0}),
            BadValues(set()),
        ],
    )
    def test_fail_closed(self, check: object, value: float) -> None:
        assert check.check(value) is False  # type: ignore[attr-defined]

    def test_special_any_still_passes(self) -> None:
        assert Special("any").check(math.nan) is True

    def test_nan_telemetry_round_trips_as_null(self) -> None:
        value = TelemetryValue("tc0", math.nan, "degC", Timestamp(1), quality=ValueQuality.BAD)
        msg = TelemetryMessage("mcc134", (value,), 1)
        data = msg.to_bytes()
        assert b'"value":null' in data  # valid JSON, unlike Python's bare NaN token
        restored = TelemetryMessage.from_bytes(data)
        assert math.isnan(restored.values[0].value)
        assert value.to_dict()["value"] is None


class TestBoundValidation:
    def test_unknown_special_kind_rejected(self) -> None:
        with pytest.raises(ThresholdError, match="Unknown special bound kind"):
            Special("anyy")
        with pytest.raises(ThresholdError, match="Unknown special bound kind"):
            bound_check_from_dict({"special": "nay"})

    def test_nan_tolerance_rejected(self) -> None:
        with pytest.raises(ThresholdError, match="delta must be >= 0"):
            WithinRange(1.0, math.nan)

    def test_malformed_args(self) -> None:
        with pytest.raises(ThresholdError, match=r"\[center, delta\]"):
            bound_check_from_dict({"within_range": [1.0]})
        with pytest.raises(ThresholdError, match="expected an integer"):
            bound_check_from_dict({"good_values": [1.5]})

    def test_from_dict_rejects_other_bound_type(self) -> None:
        with pytest.raises(ThresholdError, match='Expected a "less_than" bound'):
            LessThan.from_dict({"greater_than": 1.0})

    def test_threshold_error_is_hwtest_error(self) -> None:
        with pytest.raises(HwtestError):
            WithinRange(0.0, -1.0)


class TestTimestampPrecision:
    def test_from_datetime_is_exact_to_the_microsecond(self) -> None:
        dt = datetime(2024, 1, 15, 12, 0, 0, 123457, tzinfo=timezone.utc)
        assert Timestamp.from_datetime(dt).unix_ns == 1_705_320_000_123_457_000

    def test_from_datetime_honours_timezone(self) -> None:
        tz = timezone(timedelta(hours=-8))
        dt = datetime(2024, 1, 15, 4, 0, 0, tzinfo=tz)
        assert Timestamp.from_datetime(dt).unix_ns == 1_705_320_000_000_000_000

    def test_to_datetime_round_trip(self) -> None:
        ts = Timestamp(1_705_320_000_123_457_000, "ptp")
        assert Timestamp.from_datetime(ts.to_datetime(), "ptp") == ts

    def test_ordering_and_hash(self) -> None:
        a, b = Timestamp(1), Timestamp(2)
        assert a < b and b > a
        assert len({Timestamp(1), Timestamp(1)}) == 1


class TestStreamingErrors:
    @pytest.fixture
    def schema(self) -> StreamSchema:
        return StreamSchema(
            "s", (StreamField("count", DataType.U8), StreamField("v", DataType.F32))
        )

    def test_float_into_integer_field(self, schema: StreamSchema) -> None:
        data = StreamData(schema.schema_id, 0, 0, ((1.5, 1.0),))
        with pytest.raises(ValueError, match="requires an integer"):
            data.to_bytes(schema)

    def test_integer_out_of_range(self, schema: StreamSchema) -> None:
        data = StreamData(schema.schema_id, 0, 0, ((256, 1.0),))
        with pytest.raises(ValueError, match="out of range"):
            data.to_bytes(schema)

    def test_truncated_frame(self, schema: StreamSchema) -> None:
        good = StreamData(schema.schema_id, 0, 0, ((1, 1.0),)).to_bytes(schema)
        with pytest.raises(ValueError, match="Truncated"):
            StreamData.from_bytes(good[:-1], schema)
        with pytest.raises(ValueError, match="Truncated"):
            StreamSchema.from_bytes(schema.to_bytes()[:-1])

    def test_unknown_dtype_code(self) -> None:
        bad_field = b"\x01x" + bytes([0x7F]) + b"\x00"
        with pytest.raises(ValueError, match="Unknown data type code"):
            StreamField.from_bytes(bad_field, 0)

    def test_accepts_bytearray(self, schema: StreamSchema) -> None:
        raw = schema.to_bytes()
        assert StreamSchema.from_bytes(bytearray(raw)) == schema

    def test_bool_and_int_samples_widen_into_float_fields(self, schema: StreamSchema) -> None:
        data = StreamData(schema.schema_id, 0, 0, ((True, 2),))
        decoded = StreamData.from_bytes(data.to_bytes(schema), schema)
        assert decoded.samples == ((1, 2.0),)

    def test_samples_rejects_non_numbers(self) -> None:
        with pytest.raises(TypeError, match="int or float"):
            StreamData(0, 0, 0, (("x",),))

    def test_dtype_accepts_raw_code(self) -> None:
        assert StreamField("v", 0x0A).dtype is DataType.F64

    def test_wire_layout_is_big_endian(self, schema: StreamSchema) -> None:
        frame = StreamData(schema.schema_id, 5, 7, ((3, 0.5),)).to_bytes(schema)
        assert struct.unpack("!BIQQHBf", frame) == (2, schema.schema_id, 5, 7, 1, 3, 0.5)


class TestSerializationErrors:
    def test_missing_field(self) -> None:
        with pytest.raises(SerializationError, match="missing field"):
            TelemetryValue.from_dict({"channel": "x"})

    def test_is_value_error(self) -> None:
        with pytest.raises(ValueError):
            TelemetryMessage.from_bytes(b"not json")

    def test_metadata_must_be_json_compatible(self) -> None:
        with pytest.raises(SerializationError, match="not JSON serializable"):
            EnvironmentalState("s", "S", "", metadata={"obj": object()})

    def test_metadata_accepts_enums_and_tuples(self) -> None:
        state = EnvironmentalState("s", "S", "", metadata={"q": ValueQuality.GOOD, "t": (1, 2)})
        assert state.metadata == {"q": "good", "t": [1, 2]}
