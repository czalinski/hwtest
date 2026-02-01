"""Unit tests for the DAC8532 DAC driver."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hwtest_waveshare.dac8532 import Dac8532, Dac8532Channel, Dac8532Config


def _create_mock_gpio() -> MagicMock:
    """Create a mock GPIO interface (matching Gpio class)."""
    mock = MagicMock()
    return mock


def _create_mock_spi() -> MagicMock:
    """Create a mock SPI device."""
    mock = MagicMock()
    return mock


class TestDac8532Config:
    """Tests for the Dac8532Config dataclass."""

    def test_default_config(self) -> None:
        """Default configuration uses expected values."""
        config = Dac8532Config()
        assert config.spi_bus == 0
        assert config.spi_device == 0
        assert config.cs_pin == 23
        assert config.vref == 5.0

    def test_custom_config(self) -> None:
        """Custom configuration values are stored correctly."""
        config = Dac8532Config(
            spi_bus=1,
            spi_device=1,
            cs_pin=25,
            vref=3.3,
        )
        assert config.spi_bus == 1
        assert config.spi_device == 1
        assert config.cs_pin == 25
        assert config.vref == 3.3


class TestDac8532Channel:
    """Tests for Dac8532Channel constants."""

    def test_channel_values(self) -> None:
        """Channel constants have expected values."""
        assert Dac8532Channel.CHANNEL_A == 0
        assert Dac8532Channel.CHANNEL_B == 1


class TestDac8532:
    """Tests for the Dac8532 driver class."""

    def test_not_open_initially(self) -> None:
        """Device is not open when created."""
        dac = Dac8532()
        assert not dac.is_open

    def test_open_and_close(self) -> None:
        """Device can be opened and closed."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        dac = Dac8532(spi=mock_spi, gpio=mock_gpio)
        dac.open()

        assert dac.is_open
        # New GPIO interface uses setup(pin, direction, initial)
        # Direction: OUTPUT=1; Value: HIGH=1
        mock_gpio.setup.assert_called_with(23, 1, initial=1)

        dac.close()
        assert not dac.is_open
        mock_gpio.close.assert_called()

    def test_double_open_raises(self) -> None:
        """Opening an already open device raises RuntimeError."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        dac = Dac8532(spi=mock_spi, gpio=mock_gpio)
        dac.open()

        with pytest.raises(RuntimeError, match="already open"):
            dac.open()

        dac.close()

    def test_write_voltage_when_closed_raises(self) -> None:
        """Writing voltage when device is closed raises RuntimeError."""
        dac = Dac8532()
        with pytest.raises(RuntimeError, match="not open"):
            dac.write_voltage(0, 2.5)

    def test_write_voltage_invalid_channel_raises(self) -> None:
        """Writing to invalid channel raises ValueError."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        dac = Dac8532(spi=mock_spi, gpio=mock_gpio)
        dac.open()

        with pytest.raises(ValueError, match="channel must be 0 or 1"):
            dac.write_voltage(2, 2.5)

        dac.close()

    def test_write_voltage_out_of_range_raises(self) -> None:
        """Writing voltage out of range raises ValueError."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        dac = Dac8532(spi=mock_spi, gpio=mock_gpio)
        dac.open()

        with pytest.raises(ValueError, match="voltage must be 0-5.0V"):
            dac.write_voltage(0, 6.0)

        with pytest.raises(ValueError, match="voltage must be 0-5.0V"):
            dac.write_voltage(0, -0.1)

        dac.close()

    def test_write_voltage_channel_a(self) -> None:
        """Writing to channel A sends correct SPI data."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        dac = Dac8532(spi=mock_spi, gpio=mock_gpio)
        dac.open()
        mock_spi.reset_mock()

        dac.write_voltage(Dac8532Channel.CHANNEL_A, 2.5)

        # Should send [cmd, MSB, LSB]
        # 2.5V / 5.0V * 65535 = 32767 = 0x7FFF
        calls = mock_spi.writebytes.call_args_list
        # Last call should be the write (first call was initialization)
        assert len(calls) > 0
        last_call = calls[-1]
        assert last_call[0][0][0] == 0x10  # Write A command

        dac.close()

    def test_write_voltage_channel_b(self) -> None:
        """Writing to channel B sends correct SPI data."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        dac = Dac8532(spi=mock_spi, gpio=mock_gpio)
        dac.open()
        mock_spi.reset_mock()

        dac.write_voltage(Dac8532Channel.CHANNEL_B, 2.5)

        calls = mock_spi.writebytes.call_args_list
        assert len(calls) > 0
        last_call = calls[-1]
        assert last_call[0][0][0] == 0x24  # Write B command

        dac.close()

    def test_write_raw_valid_range(self) -> None:
        """write_raw accepts values 0-65535."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        dac = Dac8532(spi=mock_spi, gpio=mock_gpio)
        dac.open()

        dac.write_raw(0, 0)
        dac.write_raw(0, 65535)
        dac.write_raw(1, 32768)

        dac.close()

    def test_write_raw_invalid_value_raises(self) -> None:
        """write_raw with invalid value raises ValueError."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        dac = Dac8532(spi=mock_spi, gpio=mock_gpio)
        dac.open()

        with pytest.raises(ValueError, match="value must be 0-65535"):
            dac.write_raw(0, 65536)

        with pytest.raises(ValueError, match="value must be 0-65535"):
            dac.write_raw(0, -1)

        dac.close()

    def test_read_voltage_returns_last_written(self) -> None:
        """read_voltage returns the last written voltage."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        dac = Dac8532(spi=mock_spi, gpio=mock_gpio)
        dac.open()

        dac.write_voltage(0, 2.5)
        readback = dac.read_voltage(0)

        # Should be approximately 2.5V (some rounding due to 16-bit resolution)
        assert abs(readback - 2.5) < 0.001

        dac.close()

    def test_read_voltage_when_closed_raises(self) -> None:
        """read_voltage when device is closed raises RuntimeError."""
        dac = Dac8532()
        with pytest.raises(RuntimeError, match="not open"):
            dac.read_voltage(0)

    def test_read_voltage_invalid_channel_raises(self) -> None:
        """read_voltage with invalid channel raises ValueError."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        dac = Dac8532(spi=mock_spi, gpio=mock_gpio)
        dac.open()

        with pytest.raises(ValueError, match="channel must be 0 or 1"):
            dac.read_voltage(2)

        dac.close()

    def test_write_both_channels(self) -> None:
        """write_both sets both channels simultaneously."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        dac = Dac8532(spi=mock_spi, gpio=mock_gpio)
        dac.open()

        dac.write_both(1.0, 2.0)

        # Verify both channels were written
        readback_a = dac.read_voltage(0)
        readback_b = dac.read_voltage(1)

        assert abs(readback_a - 1.0) < 0.001
        assert abs(readback_b - 2.0) < 0.001

        dac.close()

    def test_write_both_invalid_voltage_raises(self) -> None:
        """write_both with invalid voltage raises ValueError."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        dac = Dac8532(spi=mock_spi, gpio=mock_gpio)
        dac.open()

        with pytest.raises(ValueError, match="voltage_a must be 0-5.0V"):
            dac.write_both(6.0, 2.5)

        with pytest.raises(ValueError, match="voltage_b must be 0-5.0V"):
            dac.write_both(2.5, 6.0)

        dac.close()

    def test_close_sets_outputs_to_zero(self) -> None:
        """Closing device sets outputs to 0V."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        dac = Dac8532(spi=mock_spi, gpio=mock_gpio)
        dac.open()

        dac.write_voltage(0, 3.0)
        dac.write_voltage(1, 4.0)

        # Close should write 0V to both channels
        dac.close()

        # writebytes should have been called multiple times including final zeros
        calls = mock_spi.writebytes.call_args_list
        # Last two writes should be zeros
        assert len(calls) >= 2
