"""Integration tests for BkDcPsu through ScpiConnection and emulator."""

from __future__ import annotations

import pytest

from hwtest_bkprecision.emulator import make_9115_emulator, make_9130b_emulator
from hwtest_bkprecision.psu import BkDcPsu
from hwtest_scpi import ScpiConnection


def _make_psu() -> tuple[BkDcPsu, ScpiConnection]:
    """Create a PSU driver backed by a 9115 emulator."""
    emu = make_9115_emulator()
    conn = ScpiConnection(emu)
    psu = BkDcPsu(conn)
    return psu, conn


def _make_multi_psu() -> tuple[BkDcPsu, ScpiConnection]:
    """Create a PSU driver backed by a 9130B emulator."""
    emu = make_9130b_emulator()
    conn = ScpiConnection(emu)
    psu = BkDcPsu(conn)
    return psu, conn


class TestIdentify:
    """Tests for identification and lifecycle."""

    def test_identify(self) -> None:
        psu, _ = _make_psu()
        idn = psu.identify()
        assert "9115" in idn
        assert "B&K Precision" in idn

    def test_get_identity(self) -> None:
        psu, _ = _make_psu()
        identity = psu.get_identity()
        assert identity.manufacturer == "B&K Precision"
        assert identity.model == "9115"
        assert identity.serial == "SN000001"
        assert identity.firmware == "V1.00-V1.00"

    def test_get_identity_9130b(self) -> None:
        psu, _ = _make_multi_psu()
        identity = psu.get_identity()
        assert identity.manufacturer == "B&K Precision"
        assert identity.model == "9130B"

    def test_reset(self) -> None:
        psu, _ = _make_psu()
        psu.set_voltage(12.0)
        psu.reset()
        assert psu.get_voltage() == pytest.approx(0.0)

    def test_close(self) -> None:
        psu, conn = _make_psu()
        psu.close()
        # Connection should be closed — transport is closed


class TestVoltage:
    """Tests for voltage commands."""

    def test_set_and_get(self) -> None:
        psu, _ = _make_psu()
        psu.set_voltage(24.5)
        assert psu.get_voltage() == pytest.approx(24.5)

    def test_measure_output_off(self) -> None:
        psu, _ = _make_psu()
        psu.set_voltage(12.0)
        assert psu.measure_voltage() == pytest.approx(0.0)

    def test_measure_output_on(self) -> None:
        psu, _ = _make_psu()
        psu.set_voltage(12.0)
        psu.enable_output()
        assert psu.measure_voltage() == pytest.approx(12.0)


class TestCurrent:
    """Tests for current commands."""

    def test_set_and_get(self) -> None:
        psu, _ = _make_psu()
        psu.set_current(3.5)
        assert psu.get_current() == pytest.approx(3.5)

    def test_measure_default(self) -> None:
        psu, _ = _make_psu()
        assert psu.measure_current() == pytest.approx(0.0)


class TestPower:
    """Tests for power measurement."""

    def test_measure_output_off(self) -> None:
        psu, _ = _make_psu()
        psu.set_voltage(12.0)
        assert psu.measure_power() == pytest.approx(0.0)

    def test_measure_with_overrides(self) -> None:
        emu = make_9115_emulator()
        conn = ScpiConnection(emu)
        psu = BkDcPsu(conn)
        emu.set_measured_voltage(10.0)
        emu.set_measured_current(2.0)
        assert psu.measure_power() == pytest.approx(20.0)


class TestOutput:
    """Tests for output control."""

    def test_default_disabled(self) -> None:
        psu, _ = _make_psu()
        assert psu.is_output_enabled() is False

    def test_enable(self) -> None:
        psu, _ = _make_psu()
        psu.enable_output()
        assert psu.is_output_enabled() is True

    def test_disable(self) -> None:
        psu, _ = _make_psu()
        psu.enable_output()
        psu.disable_output()
        assert psu.is_output_enabled() is False


class TestOvp:
    """Tests for over-voltage protection."""

    def test_default_is_max(self) -> None:
        psu, _ = _make_psu()
        assert psu.get_ovp() == pytest.approx(60.0)

    def test_set_and_get(self) -> None:
        psu, _ = _make_psu()
        psu.set_ovp(45.0)
        assert psu.get_ovp() == pytest.approx(45.0)


class TestChannel:
    """Tests for channel selection (multi-channel)."""

    def test_default_channel(self) -> None:
        psu, _ = _make_multi_psu()
        assert psu.get_selected_channel() == 1

    def test_select_channel(self) -> None:
        psu, _ = _make_multi_psu()
        psu.select_channel(2)
        assert psu.get_selected_channel() == 2

    def test_select_channel_3(self) -> None:
        psu, _ = _make_multi_psu()
        psu.select_channel(3)
        assert psu.get_selected_channel() == 3


class TestApply:
    """Tests for apply command (multi-channel convenience)."""

    def test_apply_sets_values(self) -> None:
        psu, _ = _make_multi_psu()
        psu.apply(2, 15.0, 1.5)
        psu.select_channel(2)
        assert psu.get_voltage() == pytest.approx(15.0)
        assert psu.get_current() == pytest.approx(1.5)

    def test_get_apply(self) -> None:
        psu, _ = _make_multi_psu()
        psu.set_voltage(12.0)
        psu.set_current(2.5)
        v, i = psu.get_apply()
        assert v == pytest.approx(12.0)
        assert i == pytest.approx(2.5)
