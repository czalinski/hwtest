"""Waveshare HAT instrument drivers for hwtest."""

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
