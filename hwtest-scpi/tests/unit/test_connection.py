"""Tests for ScpiConnection using a mock transport."""

from __future__ import annotations

from collections import deque

import pytest

from hwtest_core.types.common import InstrumentIdentity

from hwtest_scpi.connection import ScpiConnection, parse_idn_response
from hwtest_scpi.errors import ScpiCommandError, ScpiInstrumentError

# ---------------------------------------------------------------------------
# Mock transport
# ---------------------------------------------------------------------------


class MockTransport:
    """In-memory transport that replays pre-loaded responses."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses: deque[str] = deque(responses or [])
        self.written: list[str] = []
        self.closed: bool = False

    def write(self, message: str) -> None:
        self.written.append(message)

    def read(self) -> str:
        return self.responses.popleft()

    def close(self) -> None:
        self.closed = True


def _no_error() -> str:
    """Standard 'no error' response."""
    return '0,"No error"'


# ---------------------------------------------------------------------------
# command
# ---------------------------------------------------------------------------


class TestCommand:
    """Tests for ScpiConnection.command."""

    def test_sends_command_and_checks_errors(self) -> None:
        transport = MockTransport([_no_error()])
        conn = ScpiConnection(transport)
        conn.command("*RST")
        assert transport.written == ["*RST", "SYST:ERR?"]

    def test_error_raises_scpi_command_error(self) -> None:
        transport = MockTransport(['-100,"Command error"', _no_error()])
        conn = ScpiConnection(transport)
        with pytest.raises(ScpiCommandError) as exc_info:
            conn.command("BAD:CMD")
        assert len(exc_info.value.errors) == 1
        assert exc_info.value.errors[0].code == -100
        assert exc_info.value.errors[0].message == "Command error"

    def test_multiple_errors_collected(self) -> None:
        transport = MockTransport(['-100,"Command error"', '-200,"Execution error"', _no_error()])
        conn = ScpiConnection(transport)
        with pytest.raises(ScpiCommandError) as exc_info:
            conn.command("BAD:CMD")
        assert len(exc_info.value.errors) == 2

    def test_check_false_skips_error_check(self) -> None:
        transport = MockTransport([])
        conn = ScpiConnection(transport)
        conn.command("*RST", check=False)
        assert transport.written == ["*RST"]

    def test_instance_check_errors_false(self) -> None:
        transport = MockTransport([])
        conn = ScpiConnection(transport, check_errors=False)
        conn.command("*RST")
        assert transport.written == ["*RST"]

    def test_per_call_check_overrides_instance(self) -> None:
        transport = MockTransport([_no_error()])
        conn = ScpiConnection(transport, check_errors=False)
        conn.command("*RST", check=True)
        assert "SYST:ERR?" in transport.written


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


class TestQuery:
    """Tests for ScpiConnection.query."""

    def test_sends_query_and_returns_response(self) -> None:
        transport = MockTransport(["KEYSIGHT,34465A,SN123,1.0", _no_error()])
        conn = ScpiConnection(transport)
        result = conn.query("*IDN?")
        assert result == "KEYSIGHT,34465A,SN123,1.0"

    def test_strips_whitespace_from_response(self) -> None:
        transport = MockTransport(["  hello  \n", _no_error()])
        conn = ScpiConnection(transport)
        assert conn.query("TEST?") == "hello"

    def test_error_after_query_raises(self) -> None:
        transport = MockTransport(["0.0", '-100,"Command error"', _no_error()])
        conn = ScpiConnection(transport)
        with pytest.raises(ScpiCommandError):
            conn.query("MEAS?")


# ---------------------------------------------------------------------------
# Typed query variants
# ---------------------------------------------------------------------------


class TestTypedQueries:
    """Tests for query_number, query_numbers, query_int, query_bool."""

    def test_query_number(self) -> None:
        transport = MockTransport(["1.23E+4", _no_error()])
        conn = ScpiConnection(transport)
        assert conn.query_number("MEAS?") == 12300.0

    def test_query_numbers(self) -> None:
        transport = MockTransport(["1.0,2.0,3.0", _no_error()])
        conn = ScpiConnection(transport)
        assert conn.query_numbers("FETC?") == (1.0, 2.0, 3.0)

    def test_query_int(self) -> None:
        transport = MockTransport(["42", _no_error()])
        conn = ScpiConnection(transport)
        assert conn.query_int("COUN?") == 42

    def test_query_bool(self) -> None:
        transport = MockTransport(["1", _no_error()])
        conn = ScpiConnection(transport)
        assert conn.query_bool("OUTP?") is True


# ---------------------------------------------------------------------------
# Convenience methods
# ---------------------------------------------------------------------------


class TestConvenience:
    """Tests for identify, reset, clear_status, wait_complete."""

    def test_identify(self) -> None:
        transport = MockTransport(["ACME,Model1,SN001,1.0", _no_error()])
        conn = ScpiConnection(transport)
        result = conn.identify()
        assert result == "ACME,Model1,SN001,1.0"
        assert transport.written[0] == "*IDN?"

    def test_reset(self) -> None:
        transport = MockTransport([_no_error()])
        conn = ScpiConnection(transport)
        conn.reset()
        assert transport.written[0] == "*RST"

    def test_clear_status(self) -> None:
        transport = MockTransport([_no_error()])
        conn = ScpiConnection(transport)
        conn.clear_status()
        assert transport.written[0] == "*CLS"

    def test_wait_complete(self) -> None:
        transport = MockTransport(["1"])
        conn = ScpiConnection(transport)
        conn.wait_complete()
        assert transport.written[0] == "*OPC?"
        # Should NOT have queried SYST:ERR? (check=False)
        assert "SYST:ERR?" not in transport.written


# ---------------------------------------------------------------------------
# get_errors
# ---------------------------------------------------------------------------


class TestGetErrors:
    """Tests for ScpiConnection.get_errors."""

    def test_no_errors(self) -> None:
        transport = MockTransport([_no_error()])
        conn = ScpiConnection(transport, check_errors=False)
        errors = conn.get_errors()
        assert errors == ()

    def test_single_error(self) -> None:
        transport = MockTransport(['-100,"Command error"', _no_error()])
        conn = ScpiConnection(transport, check_errors=False)
        errors = conn.get_errors()
        assert len(errors) == 1
        assert errors[0] == ScpiInstrumentError(code=-100, message="Command error")

    def test_multiple_errors_drained(self) -> None:
        transport = MockTransport(['-100,"Command error"', '-200,"Execution error"', _no_error()])
        conn = ScpiConnection(transport, check_errors=False)
        errors = conn.get_errors()
        assert len(errors) == 2

    def test_positive_error_code(self) -> None:
        transport = MockTransport(['100,"Device-specific error"', _no_error()])
        conn = ScpiConnection(transport, check_errors=False)
        errors = conn.get_errors()
        assert errors[0].code == 100

    def test_unquoted_message(self) -> None:
        transport = MockTransport(["-100,Command error", _no_error()])
        conn = ScpiConnection(transport, check_errors=False)
        errors = conn.get_errors()
        assert errors[0].message == "Command error"

    def test_plus_sign_prefix(self) -> None:
        transport = MockTransport(['+0,"No error"'])
        conn = ScpiConnection(transport, check_errors=False)
        errors = conn.get_errors()
        assert errors == ()


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestClose:
    """Tests for ScpiConnection.close."""

    def test_close_delegates_to_transport(self) -> None:
        transport = MockTransport([])
        conn = ScpiConnection(transport, check_errors=False)
        conn.close()
        assert transport.closed is True


# ---------------------------------------------------------------------------
# ScpiCommandError formatting
# ---------------------------------------------------------------------------


class TestScpiCommandErrorFormatting:
    """Tests for ScpiCommandError string representation."""

    def test_single_error_message(self) -> None:
        err = ScpiCommandError((ScpiInstrumentError(code=-100, message="Command error"),))
        assert "-100" in str(err)
        assert "Command error" in str(err)

    def test_multiple_errors_message(self) -> None:
        err = ScpiCommandError(
            (
                ScpiInstrumentError(code=-100, message="Command error"),
                ScpiInstrumentError(code=-200, message="Execution error"),
            )
        )
        msg = str(err)
        assert "-100" in msg
        assert "-200" in msg


# ---------------------------------------------------------------------------
# parse_idn_response
# ---------------------------------------------------------------------------


class TestParseIdnResponse:
    """Tests for parse_idn_response."""

    def test_standard_four_fields(self) -> None:
        result = parse_idn_response("B&K Precision,9115,SN000001,V1.00-V1.00")
        assert result == InstrumentIdentity(
            manufacturer="B&K Precision",
            model="9115",
            serial="SN000001",
            firmware="V1.00-V1.00",
        )

    def test_strips_whitespace(self) -> None:
        result = parse_idn_response(" Keysight , 34465A , SN123 , 1.0.0 ")
        assert result.manufacturer == "Keysight"
        assert result.model == "34465A"
        assert result.serial == "SN123"
        assert result.firmware == "1.0.0"

    def test_extra_fields_joined_as_firmware(self) -> None:
        result = parse_idn_response("Mfr,Model,SN1,FW1,FW2,FW3")
        assert result.firmware == "FW1,FW2,FW3"

    def test_too_few_fields_raises(self) -> None:
        with pytest.raises(ValueError, match="at least 4"):
            parse_idn_response("Only,Two,Fields")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="at least 4"):
            parse_idn_response("")


# ---------------------------------------------------------------------------
# get_identity
# ---------------------------------------------------------------------------


class TestGetIdentity:
    """Tests for ScpiConnection.get_identity."""

    def test_returns_parsed_identity(self) -> None:
        transport = MockTransport(["ACME,Widget,SN42,2.1.0", _no_error()])
        conn = ScpiConnection(transport)
        identity = conn.get_identity()
        assert identity.manufacturer == "ACME"
        assert identity.model == "Widget"
        assert identity.serial == "SN42"
        assert identity.firmware == "2.1.0"

    def test_sends_idn_query(self) -> None:
        transport = MockTransport(["Mfr,Model,SN,FW", _no_error()])
        conn = ScpiConnection(transport)
        conn.get_identity()
        assert transport.written[0] == "*IDN?"
