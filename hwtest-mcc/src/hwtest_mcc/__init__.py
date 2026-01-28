"""MCC DAQ HAT instrument drivers for hwtest."""

from hwtest_mcc.mcc118 import (
    Mcc118Channel,
    Mcc118Config,
    Mcc118Instrument,
    create_instrument as create_mcc118,
)
from hwtest_mcc.mcc134 import (
    Mcc134Channel,
    Mcc134Config,
    Mcc134Instrument,
    ThermocoupleType,
    create_instrument as create_mcc134,
)
from hwtest_mcc.mcc152 import (
    DioDirection,
    Mcc152AnalogChannel,
    Mcc152Config,
    Mcc152DioChannel,
    Mcc152Instrument,
    create_instrument as create_mcc152,
)

__all__ = [
    # MCC 118
    "Mcc118Channel",
    "Mcc118Config",
    "Mcc118Instrument",
    "create_mcc118",
    # MCC 134
    "Mcc134Channel",
    "Mcc134Config",
    "Mcc134Instrument",
    "ThermocoupleType",
    "create_mcc134",
    # MCC 152
    "DioDirection",
    "Mcc152AnalogChannel",
    "Mcc152Config",
    "Mcc152DioChannel",
    "Mcc152Instrument",
    "create_mcc152",
]
