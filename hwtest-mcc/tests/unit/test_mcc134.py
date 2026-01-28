"""Unit tests for MCC 134 thermocouple instrument driver."""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hwtest_core.errors import HwtestError
from hwtest_core.types.common import DataType, SourceId
from hwtest_core.types.streaming import StreamData, StreamField

from hwtest_mcc.mcc134 import (
    Mcc134Channel,
    Mcc134Config,
    Mcc134Instrument,
    ThermocoupleType,
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
    mock_module.mcc134.return_value = mock_hat
    return mock_module, mock_hat


# ---------------------------------------------------------------------------
# ThermocoupleType tests
# ---------------------------------------------------------------------------


class TestThermocoupleType:
    def test_type_values(self) -> None:
        assert ThermocoupleType.TYPE_J.value == 0
        assert ThermocoupleType.TYPE_K.value == 1
        assert ThermocoupleType.TYPE_T.value == 2
        assert ThermocoupleType.TYPE_E.value == 3
        assert ThermocoupleType.TYPE_R.value == 4
        assert ThermocoupleType.TYPE_S.value == 5
        assert ThermocoupleType.TYPE_B.value == 6
        assert ThermocoupleType.TYPE_N.value == 7
        assert ThermocoupleType.DISABLED.value == 255


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestMcc134Channel:
    def test_create(self) -> None:
        ch = Mcc134Channel(id=0, name="temp1", tc_type=ThermocoupleType.TYPE_K)
        assert ch.id == 0
        assert ch.name == "temp1"
        assert ch.tc_type == ThermocoupleType.TYPE_K

    def test_frozen(self) -> None:
        ch = Mcc134Channel(id=0, name="temp1", tc_type=ThermocoupleType.TYPE_K)
        with pytest.raises(AttributeError):
            ch.id = 1  # type: ignore[misc]


class TestMcc134Config:
    def test_valid(self) -> None:
        config = Mcc134Config(
            address=0,
            channels=(Mcc134Channel(0, "ch0", ThermocoupleType.TYPE_K),),
            source_id="tc01",
            update_interval=0.5,
        )
        assert config.address == 0
        assert len(config.channels) == 1
        assert config.source_id == "tc01"
        assert config.update_interval == 0.5

    def test_default_update_interval(self) -> None:
        config = Mcc134Config(
            address=0,
            channels=(Mcc134Channel(0, "ch0", ThermocoupleType.TYPE_K),),
            source_id="tc01",
        )
        assert config.update_interval == 1.0

    def test_address_too_low(self) -> None:
        with pytest.raises(ValueError, match="address"):
            Mcc134Config(
                address=-1,
                channels=(Mcc134Channel(0, "ch0", ThermocoupleType.TYPE_K),),
                source_id="tc01",
            )

    def test_address_too_high(self) -> None:
        with pytest.raises(ValueError, match="address"):
            Mcc134Config(
                address=8,
                channels=(Mcc134Channel(0, "ch0", ThermocoupleType.TYPE_K),),
                source_id="tc01",
            )

    def test_update_interval_zero(self) -> None:
        with pytest.raises(ValueError, match="update_interval"):
            Mcc134Config(
                address=0,
                channels=(Mcc134Channel(0, "ch0", ThermocoupleType.TYPE_K),),
                source_id="tc01",
                update_interval=0.0,
            )

    def test_update_interval_negative(self) -> None:
        with pytest.raises(ValueError, match="update_interval"):
            Mcc134Config(
                address=0,
                channels=(Mcc134Channel(0, "ch0", ThermocoupleType.TYPE_K),),
                source_id="tc01",
                update_interval=-1.0,
            )

    def test_empty_channels(self) -> None:
        with pytest.raises(ValueError, match="channels"):
            Mcc134Config(
                address=0,
                channels=(),
                source_id="tc01",
            )

    def test_channel_id_too_low(self) -> None:
        with pytest.raises(ValueError, match="channel id"):
            Mcc134Config(
                address=0,
                channels=(Mcc134Channel(-1, "ch0", ThermocoupleType.TYPE_K),),
                source_id="tc01",
            )

    def test_channel_id_too_high(self) -> None:
        with pytest.raises(ValueError, match="channel id"):
            Mcc134Config(
                address=0,
                channels=(Mcc134Channel(4, "ch0", ThermocoupleType.TYPE_K),),
                source_id="tc01",
            )

    def test_duplicate_channel_id(self) -> None:
        with pytest.raises(ValueError, match="duplicate channel id"):
            Mcc134Config(
                address=0,
                channels=(
                    Mcc134Channel(0, "ch0", ThermocoupleType.TYPE_K),
                    Mcc134Channel(0, "ch1", ThermocoupleType.TYPE_J),
                ),
                source_id="tc01",
            )

    def test_duplicate_channel_name(self) -> None:
        with pytest.raises(ValueError, match="duplicate channel name"):
            Mcc134Config(
                address=0,
                channels=(
                    Mcc134Channel(0, "same", ThermocoupleType.TYPE_K),
                    Mcc134Channel(1, "same", ThermocoupleType.TYPE_J),
                ),
                source_id="tc01",
            )


# ---------------------------------------------------------------------------
# Schema construction
# ---------------------------------------------------------------------------


class TestSchemaConstruction:
    def test_schema_fields_match_channels(self) -> None:
        config = Mcc134Config(
            address=0,
            channels=(
                Mcc134Channel(0, "chamber_temp", ThermocoupleType.TYPE_K),
                Mcc134Channel(2, "dut_temp", ThermocoupleType.TYPE_T),
            ),
            source_id="tc01",
        )
        publisher = MagicMock()
        instrument = Mcc134Instrument(config, publisher)

        schema = instrument.schema
        assert schema.source_id == SourceId("tc01")
        assert len(schema.fields) == 2
        assert schema.fields[0] == StreamField("chamber_temp", DataType.F64, "degC")
        assert schema.fields[1] == StreamField("dut_temp", DataType.F64, "degC")

    def test_schema_single_channel(self) -> None:
        config = Mcc134Config(
            address=3,
            channels=(Mcc134Channel(1, "probe", ThermocoupleType.TYPE_J),),
            source_id="sensor01",
        )
        publisher = MagicMock()
        instrument = Mcc134Instrument(config, publisher)

        schema = instrument.schema
        assert schema.source_id == SourceId("sensor01")
        assert len(schema.fields) == 1
        assert schema.fields[0].name == "probe"
        assert schema.fields[0].dtype == DataType.F64
        assert schema.fields[0].unit == "degC"


# ---------------------------------------------------------------------------
# Instrument lifecycle
# ---------------------------------------------------------------------------


class TestMcc134Instrument:
    async def test_initial_state(self) -> None:
        config = Mcc134Config(
            address=0,
            channels=(Mcc134Channel(0, "ch0", ThermocoupleType.TYPE_K),),
            source_id="tc01",
        )
        instrument = Mcc134Instrument(config, MagicMock())
        assert not instrument.is_running

    async def test_start_stop(self) -> None:
        config = Mcc134Config(
            address=0,
            channels=(Mcc134Channel(0, "ch0", ThermocoupleType.TYPE_K),),
            source_id="tc01",
            update_interval=0.01,
        )
        mock_module, mock_hat = _make_mock_daqhats()
        mock_hat.t_in_read.return_value = 25.0

        publisher = MagicMock()
        publisher.publish = AsyncMock()
        instrument = Mcc134Instrument(config, publisher)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            await instrument.start()
            assert instrument.is_running

            # Let poll loop run once
            await asyncio.sleep(0.05)
            await instrument.stop()

        assert not instrument.is_running
        mock_module.mcc134.assert_called_once_with(0)
        mock_hat.tc_type_write.assert_called_once_with(0, ThermocoupleType.TYPE_K.value)

    async def test_start_is_idempotent(self) -> None:
        config = Mcc134Config(
            address=0,
            channels=(Mcc134Channel(0, "ch0", ThermocoupleType.TYPE_K),),
            source_id="tc01",
            update_interval=0.01,
        )
        mock_module, mock_hat = _make_mock_daqhats()
        mock_hat.t_in_read.return_value = 25.0

        publisher = MagicMock()
        publisher.publish = AsyncMock()
        instrument = Mcc134Instrument(config, publisher)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            await instrument.start()
            await instrument.start()  # second call should be a no-op
            await asyncio.sleep(0.02)
            await instrument.stop()

        # HAT constructor called only once
        mock_module.mcc134.assert_called_once()

    async def test_stop_when_not_running(self) -> None:
        config = Mcc134Config(
            address=0,
            channels=(Mcc134Channel(0, "ch0", ThermocoupleType.TYPE_K),),
            source_id="tc01",
        )
        instrument = Mcc134Instrument(config, MagicMock())
        await instrument.stop()  # should not raise

    async def test_daqhats_not_installed(self) -> None:
        config = Mcc134Config(
            address=0,
            channels=(Mcc134Channel(0, "ch0", ThermocoupleType.TYPE_K),),
            source_id="tc01",
        )
        instrument = Mcc134Instrument(config, MagicMock())

        with patch.dict(sys.modules, {"daqhats": None}):
            with pytest.raises(HwtestError, match="daqhats"):
                await instrument.start()

    async def test_hat_not_found(self) -> None:
        config = Mcc134Config(
            address=5,
            channels=(Mcc134Channel(0, "ch0", ThermocoupleType.TYPE_K),),
            source_id="tc01",
        )
        mock_module = MagicMock()
        mock_module.mcc134.side_effect = Exception("No HAT at address 5")

        instrument = Mcc134Instrument(config, MagicMock())

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            with pytest.raises(HwtestError, match="Failed to open MCC 134"):
                await instrument.start()


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class TestIdentity:
    async def test_get_identity(self) -> None:
        config = Mcc134Config(
            address=0,
            channels=(Mcc134Channel(0, "ch0", ThermocoupleType.TYPE_K),),
            source_id="tc01",
            update_interval=0.01,
        )
        mock_module, mock_hat = _make_mock_daqhats()
        mock_hat.serial.return_value = "ABC123"
        mock_hat.t_in_read.return_value = 25.0

        publisher = MagicMock()
        publisher.publish = AsyncMock()
        instrument = Mcc134Instrument(config, publisher)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            await instrument.start()
            identity = instrument.get_identity()
            await instrument.stop()

        assert identity.manufacturer == "Measurement Computing"
        assert identity.model == "MCC 134"
        assert identity.serial == "ABC123"
        assert identity.firmware == ""

    def test_get_identity_before_start(self) -> None:
        config = Mcc134Config(
            address=0,
            channels=(Mcc134Channel(0, "ch0", ThermocoupleType.TYPE_K),),
            source_id="tc01",
        )
        instrument = Mcc134Instrument(config, MagicMock())

        with pytest.raises(HwtestError, match="HAT not opened"):
            instrument.get_identity()


# ---------------------------------------------------------------------------
# Data publishing
# ---------------------------------------------------------------------------


class TestDataPublishing:
    async def test_publishes_temperature_data(self) -> None:
        config = Mcc134Config(
            address=0,
            channels=(
                Mcc134Channel(0, "temp_a", ThermocoupleType.TYPE_K),
                Mcc134Channel(1, "temp_b", ThermocoupleType.TYPE_K),
            ),
            source_id="tc01",
            update_interval=0.01,
        )
        mock_module, mock_hat = _make_mock_daqhats()

        # Return different temperatures for each channel
        mock_hat.t_in_read.side_effect = [25.5, 30.2]

        published: list[StreamData] = []
        publish_event = asyncio.Event()

        async def capture_publish(data: StreamData) -> None:
            published.append(data)
            publish_event.set()

        publisher = MagicMock()
        publisher.publish = capture_publish

        instrument = Mcc134Instrument(config, publisher)

        with patch.dict(sys.modules, {"daqhats": mock_module}):
            await instrument.start()
            await asyncio.wait_for(publish_event.wait(), timeout=5.0)
            await instrument.stop()

        assert len(published) == 1
        data = published[0]
        assert data.schema_id == instrument.schema.schema_id
        assert data.period_ns == 10_000_000  # 0.01s in ns
        assert len(data.samples) == 1
        assert data.samples[0] == (25.5, 30.2)


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


class TestCreateInstrument:
    def test_create_with_defaults(self) -> None:
        publisher = MagicMock()
        instrument = create_instrument(
            address=4,
            channels=[
                {"id": 0, "name": "probe1"},
                {"id": 1, "name": "probe2", "tc_type": "TYPE_J"},
            ],
            source_id="tc01",
            publisher=publisher,
        )

        assert instrument._config.address == 4
        assert instrument._config.update_interval == 1.0
        assert len(instrument._config.channels) == 2
        assert instrument._config.channels[0].tc_type == ThermocoupleType.TYPE_K
        assert instrument._config.channels[1].tc_type == ThermocoupleType.TYPE_J

    def test_create_with_custom_interval(self) -> None:
        publisher = MagicMock()
        instrument = create_instrument(
            address=0,
            channels=[{"id": 0, "name": "temp"}],
            source_id="tc01",
            publisher=publisher,
            update_interval=0.5,
        )

        assert instrument._config.update_interval == 0.5
