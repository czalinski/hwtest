"""Unit tests for the CAN interface."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hwtest_sim_pi4_waveshare.can_interface import CanConfig, CanInterface, CanMessage


def _create_mock_bus() -> MagicMock:
    """Create a mock CAN bus."""
    mock = MagicMock()
    mock.recv.return_value = None
    return mock


class TestCanMessage:
    """Tests for CanMessage."""

    def test_default_values(self) -> None:
        """Default values are correct."""
        msg = CanMessage(arbitration_id=0x123)
        assert msg.arbitration_id == 0x123
        assert msg.data == b""
        assert msg.is_extended_id is False
        assert msg.is_fd is False
        assert msg.bitrate_switch is False
        assert msg.timestamp == 0.0

    def test_with_data_bytes(self) -> None:
        """Message with bytes data."""
        msg = CanMessage(arbitration_id=0x100, data=b"\x01\x02\x03")
        assert msg.data == b"\x01\x02\x03"

    def test_with_data_list(self) -> None:
        """Message with list data converts to bytes."""
        msg = CanMessage(arbitration_id=0x100, data=[1, 2, 3])
        assert msg.data == b"\x01\x02\x03"

    def test_with_data_tuple(self) -> None:
        """Message with tuple data converts to bytes."""
        msg = CanMessage(arbitration_id=0x100, data=(4, 5, 6))
        assert msg.data == b"\x04\x05\x06"

    def test_can_data_too_long_raises(self) -> None:
        """Data longer than 8 bytes for standard CAN raises ValueError."""
        with pytest.raises(ValueError, match="data length must be <= 8"):
            CanMessage(arbitration_id=0x100, data=b"\x00" * 9)

    def test_can_fd_allows_64_bytes(self) -> None:
        """CAN FD allows up to 64 bytes."""
        msg = CanMessage(arbitration_id=0x100, data=b"\x00" * 64, is_fd=True)
        assert len(msg.data) == 64

    def test_can_fd_data_too_long_raises(self) -> None:
        """Data longer than 64 bytes for CAN FD raises ValueError."""
        with pytest.raises(ValueError, match="data length must be <= 64"):
            CanMessage(arbitration_id=0x100, data=b"\x00" * 65, is_fd=True)


class TestCanConfig:
    """Tests for CanConfig."""

    def test_default_config(self) -> None:
        """Default config uses expected values."""
        config = CanConfig()
        assert config.interface == "can0"
        assert config.bitrate == 500000
        assert config.fd is False
        assert config.data_bitrate == 2000000

    def test_custom_config(self) -> None:
        """Custom config values are stored correctly."""
        config = CanConfig(
            interface="can1",
            bitrate=1000000,
            fd=True,
            data_bitrate=4000000,
        )
        assert config.interface == "can1"
        assert config.bitrate == 1000000
        assert config.fd is True
        assert config.data_bitrate == 4000000


class TestCanInterface:
    """Tests for CanInterface."""

    def test_not_open_initially(self) -> None:
        """Interface is not open when created."""
        can = CanInterface()
        assert not can.is_open

    def test_open_and_close(self) -> None:
        """Interface can be opened and closed."""
        mock_bus = _create_mock_bus()
        can = CanInterface(bus=mock_bus)

        can.open()
        assert can.is_open

        can.close()
        assert not can.is_open
        mock_bus.shutdown.assert_called()

    def test_double_open_raises(self) -> None:
        """Opening an already open interface raises RuntimeError."""
        mock_bus = _create_mock_bus()
        can = CanInterface(bus=mock_bus)
        can.open()

        with pytest.raises(RuntimeError, match="already open"):
            can.open()

        can.close()

    def test_send_when_closed_raises(self) -> None:
        """Sending when closed raises RuntimeError."""
        can = CanInterface()
        msg = CanMessage(arbitration_id=0x100)
        with pytest.raises(RuntimeError, match="not open"):
            can.send(msg)

    def test_receive_when_closed_raises(self) -> None:
        """Receiving when closed raises RuntimeError."""
        can = CanInterface()
        with pytest.raises(RuntimeError, match="not open"):
            can.receive()

    def test_receive_timeout(self) -> None:
        """Receive returns None on timeout."""
        mock_bus = _create_mock_bus()
        mock_bus.recv.return_value = None
        can = CanInterface(bus=mock_bus)
        can.open()

        result = can.receive(timeout=0.1)

        assert result is None
        mock_bus.recv.assert_called_with(timeout=0.1)

        can.close()

    def test_receive_message(self) -> None:
        """Receive returns CanMessage on success."""
        mock_bus = _create_mock_bus()
        mock_msg = MagicMock()
        mock_msg.arbitration_id = 0x123
        mock_msg.data = b"\x01\x02"
        mock_msg.is_extended_id = False
        mock_msg.is_fd = False
        mock_msg.bitrate_switch = False
        mock_msg.timestamp = 12345.0
        mock_bus.recv.return_value = mock_msg
        can = CanInterface(bus=mock_bus)
        can.open()

        result = can.receive()

        assert result is not None
        assert result.arbitration_id == 0x123
        assert result.data == b"\x01\x02"
        assert result.timestamp == 12345.0

        can.close()

    def test_send_data(self) -> None:
        """send_data creates and sends message."""
        mock_bus = _create_mock_bus()
        can = CanInterface(bus=mock_bus)
        can.open()

        can.send_data(0x200, [0xAA, 0xBB])

        mock_bus.send.assert_called_once()

        can.close()

    def test_add_and_remove_callback(self) -> None:
        """Callbacks can be added and removed."""
        mock_bus = _create_mock_bus()
        can = CanInterface(bus=mock_bus)

        callback = MagicMock()
        can.add_callback(callback)
        can.remove_callback(callback)

        # Removing non-existent callback is a no-op
        can.remove_callback(callback)

    def test_config_property(self) -> None:
        """Config property returns the configuration."""
        config = CanConfig(interface="vcan0", bitrate=250000)
        can = CanInterface(config=config)
        assert can.config.interface == "vcan0"
        assert can.config.bitrate == 250000
