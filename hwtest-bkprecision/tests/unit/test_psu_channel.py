"""Unit tests for BK Precision PSU channel wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hwtest_bkprecision.psu_channel import (
    BkDcPsuChannel,
    BkMultiChannelPsu,
    PsuChannelConfig,
    create_multichannel_instrument,
)


class TestPsuChannelConfig:
    """Tests for PsuChannelConfig dataclass."""

    def test_create_minimal(self) -> None:
        """Test creating a PsuChannelConfig with minimal args."""
        config = PsuChannelConfig(id=1, logical_name="main_battery")
        assert config.id == 1
        assert config.logical_name == "main_battery"
        assert config.max_voltage is None
        assert config.max_current is None

    def test_create_with_limits(self) -> None:
        """Test creating a PsuChannelConfig with limits."""
        config = PsuChannelConfig(
            id=2,
            logical_name="cpu_power",
            max_voltage=15.0,
            max_current=10.0,
        )
        assert config.id == 2
        assert config.logical_name == "cpu_power"
        assert config.max_voltage == 15.0
        assert config.max_current == 10.0

    def test_frozen(self) -> None:
        """Test that PsuChannelConfig is immutable."""
        config = PsuChannelConfig(id=1, logical_name="test")
        with pytest.raises(AttributeError):
            config.logical_name = "new_name"  # type: ignore[misc]


class TestBkDcPsuChannel:
    """Tests for BkDcPsuChannel."""

    def create_mock_psu(self) -> MagicMock:
        """Create a mock BkDcPsu."""
        psu = MagicMock()
        psu.get_voltage.return_value = 12.0
        psu.get_current.return_value = 1.5
        psu.is_output_enabled.return_value = True
        psu.measure_voltage.return_value = 11.98
        psu.measure_current.return_value = 1.48
        psu.measure_power.return_value = 17.73
        return psu

    def test_properties(self) -> None:
        """Test channel properties."""
        psu = self.create_mock_psu()
        config = PsuChannelConfig(id=1, logical_name="main_battery")
        import threading

        lock = threading.Lock()

        channel = BkDcPsuChannel(psu, config, lock)

        assert channel.logical_name == "main_battery"
        assert channel.channel_id == 1

    def test_set_voltage(self) -> None:
        """Test setting voltage."""
        psu = self.create_mock_psu()
        config = PsuChannelConfig(id=1, logical_name="main_battery")
        import threading

        lock = threading.Lock()

        channel = BkDcPsuChannel(psu, config, lock)
        channel.set_voltage(12.0)

        psu.select_channel.assert_called_with(1)
        psu.set_voltage.assert_called_with(12.0)

    def test_set_voltage_exceeds_limit_raises(self) -> None:
        """Test that setting voltage above limit raises ValueError."""
        psu = self.create_mock_psu()
        config = PsuChannelConfig(id=1, logical_name="main_battery", max_voltage=15.0)
        import threading

        lock = threading.Lock()

        channel = BkDcPsuChannel(psu, config, lock)

        with pytest.raises(ValueError, match="exceeds limit"):
            channel.set_voltage(20.0)

    def test_set_current_limit(self) -> None:
        """Test setting current limit."""
        psu = self.create_mock_psu()
        config = PsuChannelConfig(id=1, logical_name="main_battery")
        import threading

        lock = threading.Lock()

        channel = BkDcPsuChannel(psu, config, lock)
        channel.set_current_limit(5.0)

        psu.select_channel.assert_called_with(1)
        psu.set_current.assert_called_with(5.0)

    def test_set_current_exceeds_limit_raises(self) -> None:
        """Test that setting current above limit raises ValueError."""
        psu = self.create_mock_psu()
        config = PsuChannelConfig(id=1, logical_name="main_battery", max_current=10.0)
        import threading

        lock = threading.Lock()

        channel = BkDcPsuChannel(psu, config, lock)

        with pytest.raises(ValueError, match="exceeds limit"):
            channel.set_current_limit(15.0)

    def test_set_output_enable(self) -> None:
        """Test enabling output."""
        psu = self.create_mock_psu()
        config = PsuChannelConfig(id=1, logical_name="main_battery")
        import threading

        lock = threading.Lock()

        channel = BkDcPsuChannel(psu, config, lock)
        channel.set_output(True)

        psu.select_channel.assert_called_with(1)
        psu.enable_output.assert_called_once()

    def test_set_output_disable(self) -> None:
        """Test disabling output."""
        psu = self.create_mock_psu()
        config = PsuChannelConfig(id=1, logical_name="main_battery")
        import threading

        lock = threading.Lock()

        channel = BkDcPsuChannel(psu, config, lock)
        channel.set_output(False)

        psu.select_channel.assert_called_with(1)
        psu.disable_output.assert_called_once()

    def test_apply(self) -> None:
        """Test applying voltage and current together."""
        psu = self.create_mock_psu()
        config = PsuChannelConfig(id=1, logical_name="main_battery")
        import threading

        lock = threading.Lock()

        channel = BkDcPsuChannel(psu, config, lock)
        channel.apply(12.0, 5.0)

        psu.apply.assert_called_with(1, 12.0, 5.0)

    def test_apply_exceeds_voltage_limit_raises(self) -> None:
        """Test that apply with voltage above limit raises ValueError."""
        psu = self.create_mock_psu()
        config = PsuChannelConfig(id=1, logical_name="main_battery", max_voltage=15.0)
        import threading

        lock = threading.Lock()

        channel = BkDcPsuChannel(psu, config, lock)

        with pytest.raises(ValueError, match="Voltage.*exceeds limit"):
            channel.apply(20.0, 5.0)

    def test_apply_exceeds_current_limit_raises(self) -> None:
        """Test that apply with current above limit raises ValueError."""
        psu = self.create_mock_psu()
        config = PsuChannelConfig(id=1, logical_name="main_battery", max_current=10.0)
        import threading

        lock = threading.Lock()

        channel = BkDcPsuChannel(psu, config, lock)

        with pytest.raises(ValueError, match="Current.*exceeds limit"):
            channel.apply(12.0, 15.0)

    def test_get_voltage(self) -> None:
        """Test getting voltage setpoint."""
        psu = self.create_mock_psu()
        config = PsuChannelConfig(id=1, logical_name="main_battery")
        import threading

        lock = threading.Lock()

        channel = BkDcPsuChannel(psu, config, lock)
        voltage = channel.get_voltage()

        psu.select_channel.assert_called_with(1)
        assert voltage == 12.0

    def test_get_current_limit(self) -> None:
        """Test getting current limit."""
        psu = self.create_mock_psu()
        config = PsuChannelConfig(id=1, logical_name="main_battery")
        import threading

        lock = threading.Lock()

        channel = BkDcPsuChannel(psu, config, lock)
        current = channel.get_current_limit()

        psu.select_channel.assert_called_with(1)
        assert current == 1.5

    def test_is_output_enabled(self) -> None:
        """Test checking output enabled state."""
        psu = self.create_mock_psu()
        config = PsuChannelConfig(id=1, logical_name="main_battery")
        import threading

        lock = threading.Lock()

        channel = BkDcPsuChannel(psu, config, lock)
        enabled = channel.is_output_enabled()

        psu.select_channel.assert_called_with(1)
        assert enabled is True

    def test_measure_voltage(self) -> None:
        """Test measuring actual voltage."""
        psu = self.create_mock_psu()
        config = PsuChannelConfig(id=1, logical_name="main_battery")
        import threading

        lock = threading.Lock()

        channel = BkDcPsuChannel(psu, config, lock)
        voltage = channel.measure_voltage()

        psu.select_channel.assert_called_with(1)
        assert voltage == 11.98

    def test_measure_current(self) -> None:
        """Test measuring actual current."""
        psu = self.create_mock_psu()
        config = PsuChannelConfig(id=1, logical_name="main_battery")
        import threading

        lock = threading.Lock()

        channel = BkDcPsuChannel(psu, config, lock)
        current = channel.measure_current()

        psu.select_channel.assert_called_with(1)
        assert current == 1.48

    def test_measure_power(self) -> None:
        """Test measuring actual power."""
        psu = self.create_mock_psu()
        config = PsuChannelConfig(id=1, logical_name="main_battery")
        import threading

        lock = threading.Lock()

        channel = BkDcPsuChannel(psu, config, lock)
        power = channel.measure_power()

        psu.select_channel.assert_called_with(1)
        assert power == 17.73


class TestBkMultiChannelPsu:
    """Tests for BkMultiChannelPsu."""

    def create_mock_psu(self) -> MagicMock:
        """Create a mock BkDcPsu."""
        psu = MagicMock()
        psu.get_identity.return_value = MagicMock(
            manufacturer="BK Precision",
            model="9115",
            serial="12345",
            firmware="1.0",
        )
        return psu

    def test_get_identity(self) -> None:
        """Test getting instrument identity."""
        psu = self.create_mock_psu()
        channels = (
            PsuChannelConfig(id=1, logical_name="main_battery"),
            PsuChannelConfig(id=2, logical_name="cpu_power"),
        )

        multi_psu = BkMultiChannelPsu(psu, channels)
        identity = multi_psu.get_identity()

        assert identity.manufacturer == "BK Precision"
        assert identity.model == "9115"

    def test_close(self) -> None:
        """Test closing the PSU connection."""
        psu = self.create_mock_psu()
        channels = (PsuChannelConfig(id=1, logical_name="main_battery"),)

        multi_psu = BkMultiChannelPsu(psu, channels)
        multi_psu.close()

        psu.close.assert_called_once()

    def test_get_channel_by_id(self) -> None:
        """Test getting channel by physical ID."""
        psu = self.create_mock_psu()
        channels = (
            PsuChannelConfig(id=1, logical_name="main_battery"),
            PsuChannelConfig(id=2, logical_name="cpu_power"),
        )

        multi_psu = BkMultiChannelPsu(psu, channels)

        ch1 = multi_psu.get_channel(1)
        assert ch1.logical_name == "main_battery"
        assert ch1.channel_id == 1

        ch2 = multi_psu.get_channel(2)
        assert ch2.logical_name == "cpu_power"
        assert ch2.channel_id == 2

    def test_get_channel_by_id_not_found_raises(self) -> None:
        """Test that getting nonexistent channel ID raises KeyError."""
        psu = self.create_mock_psu()
        channels = (PsuChannelConfig(id=1, logical_name="main_battery"),)

        multi_psu = BkMultiChannelPsu(psu, channels)

        with pytest.raises(KeyError, match="not configured"):
            multi_psu.get_channel(99)

    def test_get_channel_by_name(self) -> None:
        """Test getting channel by logical name."""
        psu = self.create_mock_psu()
        channels = (
            PsuChannelConfig(id=1, logical_name="main_battery"),
            PsuChannelConfig(id=2, logical_name="cpu_power"),
        )

        multi_psu = BkMultiChannelPsu(psu, channels)

        ch = multi_psu.get_channel_by_name("main_battery")
        assert ch is not None
        assert ch.logical_name == "main_battery"
        assert ch.channel_id == 1

        ch2 = multi_psu.get_channel_by_name("cpu_power")
        assert ch2 is not None
        assert ch2.logical_name == "cpu_power"

    def test_get_channel_by_name_not_found_returns_none(self) -> None:
        """Test that getting nonexistent logical name returns None."""
        psu = self.create_mock_psu()
        channels = (PsuChannelConfig(id=1, logical_name="main_battery"),)

        multi_psu = BkMultiChannelPsu(psu, channels)

        assert multi_psu.get_channel_by_name("nonexistent") is None

    def test_list_channels(self) -> None:
        """Test listing all channels."""
        psu = self.create_mock_psu()
        channels = (
            PsuChannelConfig(id=1, logical_name="main_battery"),
            PsuChannelConfig(id=2, logical_name="cpu_power"),
            PsuChannelConfig(id=3, logical_name="peripheral_power"),
        )

        multi_psu = BkMultiChannelPsu(psu, channels)
        channel_list = multi_psu.list_channels()

        assert len(channel_list) == 3

    def test_list_logical_names(self) -> None:
        """Test listing all logical names."""
        psu = self.create_mock_psu()
        channels = (
            PsuChannelConfig(id=1, logical_name="main_battery"),
            PsuChannelConfig(id=2, logical_name="cpu_power"),
        )

        multi_psu = BkMultiChannelPsu(psu, channels)
        names = multi_psu.list_logical_names()

        assert len(names) == 2
        assert "main_battery" in names
        assert "cpu_power" in names

    def test_channels_share_lock(self) -> None:
        """Test that all channels share the same lock."""
        psu = self.create_mock_psu()
        channels = (
            PsuChannelConfig(id=1, logical_name="ch1"),
            PsuChannelConfig(id=2, logical_name="ch2"),
        )

        multi_psu = BkMultiChannelPsu(psu, channels)

        ch1 = multi_psu.get_channel(1)
        ch2 = multi_psu.get_channel(2)

        # Both channels should share the same lock
        assert ch1._lock is ch2._lock
