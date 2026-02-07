"""Unit tests for the ADS1263 ADC driver."""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

import pytest

from hwtest_sim_pi4_waveshare.ads1263 import (
    Ads1263,
    Ads1263Config,
    Ads1263DataRate,
    Ads1263Gain,
)


def _create_mock_spi() -> MagicMock:
    """Create a mock SPI device."""
    mock = MagicMock()
    mock.xfer2.return_value = [0] * 10
    return mock


def _create_mock_gpio() -> MagicMock:
    """Create a mock GPIO module."""
    mock = MagicMock()
    mock.BCM = 11
    mock.OUT = 1
    mock.IN = 0
    mock.HIGH = 1
    mock.LOW = 0
    mock.input.return_value = 0  # DRDY low (data ready)
    return mock


class TestAds1263Config:
    """Tests for Ads1263Config."""

    def test_default_config(self) -> None:
        """Default config uses expected values."""
        config = Ads1263Config()
        assert config.spi_bus == 0
        assert config.spi_device == 1
        assert config.cs_pin == 22
        assert config.drdy_pin == 17
        assert config.reset_pin == 18
        assert config.vref == 2.5
        assert config.gain == Ads1263Gain.GAIN_1
        assert config.data_rate == Ads1263DataRate.SPS_400

    def test_custom_config(self) -> None:
        """Custom config values are stored correctly."""
        config = Ads1263Config(
            spi_bus=1,
            spi_device=0,
            cs_pin=25,
            vref=3.3,
            gain=Ads1263Gain.GAIN_4,
            data_rate=Ads1263DataRate.SPS_1200,
        )
        assert config.spi_bus == 1
        assert config.spi_device == 0
        assert config.cs_pin == 25
        assert config.vref == 3.3
        assert config.gain == Ads1263Gain.GAIN_4
        assert config.data_rate == Ads1263DataRate.SPS_1200


