"""Unit tests for the MCC HAT scanner module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hwtest_mcc.scanner import HatInfo, main, scan_hats


class TestHatInfo:
    """Tests for the HatInfo dataclass."""

    def test_create_hat_info(self) -> None:
        """HatInfo stores address, model, and serial."""
        info = HatInfo(address=0, model="MCC 118", serial="12345678")
        assert info.address == 0
        assert info.model == "MCC 118"
        assert info.serial == "12345678"


class TestScanHats:
    """Tests for the scan_hats function."""

    def test_import_error_when_daqhats_missing(self) -> None:
        """scan_hats raises ImportError when daqhats is not installed."""
        with patch.dict("sys.modules", {"daqhats": None}):
            with pytest.raises(ImportError, match="daqhats library is not installed"):
                scan_hats()

    def test_scan_finds_mcc118(self) -> None:
        """scan_hats detects MCC 118 at address 0."""
        mock_hat = MagicMock()
        mock_hat.serial.return_value = "ABC123"

        mock_daqhats = MagicMock()
        mock_daqhats.mcc118.return_value = mock_hat
        # Other HAT types should not be tried after 118 succeeds
        mock_daqhats.mcc134.side_effect = Exception("Should not be called")
        mock_daqhats.mcc152.side_effect = Exception("Should not be called")

        with patch.dict("sys.modules", {"daqhats": mock_daqhats}):
            found = scan_hats(addresses=[0])

        assert len(found) == 1
        assert found[0].address == 0
        assert found[0].model == "MCC 118"
        assert found[0].serial == "ABC123"

    def test_scan_finds_mcc134_after_118_fails(self) -> None:
        """scan_hats falls back to MCC 134 when MCC 118 fails."""
        mock_hat = MagicMock()
        mock_hat.serial.return_value = "DEF456"

        mock_daqhats = MagicMock()
        mock_daqhats.mcc118.side_effect = Exception("No MCC 118")
        mock_daqhats.mcc134.return_value = mock_hat
        mock_daqhats.mcc152.side_effect = Exception("Should not be called")

        with patch.dict("sys.modules", {"daqhats": mock_daqhats}):
            found = scan_hats(addresses=[2])

        assert len(found) == 1
        assert found[0].address == 2
        assert found[0].model == "MCC 134"
        assert found[0].serial == "DEF456"

    def test_scan_finds_mcc152(self) -> None:
        """scan_hats finds MCC 152 when others fail."""
        mock_hat = MagicMock()
        mock_hat.serial.return_value = "GHI789"

        mock_daqhats = MagicMock()
        mock_daqhats.mcc118.side_effect = Exception("No MCC 118")
        mock_daqhats.mcc134.side_effect = Exception("No MCC 134")
        mock_daqhats.mcc152.return_value = mock_hat

        with patch.dict("sys.modules", {"daqhats": mock_daqhats}):
            found = scan_hats(addresses=[1])

        assert len(found) == 1
        assert found[0].address == 1
        assert found[0].model == "MCC 152"
        assert found[0].serial == "GHI789"

    def test_scan_no_hats_found(self) -> None:
        """scan_hats returns empty list when no HATs respond."""
        mock_daqhats = MagicMock()
        mock_daqhats.mcc118.side_effect = Exception("No HAT")
        mock_daqhats.mcc134.side_effect = Exception("No HAT")
        mock_daqhats.mcc152.side_effect = Exception("No HAT")

        with patch.dict("sys.modules", {"daqhats": mock_daqhats}):
            found = scan_hats(addresses=[0, 1])

        assert found == []

    def test_scan_multiple_addresses(self) -> None:
        """scan_hats scans multiple addresses and finds different HATs."""
        mock_hat_118 = MagicMock()
        mock_hat_118.serial.return_value = "SER118"

        mock_hat_152 = MagicMock()
        mock_hat_152.serial.return_value = "SER152"

        def mock_mcc118(addr: int) -> MagicMock:
            if addr == 0:
                return mock_hat_118
            raise Exception("No MCC 118")

        def mock_mcc152(addr: int) -> MagicMock:
            if addr == 3:
                return mock_hat_152
            raise Exception("No MCC 152")

        mock_daqhats = MagicMock()
        mock_daqhats.mcc118.side_effect = mock_mcc118
        mock_daqhats.mcc134.side_effect = Exception("No MCC 134")
        mock_daqhats.mcc152.side_effect = mock_mcc152

        with patch.dict("sys.modules", {"daqhats": mock_daqhats}):
            found = scan_hats(addresses=[0, 1, 2, 3])

        assert len(found) == 2
        assert found[0].address == 0
        assert found[0].model == "MCC 118"
        assert found[1].address == 3
        assert found[1].model == "MCC 152"


class TestMain:
    """Tests for the main CLI entry point."""

    def test_help_flag(self) -> None:
        """--help prints usage and exits with 0."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0

    def test_invalid_address(self) -> None:
        """Invalid address returns exit code 1."""
        exit_code = main(["-a", "8"])
        assert exit_code == 1

    def test_missing_daqhats(self) -> None:
        """Missing daqhats library returns exit code 1."""
        with patch.dict("sys.modules", {"daqhats": None}):
            exit_code = main([])
        assert exit_code == 1

    def test_quiet_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--quiet outputs only HAT info in tab-separated format."""
        mock_hat = MagicMock()
        mock_hat.serial.return_value = "QQQQ"

        mock_daqhats = MagicMock()
        mock_daqhats.mcc118.return_value = mock_hat

        with patch.dict("sys.modules", {"daqhats": mock_daqhats}):
            exit_code = main(["-a", "0", "-q"])

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "0\tMCC 118\tQQQQ" in captured.out
        assert "MCC DAQ HAT Scan Results" not in captured.out

    def test_no_hats_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No HATs found prints appropriate message."""
        mock_daqhats = MagicMock()
        mock_daqhats.mcc118.side_effect = Exception("No HAT")
        mock_daqhats.mcc134.side_effect = Exception("No HAT")
        mock_daqhats.mcc152.side_effect = Exception("No HAT")

        with patch.dict("sys.modules", {"daqhats": mock_daqhats}):
            exit_code = main(["-a", "0"])

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "No MCC HATs found" in captured.out
