"""Unit tests for the MCP23017 GPIO expander driver."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hwtest_uut.mcp23017 import Mcp23017, Mcp23017Config, Mcp23017Register, PinDirection


def _create_mock_bus() -> MagicMock:
    """Create a mock I2C bus."""
    mock = MagicMock()
    mock.read_byte_data.return_value = 0
    return mock


class TestMcp23017Config:
    """Tests for Mcp23017Config."""

    def test_default_config(self) -> None:
        """Default config uses expected values."""
        config = Mcp23017Config()
        assert config.i2c_bus == 1
        assert config.address == 0x20

    def test_custom_config(self) -> None:
        """Custom config values are stored correctly."""
        config = Mcp23017Config(i2c_bus=2, address=0x27)
        assert config.i2c_bus == 2
        assert config.address == 0x27

    def test_invalid_address_raises(self) -> None:
        """Invalid address raises ValueError."""
        with pytest.raises(ValueError, match="address must be 0x20-0x27"):
            Mcp23017Config(address=0x28)
        with pytest.raises(ValueError, match="address must be 0x20-0x27"):
            Mcp23017Config(address=0x1F)


class TestMcp23017:
    """Tests for the Mcp23017 driver."""

    def test_not_open_initially(self) -> None:
        """Device is not open when created."""
        gpio = Mcp23017()
        assert not gpio.is_open

    def test_open_and_close(self) -> None:
        """Device can be opened and closed."""
        mock_bus = _create_mock_bus()
        gpio = Mcp23017(bus=mock_bus)

        gpio.open()
        assert gpio.is_open

        gpio.close()
        assert not gpio.is_open
        mock_bus.close.assert_called()

    def test_double_open_raises(self) -> None:
        """Opening an already open device raises RuntimeError."""
        mock_bus = _create_mock_bus()
        gpio = Mcp23017(bus=mock_bus)
        gpio.open()

        with pytest.raises(RuntimeError, match="already open"):
            gpio.open()

        gpio.close()

    def test_set_pin_direction_when_closed_raises(self) -> None:
        """Setting direction when closed raises RuntimeError."""
        gpio = Mcp23017()
        with pytest.raises(RuntimeError, match="not open"):
            gpio.set_pin_direction(0, PinDirection.OUTPUT)

    def test_set_pin_direction_invalid_pin_raises(self) -> None:
        """Invalid pin raises ValueError."""
        mock_bus = _create_mock_bus()
        gpio = Mcp23017(bus=mock_bus)
        gpio.open()

        with pytest.raises(ValueError, match="pin must be 0-15"):
            gpio.set_pin_direction(16, PinDirection.OUTPUT)

        with pytest.raises(ValueError, match="pin must be 0-15"):
            gpio.set_pin_direction(-1, PinDirection.OUTPUT)

        gpio.close()

    def test_set_pin_direction_port_a(self) -> None:
        """Setting direction on port A writes correct register."""
        mock_bus = _create_mock_bus()
        gpio = Mcp23017(bus=mock_bus)
        gpio.open()
        mock_bus.reset_mock()

        gpio.set_pin_direction(0, PinDirection.OUTPUT)

        # Should write IODIRA register with bit 0 cleared
        mock_bus.write_byte_data.assert_called_with(0x20, Mcp23017Register.IODIRA, 0xFE)

        gpio.close()

    def test_set_pin_direction_port_b(self) -> None:
        """Setting direction on port B writes correct register."""
        mock_bus = _create_mock_bus()
        gpio = Mcp23017(bus=mock_bus)
        gpio.open()
        mock_bus.reset_mock()

        gpio.set_pin_direction(8, PinDirection.OUTPUT)

        # Should write IODIRB register with bit 0 cleared
        mock_bus.write_byte_data.assert_called_with(0x20, Mcp23017Register.IODIRB, 0xFE)

        gpio.close()

    def test_write_pin_when_closed_raises(self) -> None:
        """Writing pin when closed raises RuntimeError."""
        gpio = Mcp23017()
        with pytest.raises(RuntimeError, match="not open"):
            gpio.write_pin(0, True)

    def test_write_pin_port_a(self) -> None:
        """Writing to port A pin sets correct register."""
        mock_bus = _create_mock_bus()
        gpio = Mcp23017(bus=mock_bus)
        gpio.open()
        mock_bus.reset_mock()

        gpio.write_pin(3, True)

        mock_bus.write_byte_data.assert_called_with(0x20, Mcp23017Register.OLATA, 0x08)

        gpio.close()

    def test_write_pin_port_b(self) -> None:
        """Writing to port B pin sets correct register."""
        mock_bus = _create_mock_bus()
        gpio = Mcp23017(bus=mock_bus)
        gpio.open()
        mock_bus.reset_mock()

        gpio.write_pin(11, True)

        mock_bus.write_byte_data.assert_called_with(0x20, Mcp23017Register.OLATB, 0x08)

        gpio.close()

    def test_read_pin_when_closed_raises(self) -> None:
        """Reading pin when closed raises RuntimeError."""
        gpio = Mcp23017()
        with pytest.raises(RuntimeError, match="not open"):
            gpio.read_pin(0)

    def test_read_pin_port_a(self) -> None:
        """Reading from port A pin reads correct register."""
        mock_bus = _create_mock_bus()
        mock_bus.read_byte_data.return_value = 0x04  # Pin 2 high
        gpio = Mcp23017(bus=mock_bus)
        gpio.open()

        result = gpio.read_pin(2)

        assert result is True
        mock_bus.read_byte_data.assert_called_with(0x20, Mcp23017Register.GPIOA)

        gpio.close()

    def test_read_pin_port_b(self) -> None:
        """Reading from port B pin reads correct register."""
        mock_bus = _create_mock_bus()
        mock_bus.read_byte_data.return_value = 0x10  # Pin 12 (bit 4) high
        gpio = Mcp23017(bus=mock_bus)
        gpio.open()

        result = gpio.read_pin(12)

        assert result is True
        mock_bus.read_byte_data.assert_called_with(0x20, Mcp23017Register.GPIOB)

        gpio.close()

    def test_write_port(self) -> None:
        """Writing to port sets correct register."""
        mock_bus = _create_mock_bus()
        gpio = Mcp23017(bus=mock_bus)
        gpio.open()
        mock_bus.reset_mock()

        gpio.write_port("A", 0xAA)
        mock_bus.write_byte_data.assert_called_with(0x20, Mcp23017Register.OLATA, 0xAA)

        gpio.write_port("B", 0x55)
        mock_bus.write_byte_data.assert_called_with(0x20, Mcp23017Register.OLATB, 0x55)

        gpio.close()

    def test_write_port_invalid_raises(self) -> None:
        """Invalid port raises ValueError."""
        mock_bus = _create_mock_bus()
        gpio = Mcp23017(bus=mock_bus)
        gpio.open()

        with pytest.raises(ValueError, match="port must be 'A' or 'B'"):
            gpio.write_port("C", 0xFF)

        gpio.close()

    def test_read_port(self) -> None:
        """Reading port returns correct value."""
        mock_bus = _create_mock_bus()
        mock_bus.read_byte_data.return_value = 0xAB
        gpio = Mcp23017(bus=mock_bus)
        gpio.open()

        result_a = gpio.read_port("A")
        assert result_a == 0xAB

        result_b = gpio.read_port("B")
        assert result_b == 0xAB

        gpio.close()

    def test_write_all(self) -> None:
        """Writing all pins sets both registers."""
        mock_bus = _create_mock_bus()
        gpio = Mcp23017(bus=mock_bus)
        gpio.open()
        mock_bus.reset_mock()

        gpio.write_all(0xABCD)

        calls = mock_bus.write_byte_data.call_args_list
        assert len(calls) == 2
        # Port A gets low byte
        assert calls[0][0] == (0x20, Mcp23017Register.OLATA, 0xCD)
        # Port B gets high byte
        assert calls[1][0] == (0x20, Mcp23017Register.OLATB, 0xAB)

        gpio.close()

    def test_read_all(self) -> None:
        """Reading all pins returns combined value."""
        mock_bus = _create_mock_bus()
        mock_bus.read_byte_data.side_effect = [0xCD, 0xAB]  # Port A, Port B
        gpio = Mcp23017(bus=mock_bus)
        gpio.open()

        result = gpio.read_all()

        assert result == 0xABCD

        gpio.close()

    def test_set_pullup(self) -> None:
        """Setting pullup modifies correct register."""
        mock_bus = _create_mock_bus()
        mock_bus.read_byte_data.return_value = 0x00
        gpio = Mcp23017(bus=mock_bus)
        gpio.open()
        mock_bus.reset_mock()

        gpio.set_pullup(5, True)

        mock_bus.read_byte_data.assert_called_with(0x20, Mcp23017Register.GPPUA)
        mock_bus.write_byte_data.assert_called_with(0x20, Mcp23017Register.GPPUA, 0x20)

        gpio.close()

    def test_set_port_direction(self) -> None:
        """Setting port direction writes correct register."""
        mock_bus = _create_mock_bus()
        gpio = Mcp23017(bus=mock_bus)
        gpio.open()
        mock_bus.reset_mock()

        gpio.set_port_direction("A", 0x0F)

        mock_bus.write_byte_data.assert_called_with(0x20, Mcp23017Register.IODIRA, 0x0F)

        gpio.close()

    def test_set_all_directions(self) -> None:
        """Setting all directions writes both registers."""
        mock_bus = _create_mock_bus()
        gpio = Mcp23017(bus=mock_bus)
        gpio.open()
        mock_bus.reset_mock()

        gpio.set_all_directions(0x00FF)

        calls = mock_bus.write_byte_data.call_args_list
        assert len(calls) == 2
        assert calls[0][0] == (0x20, Mcp23017Register.IODIRA, 0xFF)
        assert calls[1][0] == (0x20, Mcp23017Register.IODIRB, 0x00)

        gpio.close()
