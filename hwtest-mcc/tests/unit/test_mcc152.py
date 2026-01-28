"""Unit tests for MCC 152 digital I/O and analog output driver."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from hwtest_core.errors import HwtestError

from hwtest_mcc.mcc152 import (
    DioDirection,
    Mcc152AnalogChannel,
    Mcc152Config,
    Mcc152DioChannel,
    Mcc152Instrument,
    create_instrument,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_daqhats() -> tuple[MagicMock, MagicMock]:
    """Return (mock_module, mock_hat) for patching daqhats."""
    mock_module = MagicMock()
    mock_hat = MagicMock()
    mock_hat.serial.return_value = "0123456789"
    mock_module.mcc152.return_value = mock_hat
    return mock_module, mock_hat


# ---------------------------------------------------------------------------
# DioDirection tests
# ---------------------------------------------------------------------------


class TestDioDirection:
    def test_values(self) -> None:
        assert DioDirection.INPUT.value == 0
        assert DioDirection.OUTPUT.value == 1


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestMcc152DioChannel:
    def test_create(self) -> None:
        ch = Mcc152DioChannel(id=0, name="relay1", direction=DioDirection.OUTPUT)
        assert ch.id == 0
        assert ch.name == "relay1"
        assert ch.direction == DioDirection.OUTPUT
        assert ch.initial_value is False

    def test_with_initial_value(self) -> None:
        ch = Mcc152DioChannel(
            id=1, name="relay2", direction=DioDirection.OUTPUT, initial_value=True
        )
        assert ch.initial_value is True

    def test_frozen(self) -> None:
        ch = Mcc152DioChannel(id=0, name="relay1", direction=DioDirection.OUTPUT)
        with pytest.raises(AttributeError):
            ch.id = 1  # type: ignore[misc]


class TestMcc152AnalogChannel:
    def test_create(self) -> None:
        ch = Mcc152AnalogChannel(id=0, name="dac1")
        assert ch.id == 0
        assert ch.name == "dac1"
        assert ch.initial_voltage == 0.0

    def test_with_initial_voltage(self) -> None:
        ch = Mcc152AnalogChannel(id=1, name="dac2", initial_voltage=2.5)
        assert ch.initial_voltage == 2.5

    def test_frozen(self) -> None:
        ch = Mcc152AnalogChannel(id=0, name="dac1")
        with pytest.raises(AttributeError):
            ch.id = 1  # type: ignore[misc]


class TestMcc152Config:
    def test_valid_dio_only(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(Mcc152DioChannel(0, "relay", DioDirection.OUTPUT),),
            analog_channels=(),
            source_id="dio01",
        )
        assert config.address == 0
        assert len(config.dio_channels) == 1
        assert len(config.analog_channels) == 0
        assert config.source_id == "dio01"

    def test_valid_analog_only(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(Mcc152AnalogChannel(0, "dac1"),),
            source_id="dac01",
        )
        assert len(config.dio_channels) == 0
        assert len(config.analog_channels) == 1

    def test_valid_mixed(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(
                Mcc152DioChannel(0, "relay", DioDirection.OUTPUT),
                Mcc152DioChannel(1, "sensor", DioDirection.INPUT),
            ),
            analog_channels=(
                Mcc152AnalogChannel(0, "dac1"),
                Mcc152AnalogChannel(1, "dac2"),
            ),
            source_id="mixed01",
        )
        assert len(config.dio_channels) == 2
        assert len(config.analog_channels) == 2

    def test_address_too_low(self) -> None:
        with pytest.raises(ValueError, match="address"):
            Mcc152Config(
                address=-1,
                dio_channels=(),
                analog_channels=(),
                source_id="dio01",
            )

    def test_address_too_high(self) -> None:
        with pytest.raises(ValueError, match="address"):
            Mcc152Config(
                address=8,
                dio_channels=(),
                analog_channels=(),
                source_id="dio01",
            )

    def test_dio_channel_id_too_low(self) -> None:
        with pytest.raises(ValueError, match="DIO channel id"):
            Mcc152Config(
                address=0,
                dio_channels=(Mcc152DioChannel(-1, "ch0", DioDirection.OUTPUT),),
                analog_channels=(),
                source_id="dio01",
            )

    def test_dio_channel_id_too_high(self) -> None:
        with pytest.raises(ValueError, match="DIO channel id"):
            Mcc152Config(
                address=0,
                dio_channels=(Mcc152DioChannel(8, "ch0", DioDirection.OUTPUT),),
                analog_channels=(),
                source_id="dio01",
            )

    def test_duplicate_dio_channel_id(self) -> None:
        with pytest.raises(ValueError, match="duplicate DIO channel id"):
            Mcc152Config(
                address=0,
                dio_channels=(
                    Mcc152DioChannel(0, "ch0", DioDirection.OUTPUT),
                    Mcc152DioChannel(0, "ch1", DioDirection.INPUT),
                ),
                analog_channels=(),
                source_id="dio01",
            )

    def test_analog_channel_id_too_low(self) -> None:
        with pytest.raises(ValueError, match="analog channel id"):
            Mcc152Config(
                address=0,
                dio_channels=(),
                analog_channels=(Mcc152AnalogChannel(-1, "dac"),),
                source_id="dac01",
            )

    def test_analog_channel_id_too_high(self) -> None:
        with pytest.raises(ValueError, match="analog channel id"):
            Mcc152Config(
                address=0,
                dio_channels=(),
                analog_channels=(Mcc152AnalogChannel(2, "dac"),),
                source_id="dac01",
            )

    def test_duplicate_analog_channel_id(self) -> None:
        with pytest.raises(ValueError, match="duplicate analog channel id"):
            Mcc152Config(
                address=0,
                dio_channels=(),
                analog_channels=(
                    Mcc152AnalogChannel(0, "dac1"),
                    Mcc152AnalogChannel(0, "dac2"),
                ),
                source_id="dac01",
            )

    def test_duplicate_name_across_dio_and_analog(self) -> None:
        with pytest.raises(ValueError, match="duplicate channel name"):
            Mcc152Config(
                address=0,
                dio_channels=(Mcc152DioChannel(0, "same", DioDirection.OUTPUT),),
                analog_channels=(Mcc152AnalogChannel(0, "same"),),
                source_id="mixed01",
            )

    def test_initial_voltage_too_low(self) -> None:
        with pytest.raises(ValueError, match="initial_voltage"):
            Mcc152Config(
                address=0,
                dio_channels=(),
                analog_channels=(Mcc152AnalogChannel(0, "dac", initial_voltage=-0.1),),
                source_id="dac01",
            )

    def test_initial_voltage_too_high(self) -> None:
        with pytest.raises(ValueError, match="initial_voltage"):
            Mcc152Config(
                address=0,
                dio_channels=(),
                analog_channels=(Mcc152AnalogChannel(0, "dac", initial_voltage=5.1),),
                source_id="dac01",
            )


# ---------------------------------------------------------------------------
# Instrument lifecycle
# ---------------------------------------------------------------------------


class TestMcc152Instrument:
    def test_initial_state(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(Mcc152DioChannel(0, "relay", DioDirection.OUTPUT),),
            analog_channels=(),
            source_id="dio01",
        )
        instrument = Mcc152Instrument(config)
        assert not instrument.is_open

    def test_open_close(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(Mcc152DioChannel(0, "relay", DioDirection.OUTPUT),),
            analog_channels=(Mcc152AnalogChannel(0, "dac1"),),
            source_id="dio01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            assert instrument.is_open

            mock_module.mcc152.assert_called_once_with(0)
            mock_hat.dio_config_write_bit.assert_called_once_with(
                0, DioDirection.OUTPUT.value
            )
            mock_hat.dio_output_write_bit.assert_called_once_with(0, 0)
            mock_hat.a_out_write.assert_called_once_with(0, 0.0)

            instrument.close()
            assert not instrument.is_open

    def test_open_is_idempotent(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(),
            source_id="dio01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            instrument.open()  # second call should be a no-op

        mock_module.mcc152.assert_called_once()

    def test_close_when_not_open(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(),
            source_id="dio01",
        )
        instrument = Mcc152Instrument(config)
        instrument.close()  # should not raise

    def test_daqhats_not_installed(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(),
            source_id="dio01",
        )
        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": None}):
            with pytest.raises(HwtestError, match="daqhats"):
                instrument.open()

    def test_hat_not_found(self) -> None:
        config = Mcc152Config(
            address=5,
            dio_channels=(),
            analog_channels=(),
            source_id="dio01",
        )
        mock_module = MagicMock()
        mock_module.mcc152.side_effect = Exception("No HAT at address 5")

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            with pytest.raises(HwtestError, match="Failed to open MCC 152"):
                instrument.open()

    def test_initial_output_value(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(
                Mcc152DioChannel(0, "relay", DioDirection.OUTPUT, initial_value=True),
            ),
            analog_channels=(),
            source_id="dio01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()

        mock_hat.dio_output_write_bit.assert_called_once_with(0, 1)

    def test_initial_analog_voltage(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(Mcc152AnalogChannel(0, "dac", initial_voltage=2.5),),
            source_id="dac01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()

        mock_hat.a_out_write.assert_called_once_with(0, 2.5)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_get_identity(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(),
            source_id="dio01",
        )
        mock_module, mock_hat = _make_mock_daqhats()
        mock_hat.serial.return_value = "DEF456"

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            identity = instrument.get_identity()
            instrument.close()

        assert identity.manufacturer == "Measurement Computing"
        assert identity.model == "MCC 152"
        assert identity.serial == "DEF456"
        assert identity.firmware == ""

    def test_get_identity_before_open(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(),
            source_id="dio01",
        )
        instrument = Mcc152Instrument(config)

        with pytest.raises(HwtestError, match="HAT not opened"):
            instrument.get_identity()


# ---------------------------------------------------------------------------
# Digital I/O operations
# ---------------------------------------------------------------------------


class TestDioOperations:
    def test_dio_read_by_id(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(Mcc152DioChannel(0, "input1", DioDirection.INPUT),),
            analog_channels=(),
            source_id="dio01",
        )
        mock_module, mock_hat = _make_mock_daqhats()
        mock_hat.dio_input_read_bit.return_value = 1

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            value = instrument.dio_read(0)

        assert value is True
        mock_hat.dio_input_read_bit.assert_called_once_with(0)

    def test_dio_read_by_name(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(Mcc152DioChannel(3, "sensor", DioDirection.INPUT),),
            analog_channels=(),
            source_id="dio01",
        )
        mock_module, mock_hat = _make_mock_daqhats()
        mock_hat.dio_input_read_bit.return_value = 0

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            value = instrument.dio_read("sensor")

        assert value is False
        mock_hat.dio_input_read_bit.assert_called_once_with(3)

    def test_dio_read_invalid_name(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(),
            source_id="dio01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            with pytest.raises(HwtestError, match="Unknown DIO channel"):
                instrument.dio_read("nonexistent")

    def test_dio_read_invalid_id(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(),
            source_id="dio01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            with pytest.raises(HwtestError, match="DIO channel must be 0-7"):
                instrument.dio_read(8)

    def test_dio_write_by_id(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(Mcc152DioChannel(0, "relay", DioDirection.OUTPUT),),
            analog_channels=(),
            source_id="dio01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            mock_hat.dio_output_write_bit.reset_mock()
            instrument.dio_write(0, True)

        mock_hat.dio_output_write_bit.assert_called_once_with(0, 1)

    def test_dio_write_by_name(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(Mcc152DioChannel(5, "relay", DioDirection.OUTPUT),),
            analog_channels=(),
            source_id="dio01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            mock_hat.dio_output_write_bit.reset_mock()
            instrument.dio_write("relay", False)

        mock_hat.dio_output_write_bit.assert_called_once_with(5, 0)

    def test_dio_read_all(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(),
            source_id="dio01",
        )
        mock_module, mock_hat = _make_mock_daqhats()
        mock_hat.dio_input_read_port.return_value = 0b10100101

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            value = instrument.dio_read_all()

        assert value == 0b10100101

    def test_dio_write_all(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(),
            source_id="dio01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            instrument.dio_write_all(0b11110000)

        mock_hat.dio_output_write_port.assert_called_once_with(0b11110000)

    def test_dio_operations_require_open(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(Mcc152DioChannel(0, "relay", DioDirection.OUTPUT),),
            analog_channels=(),
            source_id="dio01",
        )
        instrument = Mcc152Instrument(config)

        with pytest.raises(HwtestError, match="HAT not opened"):
            instrument.dio_read(0)

        with pytest.raises(HwtestError, match="HAT not opened"):
            instrument.dio_write(0, True)


# ---------------------------------------------------------------------------
# Analog output operations
# ---------------------------------------------------------------------------


class TestAnalogOperations:
    def test_analog_write_by_id(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(Mcc152AnalogChannel(0, "dac1"),),
            source_id="dac01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            mock_hat.a_out_write.reset_mock()
            instrument.analog_write(0, 3.3)

        mock_hat.a_out_write.assert_called_once_with(0, 3.3)

    def test_analog_write_by_name(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(Mcc152AnalogChannel(1, "control"),),
            source_id="dac01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            mock_hat.a_out_write.reset_mock()
            instrument.analog_write("control", 2.5)

        mock_hat.a_out_write.assert_called_once_with(1, 2.5)

    def test_analog_write_invalid_name(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(),
            source_id="dac01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            with pytest.raises(HwtestError, match="Unknown analog channel"):
                instrument.analog_write("nonexistent", 1.0)

    def test_analog_write_invalid_id(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(),
            source_id="dac01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            with pytest.raises(HwtestError, match="Analog channel must be 0-1"):
                instrument.analog_write(2, 1.0)

    def test_analog_write_voltage_too_low(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(Mcc152AnalogChannel(0, "dac"),),
            source_id="dac01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            with pytest.raises(HwtestError, match="Voltage must be 0-5V"):
                instrument.analog_write(0, -0.1)

    def test_analog_write_voltage_too_high(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(Mcc152AnalogChannel(0, "dac"),),
            source_id="dac01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            with pytest.raises(HwtestError, match="Voltage must be 0-5V"):
                instrument.analog_write(0, 5.1)

    def test_analog_write_all(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(
                Mcc152AnalogChannel(0, "dac1"),
                Mcc152AnalogChannel(1, "dac2"),
            ),
            source_id="dac01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        instrument = Mcc152Instrument(config)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            instrument.open()
            instrument.analog_write_all((1.5, 3.5))

        mock_hat.a_out_write_all.assert_called_once_with(1.5, 3.5)

    def test_analog_operations_require_open(self) -> None:
        config = Mcc152Config(
            address=0,
            dio_channels=(),
            analog_channels=(Mcc152AnalogChannel(0, "dac"),),
            source_id="dac01",
        )
        instrument = Mcc152Instrument(config)

        with pytest.raises(HwtestError, match="HAT not opened"):
            instrument.analog_write(0, 2.5)


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


class TestCreateInstrument:
    def test_create_with_dio(self) -> None:
        instrument = create_instrument(
            address=0,
            source_id="dio01",
            dio_channels=[
                {"id": 0, "name": "relay1", "direction": "OUTPUT"},
                {"id": 1, "name": "sensor1", "direction": "INPUT"},
            ],
        )

        assert instrument._config.address == 0
        assert len(instrument._config.dio_channels) == 2
        assert instrument._config.dio_channels[0].direction == DioDirection.OUTPUT
        assert instrument._config.dio_channels[1].direction == DioDirection.INPUT

    def test_create_with_analog(self) -> None:
        instrument = create_instrument(
            address=0,
            source_id="dac01",
            analog_channels=[
                {"id": 0, "name": "dac1", "initial_voltage": 1.0},
                {"id": 1, "name": "dac2"},
            ],
        )

        assert len(instrument._config.analog_channels) == 2
        assert instrument._config.analog_channels[0].initial_voltage == 1.0
        assert instrument._config.analog_channels[1].initial_voltage == 0.0

    def test_create_with_defaults(self) -> None:
        instrument = create_instrument(
            address=0,
            source_id="mixed01",
            dio_channels=[{"id": 0, "name": "ch0"}],
        )

        assert instrument._config.dio_channels[0].direction == DioDirection.INPUT
        assert instrument._config.dio_channels[0].initial_value is False

    def test_create_empty(self) -> None:
        instrument = create_instrument(
            address=0,
            source_id="empty01",
        )

        assert len(instrument._config.dio_channels) == 0
        assert len(instrument._config.analog_channels) == 0
