"""Tests for telemetry types."""

import pytest

from hwtest_core.types.common import ChannelId, SourceId, Timestamp
from hwtest_core.types.telemetry import TelemetryMessage, TelemetryValue, ValueQuality


class TestValueQuality:
    """Tests for ValueQuality enum."""

    def test_values(self) -> None:
        """Test enum values."""
        assert ValueQuality.GOOD.value == "good"
        assert ValueQuality.UNCERTAIN.value == "uncertain"
        assert ValueQuality.BAD.value == "bad"
        assert ValueQuality.STALE.value == "stale"

    def test_from_string(self) -> None:
        """Test creating from string value."""
        assert ValueQuality("good") == ValueQuality.GOOD
        assert ValueQuality("uncertain") == ValueQuality.UNCERTAIN


class TestTelemetryValue:
    """Tests for TelemetryValue."""

    def test_create(self) -> None:
        """Test creating a telemetry value."""
        ts = Timestamp(unix_ns=1000000000, source="local")
        value = TelemetryValue(
            channel=ChannelId("ch0"),
            value=3.3,
            unit="V",
            source_timestamp=ts,
        )
        assert value.channel == "ch0"
        assert value.value == 3.3
        assert value.unit == "V"
        assert value.source_timestamp == ts
        assert value.publish_timestamp is None
        assert value.quality == ValueQuality.GOOD

    def test_create_with_quality(self) -> None:
        """Test creating with non-default quality."""
        ts = Timestamp.now()
        value = TelemetryValue(
            channel=ChannelId("ch0"),
            value=0.0,
            unit="V",
            source_timestamp=ts,
            quality=ValueQuality.BAD,
        )
        assert value.quality == ValueQuality.BAD

    def test_to_dict(self) -> None:
        """Test converting to dictionary."""
        ts = Timestamp(unix_ns=1000000000, source="ptp")
        pub_ts = Timestamp(unix_ns=1000000100, source="local")
        value = TelemetryValue(
            channel=ChannelId("voltage"),
            value=5.0,
            unit="V",
            source_timestamp=ts,
            publish_timestamp=pub_ts,
            quality=ValueQuality.UNCERTAIN,
        )
        d = value.to_dict()
        assert d["channel"] == "voltage"
        assert d["value"] == 5.0
        assert d["unit"] == "V"
        assert d["source_timestamp"] == 1000000000
        assert d["source_timestamp_source"] == "ptp"
        assert d["publish_timestamp"] == 1000000100
        assert d["quality"] == "uncertain"

    def test_from_dict(self) -> None:
        """Test creating from dictionary."""
        d = {
            "channel": "current",
            "value": 1.5,
            "unit": "A",
            "source_timestamp": 2000000000,
            "source_timestamp_source": "ntp",
            "publish_timestamp": None,
            "quality": "good",
        }
        value = TelemetryValue.from_dict(d)
        assert value.channel == "current"
        assert value.value == 1.5
        assert value.unit == "A"
        assert value.source_timestamp.unix_ns == 2000000000
        assert value.source_timestamp.source == "ntp"
        assert value.publish_timestamp is None
        assert value.quality == ValueQuality.GOOD

    def test_roundtrip(self) -> None:
        """Test dict roundtrip."""
        ts = Timestamp(unix_ns=1000000000, source="local")
        original = TelemetryValue(
            channel=ChannelId("temp"),
            value=25.5,
            unit="°C",
            source_timestamp=ts,
            quality=ValueQuality.GOOD,
        )
        restored = TelemetryValue.from_dict(original.to_dict())
        assert restored.channel == original.channel
        assert restored.value == original.value
        assert restored.unit == original.unit
        assert restored.source_timestamp.unix_ns == original.source_timestamp.unix_ns
        assert restored.quality == original.quality

    def test_immutable(self) -> None:
        """Test that TelemetryValue is immutable."""
        ts = Timestamp.now()
        value = TelemetryValue(
            channel=ChannelId("ch0"),
            value=1.0,
            unit="V",
            source_timestamp=ts,
        )
        with pytest.raises(AttributeError):
            value.value = 2.0  # type: ignore[misc]


class TestTelemetryMessage:
    """Tests for TelemetryMessage."""

    @pytest.fixture
    def sample_values(self) -> tuple[TelemetryValue, ...]:
        """Create sample telemetry values."""
        ts = Timestamp(unix_ns=1000000000)
        return (
            TelemetryValue(ChannelId("ch0"), 3.3, "V", ts),
            TelemetryValue(ChannelId("ch1"), 5.0, "V", ts),
            TelemetryValue(ChannelId("ch2"), 12.0, "V", ts),
        )

    def test_create(self, sample_values: tuple[TelemetryValue, ...]) -> None:
        """Test creating a message."""
        msg = TelemetryMessage(
            source=SourceId("sensor1"),
            values=sample_values,
            sequence=42,
        )
        assert msg.source == "sensor1"
        assert len(msg.values) == 3
        assert msg.sequence == 42

    def test_to_dict(self, sample_values: tuple[TelemetryValue, ...]) -> None:
        """Test converting to dictionary."""
        msg = TelemetryMessage(
            source=SourceId("adc0"),
            values=sample_values,
            sequence=1,
        )
        d = msg.to_dict()
        assert d["source"] == "adc0"
        assert len(d["values"]) == 3
        assert d["sequence"] == 1

    def test_from_dict(self) -> None:
        """Test creating from dictionary."""
        d = {
            "source": "device1",
            "values": [
                {
                    "channel": "temp",
                    "value": 25.0,
                    "unit": "C",
                    "source_timestamp": 1000000000,
                    "quality": "good",
                }
            ],
            "sequence": 100,
        }
        msg = TelemetryMessage.from_dict(d)
        assert msg.source == "device1"
        assert len(msg.values) == 1
        assert msg.values[0].channel == "temp"
        assert msg.sequence == 100

    def test_to_bytes(self, sample_values: tuple[TelemetryValue, ...]) -> None:
        """Test serializing to bytes."""
        msg = TelemetryMessage(
            source=SourceId("test"),
            values=sample_values,
            sequence=1,
        )
        data = msg.to_bytes()
        assert isinstance(data, bytes)
        assert b"test" in data
        assert b"ch0" in data

    def test_from_bytes(self, sample_values: tuple[TelemetryValue, ...]) -> None:
        """Test deserializing from bytes."""
        original = TelemetryMessage(
            source=SourceId("test"),
            values=sample_values,
            sequence=42,
        )
        data = original.to_bytes()
        restored = TelemetryMessage.from_bytes(data)
        assert restored.source == original.source
        assert restored.sequence == original.sequence
        assert len(restored.values) == len(original.values)

    def test_roundtrip(self, sample_values: tuple[TelemetryValue, ...]) -> None:
        """Test bytes roundtrip."""
        original = TelemetryMessage(
            source=SourceId("sensor"),
            values=sample_values,
            sequence=999,
        )
        data = original.to_bytes()
        restored = TelemetryMessage.from_bytes(data)

        assert restored.source == original.source
        assert restored.sequence == original.sequence
        for orig_val, rest_val in zip(original.values, restored.values):
            assert rest_val.channel == orig_val.channel
            assert rest_val.value == pytest.approx(orig_val.value)
            assert rest_val.unit == orig_val.unit
