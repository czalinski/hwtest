"""Tests for VisaResource with mocked pyvisa module."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from hwtest_core.errors import HwtestError
from hwtest_scpi.visa import VisaResource


def _make_mock_pyvisa() -> MagicMock:
    """Create a mock pyvisa module with ResourceManager."""
    mock_pyvisa = MagicMock()
    mock_rm = MagicMock()
    mock_resource = MagicMock()
    mock_rm.open_resource.return_value = mock_resource
    mock_pyvisa.ResourceManager.return_value = mock_rm
    return mock_pyvisa


# ---------------------------------------------------------------------------
# open / close lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Tests for open/close lifecycle."""

    def test_open_imports_pyvisa_and_opens_resource(self) -> None:
        mock_pyvisa = _make_mock_pyvisa()
        visa = VisaResource("TCPIP::192.168.1.1::INSTR")
        with patch.dict(sys.modules, {"pyvisa": mock_pyvisa}):
            visa.open()
        assert visa.is_open
        mock_pyvisa.ResourceManager.assert_called_once()
        mock_pyvisa.ResourceManager().open_resource.assert_called_once_with(
            "TCPIP::192.168.1.1::INSTR",
            read_termination="\n",
            write_termination="\n",
        )

    def test_open_sets_timeout(self) -> None:
        mock_pyvisa = _make_mock_pyvisa()
        visa = VisaResource("TCPIP::192.168.1.1::INSTR", timeout_ms=10000)
        with patch.dict(sys.modules, {"pyvisa": mock_pyvisa}):
            visa.open()
        resource = mock_pyvisa.ResourceManager().open_resource()
        assert resource.timeout == 10000

    def test_open_idempotent(self) -> None:
        mock_pyvisa = _make_mock_pyvisa()
        visa = VisaResource("TCPIP::192.168.1.1::INSTR")
        with patch.dict(sys.modules, {"pyvisa": mock_pyvisa}):
            visa.open()
            visa.open()  # Second call should be a no-op
        mock_pyvisa.ResourceManager.assert_called_once()

    def test_close_clears_state(self) -> None:
        mock_pyvisa = _make_mock_pyvisa()
        visa = VisaResource("TCPIP::192.168.1.1::INSTR")
        with patch.dict(sys.modules, {"pyvisa": mock_pyvisa}):
            visa.open()
        assert visa.is_open
        visa.close()
        assert not visa.is_open

    def test_close_idempotent(self) -> None:
        mock_pyvisa = _make_mock_pyvisa()
        visa = VisaResource("TCPIP::192.168.1.1::INSTR")
        with patch.dict(sys.modules, {"pyvisa": mock_pyvisa}):
            visa.open()
        visa.close()
        visa.close()  # Should not raise
        assert not visa.is_open

    def test_close_without_open(self) -> None:
        visa = VisaResource("TCPIP::192.168.1.1::INSTR")
        visa.close()  # Should not raise

    def test_is_open_initially_false(self) -> None:
        visa = VisaResource("TCPIP::192.168.1.1::INSTR")
        assert not visa.is_open


# ---------------------------------------------------------------------------
# pyvisa not installed
# ---------------------------------------------------------------------------


class TestPyvisaNotInstalled:
    """Tests for behavior when pyvisa is not installed."""

    def test_open_raises_hwtest_error(self) -> None:
        visa = VisaResource("TCPIP::192.168.1.1::INSTR")
        mock_pyvisa = MagicMock()
        mock_pyvisa.side_effect = ImportError("No module named 'pyvisa'")
        with patch.dict(sys.modules, {"pyvisa": None}):
            with pytest.raises(HwtestError, match="pyvisa library is not installed"):
                visa.open()


# ---------------------------------------------------------------------------
# open failure
# ---------------------------------------------------------------------------


class TestOpenFailure:
    """Tests for failure during resource opening."""

    def test_open_failure_raises_hwtest_error(self) -> None:
        mock_pyvisa = _make_mock_pyvisa()
        mock_pyvisa.ResourceManager().open_resource.side_effect = RuntimeError("No device")
        visa = VisaResource("TCPIP::192.168.1.1::INSTR")
        with patch.dict(sys.modules, {"pyvisa": mock_pyvisa}):
            with pytest.raises(HwtestError, match="Failed to open VISA resource"):
                visa.open()
        assert not visa.is_open


# ---------------------------------------------------------------------------
# write / read
# ---------------------------------------------------------------------------


class TestWriteRead:
    """Tests for write and read operations."""

    def test_write_delegates_to_resource(self) -> None:
        mock_pyvisa = _make_mock_pyvisa()
        visa = VisaResource("TCPIP::192.168.1.1::INSTR")
        with patch.dict(sys.modules, {"pyvisa": mock_pyvisa}):
            visa.open()
        resource = mock_pyvisa.ResourceManager().open_resource()
        visa.write("*IDN?")
        resource.write.assert_called_once_with("*IDN?")

    def test_read_delegates_to_resource(self) -> None:
        mock_pyvisa = _make_mock_pyvisa()
        visa = VisaResource("TCPIP::192.168.1.1::INSTR")
        with patch.dict(sys.modules, {"pyvisa": mock_pyvisa}):
            visa.open()
        resource = mock_pyvisa.ResourceManager().open_resource()
        resource.read.return_value = "KEYSIGHT,34465A"
        result = visa.read()
        assert result == "KEYSIGHT,34465A"

    def test_write_when_closed_raises(self) -> None:
        visa = VisaResource("TCPIP::192.168.1.1::INSTR")
        with pytest.raises(HwtestError, match="not open"):
            visa.write("*IDN?")

    def test_read_when_closed_raises(self) -> None:
        visa = VisaResource("TCPIP::192.168.1.1::INSTR")
        with pytest.raises(HwtestError, match="not open"):
            visa.read()


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:
    """Tests for VisaResource properties."""

    def test_resource_string(self) -> None:
        visa = VisaResource("GPIB0::22::INSTR")
        assert visa.resource_string == "GPIB0::22::INSTR"


# ---------------------------------------------------------------------------
# Termination configuration
# ---------------------------------------------------------------------------


class TestTermination:
    """Tests for custom termination characters."""

    def test_custom_termination(self) -> None:
        mock_pyvisa = _make_mock_pyvisa()
        visa = VisaResource(
            "TCPIP::192.168.1.1::INSTR",
            read_termination="\r\n",
            write_termination="\r\n",
        )
        with patch.dict(sys.modules, {"pyvisa": mock_pyvisa}):
            visa.open()
        mock_pyvisa.ResourceManager().open_resource.assert_called_once_with(
            "TCPIP::192.168.1.1::INSTR",
            read_termination="\r\n",
            write_termination="\r\n",
        )
