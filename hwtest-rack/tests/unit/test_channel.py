"""Unit tests for channel registry and logical naming."""

from __future__ import annotations

import pytest

from hwtest_rack.channel import ChannelRegistry, ChannelType, LogicalChannel


class TestChannelType:
    """Tests for ChannelType enum."""

    def test_values(self) -> None:
        """Test that expected channel types exist."""
        assert ChannelType.PSU.value == "psu"
        assert ChannelType.LOAD.value == "load"
        assert ChannelType.DAQ_ANALOG.value == "daq_analog"
        assert ChannelType.DAQ_DIGITAL.value == "daq_digital"

    def test_from_string(self) -> None:
        """Test creating ChannelType from string."""
        assert ChannelType("psu") == ChannelType.PSU
        assert ChannelType("load") == ChannelType.LOAD
        assert ChannelType("daq_analog") == ChannelType.DAQ_ANALOG
        assert ChannelType("daq_digital") == ChannelType.DAQ_DIGITAL

    def test_invalid_value_raises(self) -> None:
        """Test that invalid value raises ValueError."""
        with pytest.raises(ValueError):
            ChannelType("invalid")


class TestLogicalChannel:
    """Tests for LogicalChannel dataclass."""

    def test_create_minimal(self) -> None:
        """Test creating a LogicalChannel with minimal args."""
        channel = LogicalChannel(
            logical_name="main_battery",
            instrument_name="dc_psu_1",
            channel_id=1,
            channel_type=ChannelType.PSU,
        )
        assert channel.logical_name == "main_battery"
        assert channel.instrument_name == "dc_psu_1"
        assert channel.channel_id == 1
        assert channel.channel_type == ChannelType.PSU
        assert channel.metadata == {}

    def test_create_with_metadata(self) -> None:
        """Test creating a LogicalChannel with metadata."""
        channel = LogicalChannel(
            logical_name="dut_voltage",
            instrument_name="voltage_daq",
            channel_id=0,
            channel_type=ChannelType.DAQ_ANALOG,
            metadata={"unit": "V", "calibration_factor": 1.5},
        )
        assert channel.metadata["unit"] == "V"
        assert channel.metadata["calibration_factor"] == 1.5

    def test_frozen(self) -> None:
        """Test that LogicalChannel is immutable."""
        channel = LogicalChannel(
            logical_name="test",
            instrument_name="inst",
            channel_id=0,
            channel_type=ChannelType.PSU,
        )
        with pytest.raises(AttributeError):
            channel.logical_name = "new_name"  # type: ignore[misc]

    def test_hash_based_on_logical_name(self) -> None:
        """Test that hash is based on logical_name."""
        ch1 = LogicalChannel("test", "inst1", 1, ChannelType.PSU)
        ch2 = LogicalChannel("test", "inst2", 2, ChannelType.LOAD)
        ch3 = LogicalChannel("other", "inst1", 1, ChannelType.PSU)

        # Same logical_name should have same hash
        assert hash(ch1) == hash(ch2)
        # Different logical_name should have different hash
        assert hash(ch1) != hash(ch3)


