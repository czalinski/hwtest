"""Unit tests for the ADS1256 ADC driver."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from hwtest_waveshare.ads1256 import (
    Ads1256,
    Ads1256Config,
    Ads1256DataRate,
    Ads1256Gain,
    DATA_RATE_VALUES,
)


def _create_mock_gpio() -> MagicMock:
    """Create a mock GPIO interface (matching Gpio class)."""
    mock = MagicMock()
    # Mock the input method to return LOW (0) for DRDY ready
    mock.input.return_value = 0
    return mock


def _create_mock_spi() -> MagicMock:
    """Create a mock SPI device."""
    mock = MagicMock()
    # Default ADC read returns mid-scale (0 volts with GAIN_1)
    mock.readbytes.return_value = [0x00, 0x00, 0x00]
    mock.xfer2.return_value = [0x30]  # Chip ID in STATUS register
    return mock


class TestAds1256Config:
    """Tests for the Ads1256Config dataclass."""

    def test_default_config(self) -> None:
        """Default configuration uses expected values."""
        config = Ads1256Config()
        assert config.spi_bus == 0
        assert config.spi_device == 0
        assert config.cs_pin == 22
        assert config.drdy_pin == 17
        assert config.reset_pin == 18
        assert config.gain == Ads1256Gain.GAIN_1
        assert config.data_rate == Ads1256DataRate.SPS_100
        assert config.vref == 2.5

    def test_custom_config(self) -> None:
        """Custom configuration values are stored correctly."""
        config = Ads1256Config(
            spi_bus=1,
            spi_device=1,
            cs_pin=25,
            drdy_pin=24,
            reset_pin=None,
            gain=Ads1256Gain.GAIN_8,
            data_rate=Ads1256DataRate.SPS_1000,
            vref=3.3,
        )
        assert config.spi_bus == 1
        assert config.spi_device == 1
        assert config.cs_pin == 25
        assert config.drdy_pin == 24
        assert config.reset_pin is None
        assert config.gain == Ads1256Gain.GAIN_8
        assert config.data_rate == Ads1256DataRate.SPS_1000
        assert config.vref == 3.3


class TestAds1256Gain:
    """Tests for the Ads1256Gain enum."""

    def test_gain_values(self) -> None:
        """Gain enum values match expected bit patterns."""
        assert Ads1256Gain.GAIN_1.value == 0b000
        assert Ads1256Gain.GAIN_2.value == 0b001
        assert Ads1256Gain.GAIN_4.value == 0b010
        assert Ads1256Gain.GAIN_8.value == 0b011
        assert Ads1256Gain.GAIN_16.value == 0b100
        assert Ads1256Gain.GAIN_32.value == 0b101
        assert Ads1256Gain.GAIN_64.value == 0b110


class TestAds1256DataRate:
    """Tests for the Ads1256DataRate enum."""

    def test_data_rate_lookup(self) -> None:
        """Data rate values can be looked up in DATA_RATE_VALUES."""
        assert DATA_RATE_VALUES[Ads1256DataRate.SPS_30000] == 30000.0
        assert DATA_RATE_VALUES[Ads1256DataRate.SPS_1000] == 1000.0
        assert DATA_RATE_VALUES[Ads1256DataRate.SPS_100] == 100.0
        assert DATA_RATE_VALUES[Ads1256DataRate.SPS_2_5] == 2.5


class TestAds1256:
    """Tests for the Ads1256 driver class."""

    def test_not_open_initially(self) -> None:
        """Device is not open when created."""
        adc = Ads1256()
        assert not adc.is_open

    def test_open_and_close(self) -> None:
        """Device can be opened and closed."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        adc = Ads1256(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        assert adc.is_open
        # New GPIO interface uses setup(pin, direction, initial)
        # Direction: OUTPUT=1, INPUT=0; Value: HIGH=1, LOW=0
        mock_gpio.setup.assert_any_call(22, 1, initial=1)  # CS pin as output, high
        mock_gpio.setup.assert_any_call(17, 0)  # DRDY pin as input

        adc.close()
        assert not adc.is_open
        mock_gpio.close.assert_called()

    def test_double_open_raises(self) -> None:
        """Opening an already open device raises RuntimeError."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        adc = Ads1256(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        with pytest.raises(RuntimeError, match="already open"):
            adc.open()

        adc.close()

    def test_read_voltage_when_closed_raises(self) -> None:
        """Reading voltage when device is closed raises RuntimeError."""
        adc = Ads1256()
        with pytest.raises(RuntimeError, match="not open"):
            adc.read_voltage(0)

    def test_read_voltage_invalid_channel_raises(self) -> None:
        """Reading from invalid channel raises ValueError."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        adc = Ads1256(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        with pytest.raises(ValueError, match="channel must be 0-7"):
            adc.read_voltage(8)

        adc.close()

    def test_read_voltage_zero(self) -> None:
        """Reading 0V returns approximately 0."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()
        # Mid-scale (0x000000) represents 0V
        mock_spi.readbytes.return_value = [0x00, 0x00, 0x00]

        adc = Ads1256(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        voltage = adc.read_voltage(0)
        assert abs(voltage) < 0.001  # Near zero

        adc.close()

    def test_read_voltage_positive(self) -> None:
        """Reading positive voltage returns correct value."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()
        # ~Half of full scale positive (2.5V with GAIN_1, Vref=2.5V -> ~1.25V)
        mock_spi.readbytes.return_value = [0x40, 0x00, 0x00]

        adc = Ads1256(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        voltage = adc.read_voltage(0)
        # 0x400000 / 0x7FFFFF * 2.5V = ~1.25V
        assert 1.0 < voltage < 1.5

        adc.close()

    def test_read_voltage_negative(self) -> None:
        """Reading negative voltage returns negative value."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()
        # Negative value (two's complement)
        mock_spi.readbytes.return_value = [0xC0, 0x00, 0x00]

        adc = Ads1256(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        voltage = adc.read_voltage(0)
        assert voltage < 0

        adc.close()

    def test_read_differential(self) -> None:
        """Differential reading uses both channels."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()
        mock_spi.readbytes.return_value = [0x20, 0x00, 0x00]

        adc = Ads1256(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        voltage = adc.read_differential(0, 1)
        assert voltage > 0  # Should be positive for this raw value

        adc.close()

    def test_read_differential_invalid_channels(self) -> None:
        """Differential reading with invalid channels raises ValueError."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        adc = Ads1256(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        with pytest.raises(ValueError, match="positive channel must be 0-7"):
            adc.read_differential(8, 0)

        with pytest.raises(ValueError, match="negative channel must be 0-7"):
            adc.read_differential(0, 9)

        adc.close()

    def test_sample_rate_property(self) -> None:
        """sample_rate property returns configured data rate in Hz."""
        config = Ads1256Config(data_rate=Ads1256DataRate.SPS_1000)
        adc = Ads1256(config)
        assert adc.sample_rate == 1000.0

    def test_get_chip_id_when_closed_raises(self) -> None:
        """get_chip_id when device is closed raises RuntimeError."""
        adc = Ads1256()
        with pytest.raises(RuntimeError, match="not open"):
            adc.get_chip_id()

    def test_get_chip_id(self) -> None:
        """get_chip_id returns correct value from STATUS register."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()
        # STATUS register with chip ID 0x03 in upper nibble
        mock_spi.readbytes.return_value = [0x30]

        adc = Ads1256(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        chip_id = adc.get_chip_id()
        assert chip_id == 0x03

        adc.close()

    def test_read_all_channels(self) -> None:
        """read_all_channels returns 8 voltage values."""
        mock_gpio = _create_mock_gpio()
        mock_spi = _create_mock_spi()

        adc = Ads1256(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        voltages = adc.read_all_channels()
        assert len(voltages) == 8
        assert all(isinstance(v, float) for v in voltages)

        adc.close()