class TestAds1263:
    """Tests for the Ads1263 driver."""

    def test_not_open_initially(self) -> None:
        """Device is not open when created."""
        adc = Ads1263()
        assert not adc.is_open

    def test_open_and_close(self) -> None:
        """Device can be opened and closed."""
        mock_spi = _create_mock_spi()
        mock_gpio = _create_mock_gpio()

        adc = Ads1263(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        assert adc.is_open
        mock_gpio.setmode.assert_called()
        mock_gpio.setup.assert_called()

        adc.close()
        assert not adc.is_open

    def test_double_open_raises(self) -> None:
        """Opening an already open device raises RuntimeError."""
        mock_spi = _create_mock_spi()
        mock_gpio = _create_mock_gpio()

        adc = Ads1263(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        with pytest.raises(RuntimeError, match="already open"):
            adc.open()

        adc.close()

    def test_read_voltage_when_closed_raises(self) -> None:
        """Reading voltage when closed raises RuntimeError."""
        adc = Ads1263()
        with pytest.raises(RuntimeError, match="not open"):
            adc.read_voltage(0)

    def test_read_raw_when_closed_raises(self) -> None:
        """Reading raw value when closed raises RuntimeError."""
        adc = Ads1263()
        with pytest.raises(RuntimeError, match="not open"):
            adc.read_raw()

    def test_set_channel_when_closed_raises(self) -> None:
        """Setting channel when closed raises RuntimeError."""
        adc = Ads1263()
        with pytest.raises(RuntimeError, match="not open"):
            adc.set_channel(0)

    def test_get_chip_id_when_closed_raises(self) -> None:
        """Getting chip ID when closed raises RuntimeError."""
        adc = Ads1263()
        with pytest.raises(RuntimeError, match="not open"):
            adc.get_chip_id()

    def test_read_voltage_invalid_channel_raises(self) -> None:
        """Reading from invalid channel raises ValueError."""
        mock_spi = _create_mock_spi()
        mock_gpio = _create_mock_gpio()

        adc = Ads1263(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        with pytest.raises(ValueError, match="channel must be 0-9"):
            adc.read_voltage(10)

        with pytest.raises(ValueError, match="channel must be 0-9"):
            adc.read_voltage(-1)

        adc.close()

    def test_set_channel_invalid_raises(self) -> None:
        """Setting invalid channel raises ValueError."""
        mock_spi = _create_mock_spi()
        mock_gpio = _create_mock_gpio()

        adc = Ads1263(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        with pytest.raises(ValueError, match="positive channel must be 0-15"):
            adc.set_channel(16)

        with pytest.raises(ValueError, match="negative channel must be 0-15"):
            adc.set_channel(0, 16)

        adc.close()

    def test_read_raw_returns_value(self) -> None:
        """read_raw returns parsed ADC value."""
        mock_spi = _create_mock_spi()
        mock_gpio = _create_mock_gpio()

        # Simulate ADC response: status + 4 bytes of data
        # Raw value 0x00800000 (positive)
        mock_spi.xfer2.return_value = [0x00, 0x00, 0x00, 0x80, 0x00, 0x00]

        adc = Ads1263(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        raw = adc.read_raw()
        assert raw == 0x00800000

        adc.close()

    def test_read_raw_negative_value(self) -> None:
        """read_raw correctly handles negative values."""
        mock_spi = _create_mock_spi()
        mock_gpio = _create_mock_gpio()

        # Simulate negative ADC response: 0xFF800000
        mock_spi.xfer2.return_value = [0x00, 0x00, 0xFF, 0x80, 0x00, 0x00]

        adc = Ads1263(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        raw = adc.read_raw()
        assert raw == -8388608  # 0xFF800000 as signed

        adc.close()

    def test_read_voltage_converts_correctly(self) -> None:
        """read_voltage correctly converts raw value to voltage."""
        mock_spi = _create_mock_spi()
        mock_gpio = _create_mock_gpio()

        # Simulate half-scale positive: 0x40000000
        mock_spi.xfer2.return_value = [0x00, 0x00, 0x40, 0x00, 0x00, 0x00]

        config = Ads1263Config(vref=2.5, gain=Ads1263Gain.GAIN_1)
        adc = Ads1263(config=config, spi=mock_spi, gpio=mock_gpio)
        adc.open()

        voltage = adc.read_voltage()

        # Half scale should be ~1.25V with 2.5V reference
        assert abs(voltage - 1.25) < 0.01

        adc.close()

    def test_read_voltage_with_gain(self) -> None:
        """read_voltage correctly applies gain factor."""
        mock_spi = _create_mock_spi()
        mock_gpio = _create_mock_gpio()

        # Simulate half-scale positive: 0x40000000
        mock_spi.xfer2.return_value = [0x00, 0x00, 0x40, 0x00, 0x00, 0x00]

        config = Ads1263Config(vref=2.5, gain=Ads1263Gain.GAIN_4)
        adc = Ads1263(config=config, spi=mock_spi, gpio=mock_gpio)
        adc.open()

        voltage = adc.read_voltage()

        # Half scale with gain 4 should be ~0.3125V
        assert abs(voltage - 0.3125) < 0.01

        adc.close()

    def test_read_differential(self) -> None:
        """read_differential sets channels and reads."""
        mock_spi = _create_mock_spi()
        mock_gpio = _create_mock_gpio()

        mock_spi.xfer2.return_value = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00]

        adc = Ads1263(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        voltage = adc.read_differential(0, 1)
        assert isinstance(voltage, float)

        adc.close()

    def test_read_differential_invalid_channel_raises(self) -> None:
        """read_differential with invalid channel raises ValueError."""
        mock_spi = _create_mock_spi()
        mock_gpio = _create_mock_gpio()

        adc = Ads1263(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        with pytest.raises(ValueError, match="positive channel must be 0-9"):
            adc.read_differential(10, 0)

        with pytest.raises(ValueError, match="negative channel must be 0-9"):
            adc.read_differential(0, 10)

        adc.close()

    def test_read_all_channels(self) -> None:
        """read_all_channels returns list of 10 voltages."""
        mock_spi = _create_mock_spi()
        mock_gpio = _create_mock_gpio()

        mock_spi.xfer2.return_value = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00]

        adc = Ads1263(spi=mock_spi, gpio=mock_gpio)
        adc.open()

        voltages = adc.read_all_channels()

        assert len(voltages) == 10
        assert all(isinstance(v, float) for v in voltages)

        adc.close()

    def test_drdy_timeout_raises(self) -> None:
        """read_raw raises RuntimeError on DRDY timeout."""
        mock_spi = _create_mock_spi()
        mock_gpio = _create_mock_gpio()

        # DRDY always high (not ready)
        mock_gpio.input.return_value = 1

        config = Ads1263Config()
        adc = Ads1263(config=config, spi=mock_spi, gpio=mock_gpio)
        adc.open()

        # Patch _wait_drdy to return False immediately
        adc._wait_drdy = lambda timeout=2.0: False  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="timeout"):
            adc.read_raw()

        adc.close()

    def test_config_property(self) -> None:
        """config property returns the configuration."""
        config = Ads1263Config(cs_pin=25)
        adc = Ads1263(config=config)
        assert adc.config.cs_pin == 25

    def test_channel_constants(self) -> None:
        """Channel constants have expected values."""
        assert Ads1263.AIN0 == 0
        assert Ads1263.AIN9 == 9
        assert Ads1263.AINCOM == 10
