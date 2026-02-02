"""Unit tests for the UUT simulator."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hwtest_uut.mcp23017 import PinDirection
from hwtest_uut.simulator import SimulatorConfig, UutSimulator


def _create_mock_can_bus() -> MagicMock:
    """Create a mock CAN bus."""
    mock = MagicMock()
    mock.recv.return_value = None
    return mock


def _create_mock_gpio_bus() -> MagicMock:
    """Create a mock I2C bus for GPIO."""
    mock = MagicMock()
    mock.read_byte_data.return_value = 0
    return mock


def _create_mock_dac() -> MagicMock:
    """Create a mock DAC."""
    mock = MagicMock()
    return mock


def _create_mock_adc() -> MagicMock:
    """Create a mock ADC."""
    mock = MagicMock()
    mock.read_voltage.return_value = 2.5
    mock.read_all_channels.return_value = [0.0] * 8
    return mock


class TestSimulatorConfig:
    """Tests for SimulatorConfig."""

    def test_default_config(self) -> None:
        """Default config uses expected values."""
        config = SimulatorConfig()
        assert config.can_enabled is True
        assert config.can_interface == "can0"
        assert config.can_bitrate == 500000
        assert config.dac_enabled is True
        assert config.adc_enabled is True
        assert config.gpio_enabled is True
        assert config.gpio_address == 0x20

    def test_custom_config(self) -> None:
        """Custom config values are stored correctly."""
        config = SimulatorConfig(
            can_enabled=False,
            can_interface="can1",
            gpio_address=0x27,
        )
        assert config.can_enabled is False
        assert config.can_interface == "can1"
        assert config.gpio_address == 0x27


class TestUutSimulator:
    """Tests for UutSimulator."""

    def test_not_running_initially(self) -> None:
        """Simulator is not running when created."""
        sim = UutSimulator()
        assert not sim.is_running

    def test_start_and_stop(self) -> None:
        """Simulator can be started and stopped."""
        config = SimulatorConfig(
            can_enabled=False,
            dac_enabled=False,
            adc_enabled=False,
            gpio_enabled=False,
        )
        sim = UutSimulator(config=config)

        sim.start()
        assert sim.is_running

        sim.stop()
        assert not sim.is_running

    def test_double_start_raises(self) -> None:
        """Starting an already running simulator raises RuntimeError."""
        config = SimulatorConfig(
            can_enabled=False,
            dac_enabled=False,
            adc_enabled=False,
            gpio_enabled=False,
        )
        sim = UutSimulator(config=config)
        sim.start()

        with pytest.raises(RuntimeError, match="already running"):
            sim.start()

        sim.stop()

    def test_uptime(self) -> None:
        """Uptime increases while running."""
        config = SimulatorConfig(
            can_enabled=False,
            dac_enabled=False,
            adc_enabled=False,
            gpio_enabled=False,
        )
        sim = UutSimulator(config=config)
        sim.start()

        uptime = sim.uptime
        assert uptime >= 0

        sim.stop()

    def test_config_property(self) -> None:
        """Config property returns configuration."""
        config = SimulatorConfig(can_interface="vcan0")
        sim = UutSimulator(config=config)
        assert sim.config.can_interface == "vcan0"

    # -------------------------------------------------------------------------
    # CAN Tests
    # -------------------------------------------------------------------------

    def test_can_send_when_disabled_raises(self) -> None:
        """Sending CAN when interface not available raises RuntimeError."""
        config = SimulatorConfig(can_enabled=False)
        sim = UutSimulator(config=config)
        sim.start()

        from hwtest_uut.can_interface import CanMessage

        with pytest.raises(RuntimeError, match="CAN interface not available"):
            sim.can_send(CanMessage(arbitration_id=0x100))

        sim.stop()

    def test_can_with_mock_bus(self) -> None:
        """CAN interface works with mock bus."""
        mock_bus = _create_mock_can_bus()
        config = SimulatorConfig(
            dac_enabled=False,
            adc_enabled=False,
            gpio_enabled=False,
        )
        sim = UutSimulator(config=config, can_bus=mock_bus)
        sim.start()

        from hwtest_uut.can_interface import CanMessage

        msg = CanMessage(arbitration_id=0x100, data=b"\x01\x02")
        sim.can_send(msg)

        mock_bus.send.assert_called_once()

        sim.stop()

    def test_can_echo_config(self) -> None:
        """CAN echo configuration works."""
        config = SimulatorConfig(
            can_enabled=False,
            dac_enabled=False,
            adc_enabled=False,
            gpio_enabled=False,
        )
        sim = UutSimulator(config=config)
        sim.start()

        sim.can_set_echo(enabled=True, id_offset=0x100, filter_ids=[0x200, 0x300])
        echo = sim.can_get_echo_config()

        assert echo.enabled is True
        assert echo.id_offset == 0x100
        assert echo.filter_ids == [0x200, 0x300]

        sim.stop()

    def test_can_received_messages(self) -> None:
        """Received CAN messages are stored."""
        config = SimulatorConfig(
            can_enabled=False,
            dac_enabled=False,
            adc_enabled=False,
            gpio_enabled=False,
        )
        sim = UutSimulator(config=config)
        sim.start()

        # Initially empty
        assert len(sim.can_get_received()) == 0

        sim.can_clear_received()
        assert len(sim.can_get_received()) == 0

        sim.stop()

    # -------------------------------------------------------------------------
    # DAC Tests
    # -------------------------------------------------------------------------

    def test_dac_write_and_read(self) -> None:
        """DAC write and read work correctly."""
        config = SimulatorConfig(
            can_enabled=False,
            dac_enabled=True,
            adc_enabled=False,
            gpio_enabled=False,
        )
        sim = UutSimulator(config=config)
        sim.start()

        sim.dac_write(0, 2.5)
        assert sim.dac_read(0) == 2.5

        sim.dac_write(1, 3.3)
        assert sim.dac_read(1) == 3.3

        sim.stop()

    def test_dac_write_both(self) -> None:
        """DAC write_both sets both channels."""
        config = SimulatorConfig(
            can_enabled=False,
            dac_enabled=True,
            adc_enabled=False,
            gpio_enabled=False,
        )
        sim = UutSimulator(config=config)
        sim.start()

        sim.dac_write_both(1.0, 2.0)
        values = sim.dac_read_all()

        assert values[0] == 1.0
        assert values[1] == 2.0

        sim.stop()

    def test_dac_invalid_channel_raises(self) -> None:
        """Invalid DAC channel raises ValueError."""
        config = SimulatorConfig(
            can_enabled=False,
            dac_enabled=True,
            adc_enabled=False,
            gpio_enabled=False,
        )
        sim = UutSimulator(config=config)
        sim.start()

        with pytest.raises(ValueError, match="channel must be 0 or 1"):
            sim.dac_write(2, 1.0)

        with pytest.raises(ValueError, match="channel must be 0 or 1"):
            sim.dac_read(3)

        sim.stop()

    def test_dac_invalid_voltage_raises(self) -> None:
        """Invalid DAC voltage raises ValueError."""
        config = SimulatorConfig(
            can_enabled=False,
            dac_enabled=True,
            adc_enabled=False,
            gpio_enabled=False,
        )
        sim = UutSimulator(config=config)
        sim.start()

        with pytest.raises(ValueError, match="voltage must be 0-5.0V"):
            sim.dac_write(0, 6.0)

        with pytest.raises(ValueError, match="voltage must be 0-5.0V"):
            sim.dac_write(0, -1.0)

        sim.stop()

    def test_dac_with_mock(self) -> None:
        """DAC works with mock device."""
        mock_dac = _create_mock_dac()
        config = SimulatorConfig(
            can_enabled=False,
            adc_enabled=False,
            gpio_enabled=False,
        )
        sim = UutSimulator(config=config, dac=mock_dac)
        sim.start()

        sim.dac_write(0, 2.5)
        mock_dac.write_voltage.assert_called_with(0, 2.5)

        sim.stop()

    # -------------------------------------------------------------------------
    # ADC Tests
    # -------------------------------------------------------------------------

    def test_adc_read_when_not_available_raises(self) -> None:
        """ADC read when not available raises RuntimeError."""
        config = SimulatorConfig(
            can_enabled=False,
            dac_enabled=False,
            adc_enabled=False,
            gpio_enabled=False,
        )
        sim = UutSimulator(config=config)
        sim.start()

        with pytest.raises(RuntimeError, match="ADC not available"):
            sim.adc_read(0)

        with pytest.raises(RuntimeError, match="ADC not available"):
            sim.adc_read_all()

        sim.stop()

    def test_adc_invalid_channel_raises(self) -> None:
        """Invalid ADC channel raises ValueError."""
        mock_adc = _create_mock_adc()
        config = SimulatorConfig(
            can_enabled=False,
            dac_enabled=False,
            gpio_enabled=False,
        )
        sim = UutSimulator(config=config, adc=mock_adc)
        sim.start()

        with pytest.raises(ValueError, match="channel must be 0-7"):
            sim.adc_read(8)

        sim.stop()

    def test_adc_with_mock(self) -> None:
        """ADC works with mock device."""
        mock_adc = _create_mock_adc()
        mock_adc.read_voltage.return_value = 1.234
        config = SimulatorConfig(
            can_enabled=False,
            dac_enabled=False,
            gpio_enabled=False,
        )
        sim = UutSimulator(config=config, adc=mock_adc)
        sim.start()

        result = sim.adc_read(3)
        assert result == 1.234
        mock_adc.read_voltage.assert_called_with(3)

        sim.stop()

    # -------------------------------------------------------------------------
    # GPIO Tests
    # -------------------------------------------------------------------------

    def test_gpio_when_not_available_raises(self) -> None:
        """GPIO operations when not available raise RuntimeError."""
        config = SimulatorConfig(
            can_enabled=False,
            dac_enabled=False,
            adc_enabled=False,
            gpio_enabled=False,
        )
        sim = UutSimulator(config=config)
        sim.start()

        with pytest.raises(RuntimeError, match="GPIO not available"):
            sim.gpio_read(0)

        with pytest.raises(RuntimeError, match="GPIO not available"):
            sim.gpio_write(0, True)

        with pytest.raises(RuntimeError, match="GPIO not available"):
            sim.gpio_set_direction(0, PinDirection.OUTPUT)

        sim.stop()

    def test_gpio_with_mock_bus(self) -> None:
        """GPIO works with mock I2C bus."""
        mock_bus = _create_mock_gpio_bus()
        config = SimulatorConfig(
            can_enabled=False,
            dac_enabled=False,
            adc_enabled=False,
        )
        sim = UutSimulator(config=config, gpio_bus=mock_bus)
        sim.start()

        sim.gpio_set_direction(0, PinDirection.OUTPUT)
        sim.gpio_write(0, True)
        sim.gpio_read(0)

        sim.stop()

    def test_gpio_port_operations(self) -> None:
        """GPIO port operations work correctly."""
        mock_bus = _create_mock_gpio_bus()
        mock_bus.read_byte_data.return_value = 0xAB
        config = SimulatorConfig(
            can_enabled=False,
            dac_enabled=False,
            adc_enabled=False,
        )
        sim = UutSimulator(config=config, gpio_bus=mock_bus)
        sim.start()

        sim.gpio_write_port("A", 0xFF)
        result = sim.gpio_read_port("A")
        assert result == 0xAB

        sim.stop()

    def test_gpio_all_operations(self) -> None:
        """GPIO all-pin operations work correctly."""
        mock_bus = _create_mock_gpio_bus()
        mock_bus.read_byte_data.side_effect = [0xCD, 0xAB]
        config = SimulatorConfig(
            can_enabled=False,
            dac_enabled=False,
            adc_enabled=False,
        )
        sim = UutSimulator(config=config, gpio_bus=mock_bus)
        sim.start()

        sim.gpio_write_all(0xFFFF)
        result = sim.gpio_read_all()
        assert result == 0xABCD

        sim.stop()

    def test_gpio_pullup(self) -> None:
        """GPIO pullup configuration works."""
        mock_bus = _create_mock_gpio_bus()
        config = SimulatorConfig(
            can_enabled=False,
            dac_enabled=False,
            adc_enabled=False,
        )
        sim = UutSimulator(config=config, gpio_bus=mock_bus)
        sim.start()

        sim.gpio_set_pullup(5, True)

        sim.stop()
