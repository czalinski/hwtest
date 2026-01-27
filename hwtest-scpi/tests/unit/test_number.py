"""Tests for SCPI number parsing and formatting."""

from __future__ import annotations

import math

import pytest

from hwtest_scpi.number import (
    ScpiSpecial,
    format_bool,
    format_number,
    parse_bool,
    parse_int,
    parse_number,
    parse_numbers,
    parse_special,
)

# ---------------------------------------------------------------------------
# parse_number
# ---------------------------------------------------------------------------


class TestParseNumber:
    """Tests for parse_number."""

    def test_nr1_integer(self) -> None:
        assert parse_number("42") == 42.0

    def test_nr1_negative(self) -> None:
        assert parse_number("-7") == -7.0

    def test_nr2_fixed_point(self) -> None:
        assert parse_number("1.23") == 1.23

    def test_nr2_negative(self) -> None:
        assert parse_number("-0.5") == -0.5

    def test_nr3_scientific(self) -> None:
        assert parse_number("1.23E+4") == 12300.0

    def test_nr3_negative_exponent(self) -> None:
        assert parse_number("5E-3") == 0.005

    def test_nan(self) -> None:
        assert math.isnan(parse_number("NAN"))

    def test_nan_lowercase(self) -> None:
        assert math.isnan(parse_number("nan"))

    def test_inf(self) -> None:
        assert parse_number("INF") == float("inf")

    def test_ninf(self) -> None:
        assert parse_number("NINF") == float("-inf")

    def test_negative_inf(self) -> None:
        assert parse_number("-INF") == float("-inf")

    def test_whitespace_stripped(self) -> None:
        assert parse_number("  3.14  ") == 3.14

    def test_zero(self) -> None:
        assert parse_number("0") == 0.0

    def test_positive_sign(self) -> None:
        assert parse_number("+1.5") == 1.5

    def test_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid SCPI number"):
            parse_number("abc")

    def test_empty_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid SCPI number"):
            parse_number("")


# ---------------------------------------------------------------------------
# parse_numbers
# ---------------------------------------------------------------------------


class TestParseNumbers:
    """Tests for parse_numbers."""

    def test_single_value(self) -> None:
        assert parse_numbers("1.0") == (1.0,)

    def test_multiple_values(self) -> None:
        assert parse_numbers("1.0,2.0,3.0") == (1.0, 2.0, 3.0)

    def test_with_spaces(self) -> None:
        assert parse_numbers(" 1.0 , 2.0 , 3.0 ") == (1.0, 2.0, 3.0)

    def test_mixed_formats(self) -> None:
        result = parse_numbers("42,1.5,1E3")
        assert result == (42.0, 1.5, 1000.0)

    def test_with_special_values(self) -> None:
        result = parse_numbers("1.0,NAN,INF")
        assert result[0] == 1.0
        assert math.isnan(result[1])
        assert result[2] == float("inf")

    def test_invalid_element_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_numbers("1.0,abc,3.0")


# ---------------------------------------------------------------------------
# parse_int
# ---------------------------------------------------------------------------


class TestParseInt:
    """Tests for parse_int."""

    def test_positive(self) -> None:
        assert parse_int("42") == 42

    def test_negative(self) -> None:
        assert parse_int("-7") == -7

    def test_zero(self) -> None:
        assert parse_int("0") == 0

    def test_whitespace_stripped(self) -> None:
        assert parse_int("  100  ") == 100

    def test_float_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid SCPI integer"):
            parse_int("1.5")

    def test_text_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid SCPI integer"):
            parse_int("abc")


# ---------------------------------------------------------------------------
# parse_bool
# ---------------------------------------------------------------------------


class TestParseBool:
    """Tests for parse_bool."""

    def test_one_is_true(self) -> None:
        assert parse_bool("1") is True

    def test_zero_is_false(self) -> None:
        assert parse_bool("0") is False

    def test_on_is_true(self) -> None:
        assert parse_bool("ON") is True

    def test_off_is_false(self) -> None:
        assert parse_bool("OFF") is False

    def test_case_insensitive(self) -> None:
        assert parse_bool("on") is True
        assert parse_bool("Off") is False

    def test_whitespace_stripped(self) -> None:
        assert parse_bool("  1  ") is True

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid SCPI boolean"):
            parse_bool("yes")

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid SCPI boolean"):
            parse_bool("")


# ---------------------------------------------------------------------------
# parse_special
# ---------------------------------------------------------------------------


class TestParseSpecial:
    """Tests for parse_special."""

    def test_min_returns_enum(self) -> None:
        assert parse_special("MIN") is ScpiSpecial.MIN

    def test_max_returns_enum(self) -> None:
        assert parse_special("MAX") is ScpiSpecial.MAX

    def test_def_returns_enum(self) -> None:
        assert parse_special("DEF") is ScpiSpecial.DEF

    def test_case_insensitive_keywords(self) -> None:
        assert parse_special("min") is ScpiSpecial.MIN

    def test_numeric_returns_float(self) -> None:
        result = parse_special("3.14")
        assert isinstance(result, float)
        assert result == 3.14

    def test_nan_returns_float(self) -> None:
        result = parse_special("NAN")
        assert isinstance(result, float)
        assert math.isnan(result)

    def test_inf_returns_float(self) -> None:
        result = parse_special("INF")
        assert isinstance(result, float)
        assert result == float("inf")

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_special("BOGUS")


# ---------------------------------------------------------------------------
# format_number
# ---------------------------------------------------------------------------


class TestFormatNumber:
    """Tests for format_number."""

    def test_finite_value(self) -> None:
        assert format_number(3.14) == "3.14"

    def test_integer_value(self) -> None:
        assert format_number(42.0) == "42.0"

    def test_nan(self) -> None:
        assert format_number(float("nan")) == "NAN"

    def test_inf(self) -> None:
        assert format_number(float("inf")) == "INF"

    def test_negative_inf(self) -> None:
        assert format_number(float("-inf")) == "NINF"

    def test_zero(self) -> None:
        assert format_number(0.0) == "0.0"

    def test_negative(self) -> None:
        assert format_number(-1.5) == "-1.5"


# ---------------------------------------------------------------------------
# format_bool
# ---------------------------------------------------------------------------


class TestFormatBool:
    """Tests for format_bool."""

    def test_true(self) -> None:
        assert format_bool(True) == "1"

    def test_false(self) -> None:
        assert format_bool(False) == "0"
