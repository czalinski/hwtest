"""Unit tests for the High-Precision AD/DA instrument."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from hwtest_waveshare.ads1256 import Ads1256DataRate, Ads1256Gain
from hwtest_waveshare.high_precision_ad_da import (
    AdcChannel,
    DacChannel,
    HighPrecisionAdDaConfig,
    HighPrecisionAdDaInstrument,
    create_instrument,
)


class TestAdcChannel:
    """Tests for the AdcChannel dataclass."""

    def test_single_ended_channel(self) -> None:
        """Single-ended channel has no differential_negative."""
        ch = AdcChannel(id=0, name="voltage0")
        assert ch.id == 0
        assert ch.name == "voltage0"
        assert ch.differential_negative is None

    def test_differential_channel(self) -> None:
        """Differential channel specifies negative input."""
        ch = AdcChannel(id=0, name="diff01", differential_negative=1)
        assert ch.id == 0
        assert ch.name == "diff01"
        assert ch.differential_negative == 1


class TestDacChannel:
    """Tests for the DacChannel dataclass."""

    def test_default_initial_voltage(self) -> None:
        """Default initial voltage is 0.0."""
        ch = DacChannel(id=0, name="out_a")
        assert ch.id == 0
        assert ch.name == "out_a"
        assert ch.initial_voltage == 0.0

    def test_custom_initial_voltage(self) -> None:
        """Custom initial voltage is stored."""
        ch = DacChannel(id=1, name="out_b", initial_voltage=2.5)
        assert ch.initial_voltage == 2.5


class TestHighPrecisionAdDaConfig:
    """Tests for the HighPrecisionAdDaConfig dataclass."""

    def test_default_config(self) -> None:
        """Default configuration uses expected values."""
        config = HighPrecisionAdDaConfig(source_id="test")
        assert config.source_id == "test"
        assert config.adc_channels == ()
        assert config.dac_channels == ()
        assert config.adc_gain == Ads1256Gain.GAIN_1
        assert config.adc_data_rate == Ads1256DataRate.SPS_100
        assert config.adc_vref == 2.5
        assert config.dac_vref == 5.0

    def test_config_with_channels(self) -> None:
        """Configuration with channels stores them correctly."""
        adc_ch = (AdcChannel(id=0, name="v0"), AdcChannel(id=1, name="v1"))
        dac_ch = (DacChannel(id=0, name="out0"),)
        config = HighPrecisionAdDaConfig(
            source_id="test",
            adc_channels=adc_ch,
            dac_channels=dac_ch,
        )
        assert len(config.adc_channels) == 2
        assert len(config.dac_channels) == 1

    def test_invalid_adc_channel_id_raises(self) -> None:
        """ADC channel ID out of range raises ValueError."""
        with pytest.raises(ValueError, match="ADC channel id must be 0-7"):
            HighPrecisionAdDaConfig(
                source_id="test",
                adc_channels=(AdcChannel(id=8, name="bad"),),
            )

    def test_duplicate_adc_channel_id_raises(self) -> None:
        """Duplicate ADC channel IDs raise ValueError."""
        with pytest.raises(ValueError, match="duplicate ADC channel id"):
            HighPrecisionAdDaConfig(
                source_id="test",
                adc_channels=(
                    AdcChannel(id=0, name="v0"),
                    AdcChannel(id=0, name="v1"),
                ),
            )

    def test_duplicate_channel_name_raises(self) -> None:
        """Duplicate channel names raise ValueError."""
        with pytest.raises(ValueError, match="duplicate channel name"):
            HighPrecisionAdDaConfig(
                source_id="test",
                adc_channels=(
                    AdcChannel(id=0, name="voltage"),
                    AdcChannel(id=1, name="voltage"),
                ),
            )

    def test_invalid_dac_channel_id_raises(self) -> None:
        """DAC channel ID out of range raises ValueError."""
        with pytest.raises(ValueError, match="DAC channel id must be 0 or 1"):
            HighPrecisionAdDaConfig(
                source_id="test",
                dac_channels=(DacChannel(id=2, name="bad"),),
            )

    def test_invalid_initial_voltage_raises(self) -> None:
        """Invalid initial voltage raises ValueError."""
        with pytest.raises(ValueError, match="initial_voltage must be"):
            HighPrecisionAdDaConfig(
                source_id="test",
                dac_channels=(DacChannel(id=0, name="out", initial_voltage=6.0),),
            )


class TestHighPrecisionAdDaInstrument:
    """Tests for the HighPrecisionAdDaInstrument class."""

    def test_not_running_initially(self) -> None:
        """Instrument is not running when created."""
        config = HighPrecisionAdDaConfig(source_id="test")
        instrument = HighPrecisionAdDaInstrument(config)
        assert not instrument.is_running

    def test_sample_rate_property(self) -> None:
        """sample_rate property returns configured data rate."""
        config = HighPrecisionAdDaConfig(
            source_id="test",
            adc_data_rate=Ads1256DataRate.SPS_1000,
        )
        instrument = HighPrecisionAdDaInstrument(config)
        assert instrument.actual_sample_rate == 1000.0

    def test_schema_includes_adc_channels(self) -> None:
        """Stream schema includes all ADC channel names."""
        config = HighPrecisionAdDaConfig(
            source_id="test",
            adc_channels=(
                AdcChannel(id=0, name="voltage_a"),
                AdcChannel(id=1, name="voltage_b"),
            ),
        )
        instrument = HighPrecisionAdDaInstrument(config)

        schema = instrument.schema
        assert schema.source_id == "test"
        assert len(schema.fields) == 2
        assert schema.fields[0].name == "voltage_a"
        assert schema.fields[1].name == "voltage_b"

    def test_get_identity_before_start(self) -> None:
        """get_identity returns basic identity without serial before start."""
        config = HighPrecisionAdDaConfig(source_id="test")
        instrument = HighPrecisionAdDaInstrument(config)

        identity = instrument.get_identity()
        assert identity.manufacturer == "Waveshare"
        assert identity.model == "High-Precision AD/DA"
        assert identity.serial == ""


class TestCreateInstrument:
    """Tests for the create_instrument factory function."""

    def test_create_with_minimal_args(self) -> None:
        """create_instrument works with minimal arguments."""
        instrument = create_instrument(source_id="test")
        assert instrument is not None
        assert instrument.schema.source_id == "test"

    def test_create_with_adc_channels(self) -> None:
        """create_instrument parses ADC channels correctly."""
        instrument = create_instrument(
            source_id="test",
            adc_channels=[
                {"id": 0, "name": "ch0"},
                {"id": 1, "name": "ch1", "differential_negative": 2},
            ],
        )
        schema = instrument.schema
        assert len(schema.fields) == 2
        assert schema.fields[0].name == "ch0"
        assert schema.fields[1].name == "ch1"

    def test_create_with_dac_channels(self) -> None:
        """create_instrument parses DAC channels correctly."""
        instrument = create_instrument(
            source_id="test",
            dac_channels=[
                {"id": 0, "name": "out_a"},
                {"id": 1, "name": "out_b", "initial_voltage": 2.5},
            ],
        )
        # DAC channels don't affect schema, but should be configured
        assert instrument is not None

    def test_create_with_gain_string(self) -> None:
        """create_instrument accepts gain as string."""
        instrument = create_instrument(
            source_id="test",
            adc_gain="GAIN_8",
        )
        assert instrument is not None

    def test_create_with_data_rate_string(self) -> None:
        """create_instrument accepts data rate as string."""
        instrument = create_instrument(
            source_id="test",
            adc_data_rate="SPS_1000",
        )
        assert instrument.actual_sample_rate == 1000.0

    def test_create_with_custom_pins(self) -> None:
        """create_instrument accepts custom GPIO pins."""
        instrument = create_instrument(
            source_id="test",
            adc_cs_pin=25,
            adc_drdy_pin=24,
            adc_reset_pin=None,
            dac_cs_pin=26,
        )
        assert instrument is not None

    def test_create_with_custom_vref(self) -> None:
        """create_instrument accepts custom reference voltages."""
        instrument = create_instrument(
            source_id="test",
            adc_vref=3.3,
            dac_vref=3.3,
        )
        assert instrument is not None

    def test_create_with_publisher(self) -> None:
        """create_instrument accepts a stream publisher."""
        mock_publisher = AsyncMock()
        instrument = create_instrument(
            source_id="test",
            publisher=mock_publisher,
        )
        assert instrument is not None