class TestChannelRegistry:
    """Tests for ChannelRegistry."""

    def test_empty_registry(self) -> None:
        """Test empty registry."""
        registry = ChannelRegistry()
        assert len(registry) == 0
        assert registry.list_all() == []

    def test_register_single_channel(self) -> None:
        """Test registering a single channel."""
        registry = ChannelRegistry()
        channel = LogicalChannel(
            logical_name="main_battery",
            instrument_name="psu_1",
            channel_id=1,
            channel_type=ChannelType.PSU,
        )
        registry.register(channel)

        assert len(registry) == 1
        assert "main_battery" in registry
        assert registry.get("main_battery") == channel

    def test_register_multiple_channels(self) -> None:
        """Test registering multiple channels."""
        registry = ChannelRegistry()

        ch1 = LogicalChannel("main_battery", "psu_1", 1, ChannelType.PSU)
        ch2 = LogicalChannel("cpu_power", "psu_1", 2, ChannelType.PSU)
        ch3 = LogicalChannel("dut_voltage", "daq_1", 0, ChannelType.DAQ_ANALOG)

        registry.register(ch1)
        registry.register(ch2)
        registry.register(ch3)

        assert len(registry) == 3
        assert registry.get("main_battery") == ch1
        assert registry.get("cpu_power") == ch2
        assert registry.get("dut_voltage") == ch3

    def test_register_duplicate_raises(self) -> None:
        """Test that registering duplicate logical name raises ValueError."""
        registry = ChannelRegistry()
        ch1 = LogicalChannel("main_battery", "psu_1", 1, ChannelType.PSU)
        ch2 = LogicalChannel("main_battery", "psu_2", 1, ChannelType.PSU)

        registry.register(ch1)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(ch2)

    def test_get_nonexistent_returns_none(self) -> None:
        """Test that getting nonexistent channel returns None."""
        registry = ChannelRegistry()
        assert registry.get("nonexistent") is None

    def test_resolve_channel(self) -> None:
        """Test resolving logical name to instrument and channel ID."""
        registry = ChannelRegistry()
        channel = LogicalChannel("main_battery", "psu_1", 1, ChannelType.PSU)
        registry.register(channel)

        result = registry.resolve("main_battery")
        assert result == ("psu_1", 1)

    def test_resolve_nonexistent_returns_none(self) -> None:
        """Test that resolving nonexistent channel returns None."""
        registry = ChannelRegistry()
        assert registry.resolve("nonexistent") is None

    def test_get_by_instrument(self) -> None:
        """Test getting all channels for an instrument."""
        registry = ChannelRegistry()

        ch1 = LogicalChannel("main_battery", "psu_1", 1, ChannelType.PSU)
        ch2 = LogicalChannel("cpu_power", "psu_1", 2, ChannelType.PSU)
        ch3 = LogicalChannel("dut_voltage", "daq_1", 0, ChannelType.DAQ_ANALOG)

        registry.register(ch1)
        registry.register(ch2)
        registry.register(ch3)

        psu_channels = registry.get_by_instrument("psu_1")
        assert len(psu_channels) == 2
        assert ch1 in psu_channels
        assert ch2 in psu_channels

        daq_channels = registry.get_by_instrument("daq_1")
        assert len(daq_channels) == 1
        assert ch3 in daq_channels

    def test_get_by_instrument_nonexistent(self) -> None:
        """Test getting channels for nonexistent instrument."""
        registry = ChannelRegistry()
        assert registry.get_by_instrument("nonexistent") == []

    def test_get_by_type(self) -> None:
        """Test getting all channels of a specific type."""
        registry = ChannelRegistry()

        ch1 = LogicalChannel("main_battery", "psu_1", 1, ChannelType.PSU)
        ch2 = LogicalChannel("cpu_power", "psu_1", 2, ChannelType.PSU)
        ch3 = LogicalChannel("dut_voltage", "daq_1", 0, ChannelType.DAQ_ANALOG)
        ch4 = LogicalChannel("relay_1", "dio_1", 0, ChannelType.DAQ_DIGITAL)

        registry.register(ch1)
        registry.register(ch2)
        registry.register(ch3)
        registry.register(ch4)

        psu_channels = registry.get_by_type(ChannelType.PSU)
        assert len(psu_channels) == 2
        assert ch1 in psu_channels
        assert ch2 in psu_channels

        analog_channels = registry.get_by_type(ChannelType.DAQ_ANALOG)
        assert len(analog_channels) == 1
        assert ch3 in analog_channels

        digital_channels = registry.get_by_type(ChannelType.DAQ_DIGITAL)
        assert len(digital_channels) == 1
        assert ch4 in digital_channels

    def test_get_by_type_empty(self) -> None:
        """Test getting channels by type when none exist."""
        registry = ChannelRegistry()
        ch1 = LogicalChannel("main_battery", "psu_1", 1, ChannelType.PSU)
        registry.register(ch1)

        assert registry.get_by_type(ChannelType.LOAD) == []

    def test_list_all(self) -> None:
        """Test listing all channels."""
        registry = ChannelRegistry()

        ch1 = LogicalChannel("main_battery", "psu_1", 1, ChannelType.PSU)
        ch2 = LogicalChannel("cpu_power", "psu_1", 2, ChannelType.PSU)

        registry.register(ch1)
        registry.register(ch2)

        all_channels = registry.list_all()
        assert len(all_channels) == 2
        assert ch1 in all_channels
        assert ch2 in all_channels

    def test_contains(self) -> None:
        """Test 'in' operator."""
        registry = ChannelRegistry()
        ch1 = LogicalChannel("main_battery", "psu_1", 1, ChannelType.PSU)
        registry.register(ch1)

        assert "main_battery" in registry
        assert "nonexistent" not in registry
