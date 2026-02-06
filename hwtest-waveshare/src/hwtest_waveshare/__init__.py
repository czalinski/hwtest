"""Waveshare HAT instrument drivers for hwtest.

This package provides drivers for Waveshare HAT boards compatible with
Raspberry Pi (including Pi 5 via lgpio). The main components are:

Modules:
    ads1256: ADS1256 24-bit ADC driver (8 channels, 30 kSPS max).
    dac8532: DAC8532 16-bit DAC driver (2 channels, 0-5V output).
    gpio: GPIO abstraction layer for Raspberry Pi 5 compatibility.
    high_precision_ad_da: Unified instrument driver combining ADC and DAC.

Example:
    Basic usage with the High-Precision AD/DA board::

        from hwtest_waveshare import (
            HighPrecisionAdDaInstrument,
            HighPrecisionAdDaConfig,
            AdcChannel,
            DacChannel,
        )

        config = HighPrecisionAdDaConfig(
            source_id="sensor",
            adc_channels=(AdcChannel(id=0, name="voltage"),),
            dac_channels=(DacChannel(id=0, name="output"),),
        )
        instrument = HighPrecisionAdDaInstrument(config)
        await instrument.start()
        voltage = await instrument.read_voltage("voltage")
        await instrument.stop()
"""

from hwtest_waveshare.ads1256 import (
    Ads1256,
    Ads1256Config,
    Ads1256DataRate,
    Ads1256Gain,
    DATA_RATE_VALUES,
)
from hwtest_waveshare.dac8532 import (
    Dac8532,
    Dac8532Channel,
    Dac8532Config,
)
from hwtest_waveshare.high_precision_ad_da import (
    AdcChannel,
    DacChannel,
    HighPrecisionAdDaConfig,
    HighPrecisionAdDaInstrument,
    create_instrument as create_high_precision_ad_da,
)

__all__ = [
    # ADS1256 ADC
    "Ads1256",
    "Ads1256Config",
    "Ads1256DataRate",
    "Ads1256Gain",
    "DATA_RATE_VALUES",
    # DAC8532 DAC
    "Dac8532",
    "Dac8532Channel",
    "Dac8532Config",
    # High-Precision AD/DA instrument
    "AdcChannel",
    "DacChannel",
    "HighPrecisionAdDaConfig",
    "HighPrecisionAdDaInstrument",
    "create_high_precision_ad_da",
]
