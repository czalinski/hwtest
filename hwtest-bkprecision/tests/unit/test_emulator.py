"""Tests for the BK Precision DC PSU emulator."""

from __future__ import annotations

import pytest

from hwtest_bkprecision.emulator import (
    BkDcPsuEmulator,
    BkDcPsuEmulatorConfig,
    _normalize_header,
    make_9115_emulator,
    make_9130b_emulator,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _query(emu: BkDcPsuEmulator, cmd: str) -> str:
    """Send a query and return the response."""
    emu.write(cmd)
    return emu.read()


def _command(emu: BkDcPsuEmulator, cmd: str) -> None:
    """Send a command (no response expected)."""
    emu.write(cmd)


def _make_single_channel() -> BkDcPsuEmulator:
    return make_9115_emulator()


def _make_multi_channel() -> BkDcPsuEmulator:
    return make_9130b_emulator()


# ---------------------------------------------------------------------------
# TestBkDcPsuEmulatorConfig
# ---------------------------------------------------------------------------


class TestBkDcPsuEmulatorConfig:
    """Tests for BkDcPsuEmulatorConfig validation."""

    def test_valid_config(self) -> None:
        config = BkDcPsuEmulatorConfig(
            identity="Test PSU", num_channels=1, max_voltage=60.0, max_current=5.0
        )
        assert config.identity == "Test PSU"
        assert config.num_channels == 1
        assert config.max_voltage == 60.0
        assert config.max_current == 5.0

    def test_frozen(self) -> None:
        config = BkDcPsuEmulatorConfig(
            identity="Test", num_channels=1, max_voltage=60.0, max_current=5.0
        )
        with pytest.raises(AttributeError):
            config.identity = "Changed"  # type: ignore[misc]

    def test_empty_identity_raises(self) -> None:
        with pytest.raises(ValueError, match="identity"):
            BkDcPsuEmulatorConfig(identity="", num_channels=1, max_voltage=60.0, max_current=5.0)

    def test_zero_channels_raises(self) -> None:
        with pytest.raises(ValueError, match="num_channels"):
            BkDcPsuEmulatorConfig(
                identity="Test", num_channels=0, max_voltage=60.0, max_current=5.0
            )

    def test_negative_channels_raises(self) -> None:
        with pytest.raises(ValueError, match="num_channels"):
            BkDcPsuEmulatorConfig(
                identity="Test", num_channels=-1, max_voltage=60.0, max_current=5.0
            )

    def test_zero_voltage_raises(self) -> None:
        with pytest.raises(ValueError, match="max_voltage"):
            BkDcPsuEmulatorConfig(identity="Test", num_channels=1, max_voltage=0.0, max_current=5.0)

    def test_negative_voltage_raises(self) -> None:
        with pytest.raises(ValueError, match="max_voltage"):
            BkDcPsuEmulatorConfig(
                identity="Test", num_channels=1, max_voltage=-1.0, max_current=5.0
            )

    def test_zero_current_raises(self) -> None:
        with pytest.raises(ValueError, match="max_current"):
            BkDcPsuEmulatorConfig(
                identity="Test", num_channels=1, max_voltage=60.0, max_current=0.0
            )

    def test_negative_current_raises(self) -> None:
        with pytest.raises(ValueError, match="max_current"):
            BkDcPsuEmulatorConfig(
                identity="Test", num_channels=1, max_voltage=60.0, max_current=-1.0
            )


# ---------------------------------------------------------------------------
# TestIeee4882Commands
# ---------------------------------------------------------------------------


class TestIeee4882Commands:
    """Tests for IEEE 488.2 common commands."""

    def test_idn(self) -> None:
        emu = _make_single_channel()
        assert _query(emu, "*IDN?") == "B&K Precision,9115,SN000001,V1.00-V1.00"

    def test_idn_case_insensitive(self) -> None:
        emu = _make_single_channel()
        assert _query(emu, "*idn?") == "B&K Precision,9115,SN000001,V1.00-V1.00"

    def test_rst_resets_voltage(self) -> None:
        emu = _make_single_channel()
        _command(emu, "VOLT 12.0")
        _command(emu, "*RST")
        assert _query(emu, "VOLT?") == "0.0000"

    def test_rst_resets_output(self) -> None:
        emu = _make_single_channel()
        _command(emu, "OUTP ON")
        _command(emu, "*RST")
        assert _query(emu, "OUTP?") == "0"

    def test_cls_clears_error_queue(self) -> None:
        emu = _make_single_channel()
        _command(emu, "INVALID:CMD")
        _command(emu, "*CLS")
        assert _query(emu, "SYST:ERR?") == '0,"No error"'

    def test_opc(self) -> None:
        emu = _make_single_channel()
        assert _query(emu, "*OPC?") == "1"


# ---------------------------------------------------------------------------
# TestVoltageCommands
# ---------------------------------------------------------------------------


class TestVoltageCommands:
    """Tests for voltage setpoint commands."""

    def test_set_and_query(self) -> None:
        emu = _make_single_channel()
        _command(emu, "VOLT 12.5")
        assert _query(emu, "VOLT?") == "12.5000"

    def test_default_voltage_is_zero(self) -> None:
        emu = _make_single_channel()
        assert _query(emu, "VOLT?") == "0.0000"

    def test_long_form(self) -> None:
        emu = _make_single_channel()
        _command(emu, "SOURCE:VOLTAGE:LEVEL:IMMEDIATE 24.0")
        assert _query(emu, "SOURCE:VOLTAGE:LEVEL:IMMEDIATE?") == "24.0000"

    def test_mixed_form(self) -> None:
        emu = _make_single_channel()
        _command(emu, "SOUR:VOLT 15.0")
        assert _query(emu, "VOLT?") == "15.0000"

    def test_invalid_parameter(self) -> None:
        emu = _make_single_channel()
        _command(emu, "VOLT abc")
        assert _query(emu, "SYST:ERR?") == '-220,"Parameter error"'


# ---------------------------------------------------------------------------
# TestCurrentCommands
# ---------------------------------------------------------------------------


class TestCurrentCommands:
    """Tests for current limit commands."""

    def test_set_and_query(self) -> None:
        emu = _make_single_channel()
        _command(emu, "CURR 2.5")
        assert _query(emu, "CURR?") == "2.5000"

    def test_default_current_is_zero(self) -> None:
        emu = _make_single_channel()
        assert _query(emu, "CURR?") == "0.0000"

    def test_long_form(self) -> None:
        emu = _make_single_channel()
        _command(emu, "SOURCE:CURRENT:LEVEL:IMMEDIATE 3.0")
        assert _query(emu, "SOURCE:CURRENT:LEVEL:IMMEDIATE?") == "3.0000"


# ---------------------------------------------------------------------------
# TestOvpCommands
# ---------------------------------------------------------------------------


class TestOvpCommands:
    """Tests for over-voltage protection commands."""

    def test_default_is_max_voltage(self) -> None:
        emu = _make_single_channel()
        # 9115 max is 60V
        assert _query(emu, "VOLT:PROT?") == "60.0000"

    def test_set_and_query(self) -> None:
        emu = _make_single_channel()
        _command(emu, "VOLT:PROT 55.0")
        assert _query(emu, "VOLT:PROT?") == "55.0000"

    def test_long_form(self) -> None:
        emu = _make_single_channel()
        _command(emu, "SOURCE:VOLTAGE:PROTECTION 45.0")
        assert _query(emu, "SOURCE:VOLTAGE:PROTECTION?") == "45.0000"

    def test_rst_resets_to_max(self) -> None:
        emu = _make_single_channel()
        _command(emu, "VOLT:PROT 30.0")
        _command(emu, "*RST")
        assert _query(emu, "VOLT:PROT?") == "60.0000"


# ---------------------------------------------------------------------------
# TestOutputCommands
# ---------------------------------------------------------------------------


class TestOutputCommands:
    """Tests for output control commands."""

    def test_default_off(self) -> None:
        emu = _make_single_channel()
        assert _query(emu, "OUTP?") == "0"

    def test_on(self) -> None:
        emu = _make_single_channel()
        _command(emu, "OUTP ON")
        assert _query(emu, "OUTP?") == "1"

    def test_off(self) -> None:
        emu = _make_single_channel()
        _command(emu, "OUTP ON")
        _command(emu, "OUTP OFF")
        assert _query(emu, "OUTP?") == "0"

    def test_numeric_on(self) -> None:
        emu = _make_single_channel()
        _command(emu, "OUTP 1")
        assert _query(emu, "OUTP?") == "1"

    def test_numeric_off(self) -> None:
        emu = _make_single_channel()
        _command(emu, "OUTP ON")
        _command(emu, "OUTP 0")
        assert _query(emu, "OUTP?") == "0"

    def test_long_form(self) -> None:
        emu = _make_single_channel()
        _command(emu, "OUTPUT:STATE ON")
        assert _query(emu, "OUTPUT:STATE?") == "1"

    def test_invalid_parameter(self) -> None:
        emu = _make_single_channel()
        _command(emu, "OUTP MAYBE")
        assert _query(emu, "SYST:ERR?") == '-220,"Parameter error"'


# ---------------------------------------------------------------------------
# TestMeasureCommands
# ---------------------------------------------------------------------------


class TestMeasureCommands:
    """Tests for measurement query commands."""

    def test_voltage_output_off(self) -> None:
        emu = _make_single_channel()
        _command(emu, "VOLT 12.0")
        assert _query(emu, "MEAS:VOLT?") == "0.0000"

    def test_voltage_output_on(self) -> None:
        emu = _make_single_channel()
        _command(emu, "VOLT 12.0")
        _command(emu, "OUTP ON")
        assert _query(emu, "MEAS:VOLT?") == "12.0000"

    def test_current_default(self) -> None:
        emu = _make_single_channel()
        assert _query(emu, "MEAS:CURR?") == "0.0000"

    def test_power_output_off(self) -> None:
        emu = _make_single_channel()
        _command(emu, "VOLT 12.0")
        _command(emu, "CURR 2.0")
        assert _query(emu, "MEAS:POW?") == "0.0000"

    def test_power_output_on_no_current_override(self) -> None:
        emu = _make_single_channel()
        _command(emu, "VOLT 12.0")
        _command(emu, "OUTP ON")
        # No current override, so measured current is 0 → power is 0
        assert _query(emu, "MEAS:POW?") == "0.0000"

    def test_power_with_overrides(self) -> None:
        emu = _make_single_channel()
        emu.set_measured_voltage(10.0)
        emu.set_measured_current(2.0)
        assert _query(emu, "MEAS:POW?") == "20.0000"

    def test_voltage_long_form(self) -> None:
        emu = _make_single_channel()
        _command(emu, "VOLT 5.0")
        _command(emu, "OUTP ON")
        assert _query(emu, "MEASURE:SCALAR:VOLTAGE:DC?") == "5.0000"

    def test_current_long_form(self) -> None:
        emu = _make_single_channel()
        emu.set_measured_current(1.5)
        assert _query(emu, "MEASURE:SCALAR:CURRENT:DC?") == "1.5000"


# ---------------------------------------------------------------------------
# TestMeasuredOverrides
# ---------------------------------------------------------------------------


class TestMeasuredOverrides:
    """Tests for test helper measurement overrides."""

    def test_voltage_override(self) -> None:
        emu = _make_single_channel()
        emu.set_measured_voltage(11.5)
        assert _query(emu, "MEAS:VOLT?") == "11.5000"

    def test_current_override(self) -> None:
        emu = _make_single_channel()
        emu.set_measured_current(3.2)
        assert _query(emu, "MEAS:CURR?") == "3.2000"

    def test_overrides_independent_of_output_state(self) -> None:
        emu = _make_single_channel()
        emu.set_measured_voltage(7.5)
        # Output is off but override should still report
        assert _query(emu, "MEAS:VOLT?") == "7.5000"

    def test_rst_clears_overrides(self) -> None:
        emu = _make_single_channel()
        emu.set_measured_voltage(12.0)
        emu.set_measured_current(2.0)
        _command(emu, "*RST")
        assert _query(emu, "MEAS:VOLT?") == "0.0000"
        assert _query(emu, "MEAS:CURR?") == "0.0000"

    def test_override_specific_channel(self) -> None:
        emu = _make_multi_channel()
        emu.set_measured_voltage(5.0, channel=2)
        _command(emu, "INST:NSEL 2")
        assert _query(emu, "MEAS:VOLT?") == "5.0000"
        _command(emu, "INST:NSEL 1")
        assert _query(emu, "MEAS:VOLT?") == "0.0000"


# ---------------------------------------------------------------------------
# TestMultiChannel
# ---------------------------------------------------------------------------


class TestMultiChannel:
    """Tests for multi-channel operations."""

    def test_default_channel_is_1(self) -> None:
        emu = _make_multi_channel()
        assert _query(emu, "INST:NSEL?") == "1"

    def test_select_channel(self) -> None:
        emu = _make_multi_channel()
        _command(emu, "INST:NSEL 2")
        assert _query(emu, "INST:NSEL?") == "2"

    def test_channel_isolation(self) -> None:
        emu = _make_multi_channel()
        _command(emu, "INST:NSEL 1")
        _command(emu, "VOLT 10.0")
        _command(emu, "INST:NSEL 2")
        _command(emu, "VOLT 20.0")

        _command(emu, "INST:NSEL 1")
        assert _query(emu, "VOLT?") == "10.0000"
        _command(emu, "INST:NSEL 2")
        assert _query(emu, "VOLT?") == "20.0000"

    def test_invalid_channel_errors(self) -> None:
        emu = _make_multi_channel()
        _command(emu, "INST:NSEL 4")
        assert _query(emu, "SYST:ERR?") == '-220,"Parameter error"'

    def test_channel_zero_errors(self) -> None:
        emu = _make_multi_channel()
        _command(emu, "INST:NSEL 0")
        assert _query(emu, "SYST:ERR?") == '-220,"Parameter error"'

    def test_apply_command(self) -> None:
        emu = _make_multi_channel()
        _command(emu, "APPL CH2,15.0,1.5")
        _command(emu, "INST:NSEL 2")
        assert _query(emu, "VOLT?") == "15.0000"
        assert _query(emu, "CURR?") == "1.5000"

    def test_apply_query(self) -> None:
        emu = _make_multi_channel()
        _command(emu, "VOLT 12.0")
        _command(emu, "CURR 2.5")
        assert _query(emu, "APPL?") == "12.0000,2.5000"

    def test_apply_does_not_change_selected_channel(self) -> None:
        emu = _make_multi_channel()
        _command(emu, "INST:NSEL 1")
        _command(emu, "APPL CH3,5.0,1.0")
        assert _query(emu, "INST:NSEL?") == "1"

    def test_apply_invalid_channel(self) -> None:
        emu = _make_multi_channel()
        _command(emu, "APPL CH4,5.0,1.0")
        assert _query(emu, "SYST:ERR?") == '-220,"Parameter error"'

    def test_rst_resets_selected_channel(self) -> None:
        emu = _make_multi_channel()
        _command(emu, "INST:NSEL 3")
        _command(emu, "*RST")
        assert _query(emu, "INST:NSEL?") == "1"


# ---------------------------------------------------------------------------
# TestErrorQueue
# ---------------------------------------------------------------------------


class TestErrorQueue:
    """Tests for the SCPI error queue."""

    def test_no_error_default(self) -> None:
        emu = _make_single_channel()
        assert _query(emu, "SYST:ERR?") == '0,"No error"'

    def test_unrecognized_command_queues_error(self) -> None:
        emu = _make_single_channel()
        _command(emu, "BOGUS:CMD 123")
        assert _query(emu, "SYST:ERR?") == '-100,"Command error"'

    def test_unrecognized_query_queues_error(self) -> None:
        emu = _make_single_channel()
        _query(emu, "BOGUS:CMD?")
        assert _query(emu, "SYST:ERR?") == '-100,"Command error"'

    def test_drain_order(self) -> None:
        emu = _make_single_channel()
        _command(emu, "BOGUS1")
        _command(emu, "BOGUS2")
        err1 = _query(emu, "SYST:ERR?")
        err2 = _query(emu, "SYST:ERR?")
        err3 = _query(emu, "SYST:ERR?")
        assert err1 == '-100,"Command error"'
        assert err2 == '-100,"Command error"'
        assert err3 == '0,"No error"'

    def test_cls_clears_errors(self) -> None:
        emu = _make_single_channel()
        _command(emu, "BOGUS")
        _command(emu, "*CLS")
        assert _query(emu, "SYST:ERR?") == '0,"No error"'

    def test_system_error_long_form(self) -> None:
        emu = _make_single_channel()
        _command(emu, "BOGUS")
        assert _query(emu, "SYSTEM:ERROR?") == '-100,"Command error"'


# ---------------------------------------------------------------------------
# TestNormalization
# ---------------------------------------------------------------------------


class TestNormalization:
    """Tests for SCPI command normalization."""

    def test_short_form_unchanged(self) -> None:
        assert _normalize_header("VOLT") == "VOLT"

    def test_long_form_converted(self) -> None:
        assert _normalize_header("VOLTAGE") == "VOLT"

    def test_source_stripped(self) -> None:
        assert _normalize_header("SOURCE:VOLTAGE") == "VOLT"

    def test_level_immediate_stripped(self) -> None:
        assert _normalize_header("VOLTAGE:LEVEL:IMMEDIATE") == "VOLT"

    def test_full_long_form(self) -> None:
        assert _normalize_header("SOURCE:VOLTAGE:LEVEL:IMMEDIATE") == "VOLT"

    def test_measure_scalar_dc(self) -> None:
        assert _normalize_header("MEASURE:SCALAR:VOLTAGE:DC") == "MEAS:VOLT"

    def test_output_state(self) -> None:
        assert _normalize_header("OUTPUT:STATE") == "OUTP"

    def test_leading_colon_stripped(self) -> None:
        assert _normalize_header(":VOLTAGE") == "VOLT"

    def test_case_insensitive(self) -> None:
        assert _normalize_header("voltage") == "VOLT"
        assert _normalize_header("Source:Voltage") == "VOLT"

    def test_instrument_select(self) -> None:
        assert _normalize_header("INST:NSEL") == "INST:NSEL"

    def test_voltage_protection(self) -> None:
        assert _normalize_header("SOURCE:VOLTAGE:PROTECTION") == "VOLT:PROT"

    def test_measure_current(self) -> None:
        assert _normalize_header("MEASURE:CURRENT") == "MEAS:CURR"


# ---------------------------------------------------------------------------
# TestFactoryFunctions
# ---------------------------------------------------------------------------


class TestFactoryFunctions:
    """Tests for emulator factory functions."""

    def test_make_9115(self) -> None:
        emu = make_9115_emulator()
        idn = _query(emu, "*IDN?")
        assert "9115" in idn
        assert "SN000001" in idn
        assert "B&K Precision" in idn

    def test_make_9115_custom_serial(self) -> None:
        emu = make_9115_emulator(serial="ABC123")
        idn = _query(emu, "*IDN?")
        assert "ABC123" in idn

    def test_make_9115_single_channel(self) -> None:
        emu = make_9115_emulator()
        assert _query(emu, "INST:NSEL?") == "1"
        # Trying channel 2 should error
        _command(emu, "INST:NSEL 2")
        assert _query(emu, "SYST:ERR?") == '-220,"Parameter error"'

    def test_make_9115_max_voltage(self) -> None:
        emu = make_9115_emulator()
        assert _query(emu, "VOLT:PROT?") == "60.0000"

    def test_make_9130b(self) -> None:
        emu = make_9130b_emulator()
        idn = _query(emu, "*IDN?")
        assert "9130B" in idn
        assert "SN000001" in idn

    def test_make_9130b_custom_serial(self) -> None:
        emu = make_9130b_emulator(serial="XYZ789")
        idn = _query(emu, "*IDN?")
        assert "XYZ789" in idn

    def test_make_9130b_three_channels(self) -> None:
        emu = make_9130b_emulator()
        _command(emu, "INST:NSEL 3")
        assert _query(emu, "INST:NSEL?") == "3"

    def test_make_9130b_max_voltage(self) -> None:
        emu = make_9130b_emulator()
        assert _query(emu, "VOLT:PROT?") == "30.0000"

    def test_close(self) -> None:
        emu = make_9115_emulator()
        emu.close()
        # Should not raise — emulator is simple
