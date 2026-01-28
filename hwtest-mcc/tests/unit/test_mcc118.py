"""Unit tests for MCC 118 instrument driver."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hwtest_core.errors import HwtestError
from hwtest_core.types.common import DataType, SourceId
from hwtest_core.types.streaming import StreamData, StreamField, StreamSchema

from hwtest_mcc.mcc118 import Mcc118Channel, Mcc118Config, Mcc118Instrument

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scan_result(
    data: list[float],
    hardware_overrun: bool = False,
    buffer_overrun: bool = False,
    running: bool = True,
    timeout: bool = False,
) -> SimpleNamespace:
    """Build a fake daqhats scan-read result."""
    return SimpleNamespace(
        data=data,
        hardware_overrun=hardware_overrun,
        buffer_overrun=buffer_overrun,
        running=running,
        timeout=timeout,
    )


def _make_mock_daqhats(actual_rate: float = 1000.0) -> tuple[MagicMock, MagicMock]:
    """Return (mock_module, mock_hat) for patching daqhats."""
    mock_module = MagicMock()
    mock_module.OptionFlags.CONTINUOUS = 0x01

    mock_hat = MagicMock()
    mock_hat.a_in_scan_start.return_value = actual_rate
    mock_module.mcc118.return_value = mock_hat

    return mock_module, mock_hat


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestMcc118Channel:
    def test_create(self) -> None:
        ch = Mcc118Channel(id=0, name="voltage")
        assert ch.id == 0
        assert ch.name == "voltage"

    def test_frozen(self) -> None:
        ch = Mcc118Channel(id=0, name="voltage")
        with pytest.raises(AttributeError):
            ch.id = 1  # type: ignore[misc]


class TestMcc118Config:
    def test_valid(self) -> None:
        config = Mcc118Config(
            address=0,
            sample_rate=1000.0,
            channels=(Mcc118Channel(0, "ch0"),),
            source_id="daq01",
        )
        assert config.address == 0
        assert config.sample_rate == 1000.0
        assert len(config.channels) == 1
        assert config.source_id == "daq01"

    def test_address_too_low(self) -> None:
        with pytest.raises(ValueError, match="address"):
            Mcc118Config(
                address=-1,
                sample_rate=1000.0,
                channels=(Mcc118Channel(0, "ch0"),),
                source_id="daq01",
            )

    def test_address_too_high(self) -> None:
        with pytest.raises(ValueError, match="address"):
            Mcc118Config(
                address=8,
                sample_rate=1000.0,
                channels=(Mcc118Channel(0, "ch0"),),
                source_id="daq01",
            )

    def test_sample_rate_zero(self) -> None:
        with pytest.raises(ValueError, match="sample_rate"):
            Mcc118Config(
                address=0,
                sample_rate=0.0,
                channels=(Mcc118Channel(0, "ch0"),),
                source_id="daq01",
            )

    def test_sample_rate_negative(self) -> None:
        with pytest.raises(ValueError, match="sample_rate"):
            Mcc118Config(
                address=0,
                sample_rate=-100.0,
                channels=(Mcc118Channel(0, "ch0"),),
                source_id="daq01",
            )

    def test_empty_channels(self) -> None:
        with pytest.raises(ValueError, match="channels"):
            Mcc118Config(
                address=0,
                sample_rate=1000.0,
                channels=(),
                source_id="daq01",
            )

    def test_channel_id_too_low(self) -> None:
        with pytest.raises(ValueError, match="channel id"):
            Mcc118Config(
                address=0,
                sample_rate=1000.0,
                channels=(Mcc118Channel(-1, "ch0"),),
                source_id="daq01",
            )

    def test_channel_id_too_high(self) -> None:
        with pytest.raises(ValueError, match="channel id"):
            Mcc118Config(
                address=0,
                sample_rate=1000.0,
                channels=(Mcc118Channel(8, "ch0"),),
                source_id="daq01",
            )

    def test_duplicate_channel_id(self) -> None:
        with pytest.raises(ValueError, match="duplicate channel id"):
            Mcc118Config(
                address=0,
                sample_rate=1000.0,
                channels=(Mcc118Channel(0, "ch0"), Mcc118Channel(0, "ch1")),
                source_id="daq01",
            )

    def test_duplicate_channel_name(self) -> None:
        with pytest.raises(ValueError, match="duplicate channel name"):
            Mcc118Config(
                address=0,
                sample_rate=1000.0,
                channels=(Mcc118Channel(0, "same"), Mcc118Channel(1, "same")),
                source_id="daq01",
            )


# ---------------------------------------------------------------------------
# Schema construction
# ---------------------------------------------------------------------------


class TestSchemaConstruction:
    def test_schema_fields_match_channels(self) -> None:
        config = Mcc118Config(
            address=0,
            sample_rate=1000.0,
            channels=(
                Mcc118Channel(0, "dut_voltage"),
                Mcc118Channel(2, "ref_voltage"),
            ),
            source_id="daq01",
        )
        publisher = MagicMock()
        instrument = Mcc118Instrument(config, publisher)

        schema = instrument.schema
        assert schema.source_id == SourceId("daq01")
        assert len(schema.fields) == 2
        assert schema.fields[0] == StreamField("dut_voltage", DataType.F64, "V")
        assert schema.fields[1] == StreamField("ref_voltage", DataType.F64, "V")

    def test_schema_single_channel(self) -> None:
        config = Mcc118Config(
            address=3,
            sample_rate=500.0,
            channels=(Mcc118Channel(5, "temp"),),
            source_id="sensor01",
        )
        publisher = MagicMock()
        instrument = Mcc118Instrument(config, publisher)

        schema = instrument.schema
        assert schema.source_id == SourceId("sensor01")
        assert len(schema.fields) == 1
        assert schema.fields[0].name == "temp"
        assert schema.fields[0].dtype == DataType.F64
        assert schema.fields[0].unit == "V"

    def test_schema_id_is_deterministic(self) -> None:
        config = Mcc118Config(
            address=0,
            sample_rate=1000.0,
            channels=(
                Mcc118Channel(0, "a"),
                Mcc118Channel(1, "b"),
            ),
            source_id="x",
        )
        inst1 = Mcc118Instrument(config, MagicMock())
        inst2 = Mcc118Instrument(config, MagicMock())
        assert inst1.schema.schema_id == inst2.schema.schema_id
        assert inst1.schema.schema_id != 0


# ---------------------------------------------------------------------------
# Instrument lifecycle
# ---------------------------------------------------------------------------


class TestMcc118Instrument:
    async def test_initial_state(self) -> None:
        config = Mcc118Config(
            address=0,
            sample_rate=1000.0,
            channels=(Mcc118Channel(0, "ch0"),),
            source_id="daq01",
        )
        instrument = Mcc118Instrument(config, MagicMock())
        assert not instrument.is_running
        assert instrument.actual_sample_rate == 0.0

    async def test_start_stop(self) -> None:
        config = Mcc118Config(
            address=0,
            sample_rate=1000.0,
            channels=(Mcc118Channel(0, "ch0"),),
            source_id="daq01",
        )
        mock_module, mock_hat = _make_mock_daqhats()
        mock_hat.a_in_scan_read.return_value = _make_scan_result([], running=False)

        publisher = MagicMock()
        publisher.publish = AsyncMock()
        instrument = Mcc118Instrument(config, publisher)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            await instrument.start()
            assert instrument.is_running
            assert instrument.actual_sample_rate == 1000.0

            # Let scan loop process the running=False result and exit
            await asyncio.sleep(0.1)
            await instrument.stop()

        assert not instrument.is_running
        mock_module.mcc118.assert_called_once_with(0)
        mock_hat.a_in_scan_start.assert_called_once()
        mock_hat.a_in_scan_stop.assert_called_once()
        mock_hat.a_in_scan_cleanup.assert_called_once()

    async def test_start_is_idempotent(self) -> None:
        config = Mcc118Config(
            address=0,
            sample_rate=1000.0,
            channels=(Mcc118Channel(0, "ch0"),),
            source_id="daq01",
        )
        mock_module, mock_hat = _make_mock_daqhats()
        mock_hat.a_in_scan_read.return_value = _make_scan_result([], running=False)

        publisher = MagicMock()
        publisher.publish = AsyncMock()
        instrument = Mcc118Instrument(config, publisher)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            await instrument.start()
            await instrument.start()  # second call should be a no-op
            await asyncio.sleep(0.1)
            await instrument.stop()

        # HAT constructor called only once
        mock_module.mcc118.assert_called_once()

    async def test_stop_when_not_running(self) -> None:
        config = Mcc118Config(
            address=0,
            sample_rate=1000.0,
            channels=(Mcc118Channel(0, "ch0"),),
            source_id="daq01",
        )
        instrument = Mcc118Instrument(config, MagicMock())
        await instrument.stop()  # should not raise

    async def test_daqhats_not_installed(self) -> None:
        config = Mcc118Config(
            address=0,
            sample_rate=1000.0,
            channels=(Mcc118Channel(0, "ch0"),),
            source_id="daq01",
        )
        instrument = Mcc118Instrument(config, MagicMock())

        with patch.dict(sys.modules, {"daqhats": None}):
            with pytest.raises(HwtestError, match="daqhats"):
                await instrument.start()

    async def test_hat_not_found(self) -> None:
        config = Mcc118Config(
            address=5,
            sample_rate=1000.0,
            channels=(Mcc118Channel(0, "ch0"),),
            source_id="daq01",
        )
        mock_module = MagicMock()
        mock_module.mcc118.side_effect = Exception("No HAT at address 5")

        instrument = Mcc118Instrument(config, MagicMock())

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            with pytest.raises(HwtestError, match="Failed to open MCC 118"):
                await instrument.start()

    async def test_channel_mask(self) -> None:
        config = Mcc118Config(
            address=0,
            sample_rate=1000.0,
            channels=(
                Mcc118Channel(0, "ch0"),
                Mcc118Channel(2, "ch2"),
                Mcc118Channel(7, "ch7"),
            ),
            source_id="daq01",
        )
        instrument = Mcc118Instrument(config, MagicMock())
        # Channels 0, 2, 7 -> bits 0, 2, 7 -> 0b10000101 = 133
        assert instrument._channel_mask() == 0b10000101


# ---------------------------------------------------------------------------
# Data reshaping
# ---------------------------------------------------------------------------


class TestDataReshaping:
    async def test_interleaved_to_tuples(self) -> None:
        """Verify interleaved scan data is reshaped into per-sample tuples."""
        config = Mcc118Config(
            address=0,
            sample_rate=1000.0,
            channels=(
                Mcc118Channel(0, "ch_a"),
                Mcc118Channel(2, "ch_b"),
            ),
            source_id="daq01",
        )
        mock_module, mock_hat = _make_mock_daqhats(actual_rate=1000.0)

        call_count = 0

        def mock_read(samples_per_channel: int, timeout: float) -> SimpleNamespace:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 3 samples, 2 channels interleaved:
                # sample0: (1.0, 2.0), sample1: (3.0, 4.0), sample2: (5.0, 6.0)
                return _make_scan_result([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
            return _make_scan_result([], running=False)

        mock_hat.a_in_scan_read.side_effect = mock_read

        published: list[StreamData] = []
        publish_event = asyncio.Event()

        async def capture_publish(data: StreamData) -> None:
            published.append(data)
            publish_event.set()

        publisher = MagicMock()
        publisher.publish = capture_publish

        instrument = Mcc118Instrument(config, publisher)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            with patch("hwtest_mcc.mcc118.time") as mock_time:
                mock_time.time_ns.return_value = 1_000_000_000_000
                await instrument.start()
                await asyncio.wait_for(publish_event.wait(), timeout=5.0)
                await instrument.stop()

        assert len(published) == 1
        data = published[0]
        assert data.schema_id == instrument.schema.schema_id
        assert data.period_ns == 1_000_000  # 1 GHz / 1000 Hz
        assert data.timestamp_ns == 1_000_000_000_000
        assert data.samples == ((1.0, 2.0), (3.0, 4.0), (5.0, 6.0))

    async def test_single_channel_reshaping(self) -> None:
        """Verify reshaping works with a single channel."""
        config = Mcc118Config(
            address=0,
            sample_rate=500.0,
            channels=(Mcc118Channel(3, "voltage"),),
            source_id="daq01",
        )
        mock_module, mock_hat = _make_mock_daqhats(actual_rate=500.0)

        call_count = 0

        def mock_read(samples_per_channel: int, timeout: float) -> SimpleNamespace:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_scan_result([10.0, 20.0])
            return _make_scan_result([], running=False)

        mock_hat.a_in_scan_read.side_effect = mock_read

        published: list[StreamData] = []
        publish_event = asyncio.Event()

        async def capture_publish(data: StreamData) -> None:
            published.append(data)
            publish_event.set()

        publisher = MagicMock()
        publisher.publish = capture_publish

        instrument = Mcc118Instrument(config, publisher)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            await instrument.start()
            await asyncio.wait_for(publish_event.wait(), timeout=5.0)
            await instrument.stop()

        assert len(published) == 1
        data = published[0]
        assert data.period_ns == 2_000_000  # 1 GHz / 500 Hz
        assert data.samples == ((10.0,), (20.0,))

    async def test_consecutive_batches_have_continuous_timestamps(self) -> None:
        """Verify timestamps increment correctly across batches."""
        config = Mcc118Config(
            address=0,
            sample_rate=1000.0,
            channels=(Mcc118Channel(0, "ch0"),),
            source_id="daq01",
        )
        mock_module, mock_hat = _make_mock_daqhats(actual_rate=1000.0)

        call_count = 0

        def mock_read(samples_per_channel: int, timeout: float) -> SimpleNamespace:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return _make_scan_result([1.0, 2.0])  # 2 samples each batch
            return _make_scan_result([], running=False)

        mock_hat.a_in_scan_read.side_effect = mock_read

        published: list[StreamData] = []
        both_received = asyncio.Event()

        async def capture_publish(data: StreamData) -> None:
            published.append(data)
            if len(published) >= 2:
                both_received.set()

        publisher = MagicMock()
        publisher.publish = capture_publish

        instrument = Mcc118Instrument(config, publisher)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            with patch("hwtest_mcc.mcc118.time") as mock_time:
                mock_time.time_ns.return_value = 0
                await instrument.start()
                await asyncio.wait_for(both_received.wait(), timeout=5.0)
                await instrument.stop()

        assert len(published) == 2
        # First batch: 2 samples starting at t=0
        assert published[0].timestamp_ns == 0
        # Second batch: starts after 2 samples at period_ns=1_000_000
        assert published[1].timestamp_ns == 2 * 1_000_000


# ---------------------------------------------------------------------------
# Overrun handling
# ---------------------------------------------------------------------------


class TestOverrunHandling:
    async def test_hardware_overrun_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        config = Mcc118Config(
            address=0,
            sample_rate=1000.0,
            channels=(Mcc118Channel(0, "ch0"),),
            source_id="daq01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        call_count = 0

        def mock_read(samples_per_channel: int, timeout: float) -> SimpleNamespace:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_scan_result([1.0], hardware_overrun=True)
            return _make_scan_result([], running=False)

        mock_hat.a_in_scan_read.side_effect = mock_read

        published: list[StreamData] = []
        publish_event = asyncio.Event()

        async def capture_publish(data: StreamData) -> None:
            published.append(data)
            publish_event.set()

        publisher = MagicMock()
        publisher.publish = capture_publish

        instrument = Mcc118Instrument(config, publisher)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            await instrument.start()
            await asyncio.wait_for(publish_event.wait(), timeout=5.0)
            await instrument.stop()

        assert "hardware buffer overrun" in caplog.text
        # Data was still published despite the overrun
        assert len(published) == 1

    async def test_buffer_overrun_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        config = Mcc118Config(
            address=0,
            sample_rate=1000.0,
            channels=(Mcc118Channel(0, "ch0"),),
            source_id="daq01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        call_count = 0

        def mock_read(samples_per_channel: int, timeout: float) -> SimpleNamespace:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_scan_result([1.0], buffer_overrun=True)
            return _make_scan_result([], running=False)

        mock_hat.a_in_scan_read.side_effect = mock_read

        published: list[StreamData] = []
        publish_event = asyncio.Event()

        async def capture_publish(data: StreamData) -> None:
            published.append(data)
            publish_event.set()

        publisher = MagicMock()
        publisher.publish = capture_publish

        instrument = Mcc118Instrument(config, publisher)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            await instrument.start()
            await asyncio.wait_for(publish_event.wait(), timeout=5.0)
            await instrument.stop()

        assert "software buffer overrun" in caplog.text
        assert len(published) == 1

    async def test_both_overruns_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        config = Mcc118Config(
            address=0,
            sample_rate=1000.0,
            channels=(Mcc118Channel(0, "ch0"),),
            source_id="daq01",
        )
        mock_module, mock_hat = _make_mock_daqhats()

        call_count = 0

        def mock_read(samples_per_channel: int, timeout: float) -> SimpleNamespace:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_scan_result([1.0], hardware_overrun=True, buffer_overrun=True)
            return _make_scan_result([], running=False)

        mock_hat.a_in_scan_read.side_effect = mock_read

        published: list[StreamData] = []
        publish_event = asyncio.Event()

        async def capture_publish(data: StreamData) -> None:
            published.append(data)
            publish_event.set()

        publisher = MagicMock()
        publisher.publish = capture_publish

        instrument = Mcc118Instrument(config, publisher)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            await instrument.start()
            await asyncio.wait_for(publish_event.wait(), timeout=5.0)
            await instrument.stop()

        assert "hardware buffer overrun" in caplog.text
        assert "software buffer overrun" in caplog.text


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class TestMcc118Identity:
    async def test_get_identity(self) -> None:
        config = Mcc118Config(
            address=0,
            sample_rate=1000.0,
            channels=(Mcc118Channel(0, "ch0"),),
            source_id="daq01",
        )
        mock_module, mock_hat = _make_mock_daqhats()
        mock_hat.serial.return_value = "SN12345"
        mock_hat.a_in_scan_read.return_value = _make_scan_result([], running=False)

        publisher = MagicMock()
        publisher.publish = AsyncMock()
        instrument = Mcc118Instrument(config, publisher)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            await instrument.start()
            identity = instrument.get_identity()
            await asyncio.sleep(0.1)
            await instrument.stop()

        assert identity.manufacturer == "Measurement Computing"
        assert identity.model == "MCC 118"
        assert identity.serial == "SN12345"
        assert identity.firmware == ""

    def test_get_identity_before_start(self) -> None:
        config = Mcc118Config(
            address=0,
            sample_rate=1000.0,
            channels=(Mcc118Channel(0, "ch0"),),
            source_id="daq01",
        )
        instrument = Mcc118Instrument(config, MagicMock())

        with pytest.raises(HwtestError, match="HAT not opened"):
            instrument.get_identity()


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


class TestCreateInstrument:
    def test_create_instrument(self) -> None:
        from hwtest_mcc.mcc118 import create_instrument

        publisher = MagicMock()
        instrument = create_instrument(
            address=1,
            sample_rate=500.0,
            channels=[
                {"id": 0, "name": "voltage_a"},
                {"id": 2, "name": "voltage_b"},
            ],
            source_id="test_daq",
            publisher=publisher,
        )

        assert instrument._config.address == 1
        assert instrument._config.sample_rate == 500.0
        assert len(instrument._config.channels) == 2
        assert instrument._config.channels[0].id == 0
        assert instrument._config.channels[0].name == "voltage_a"
        assert instrument._config.channels[1].id == 2
        assert instrument._config.channels[1].name == "voltage_b"
        assert instrument._config.source_id == "test_daq"
