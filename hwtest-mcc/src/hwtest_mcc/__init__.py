"""MCC DAQ HAT instrument drivers for hwtest.

This package provides instrument drivers for Measurement Computing (MCC) DAQ HAT
boards designed for Raspberry Pi and compatible single-board computers. These
drivers implement the hwtest streaming and identity interfaces for integration
with the test rack system.

Supported Hardware:
    - MCC 118: 8-channel voltage DAQ (+-10V, 100kS/s aggregate)
    - MCC 134: 4-channel thermocouple DAQ (Type J/K/T/E/R/S/B/N)
    - MCC 152: 8 digital I/O + 2 analog outputs (0-5V)

Example:
    Basic usage with the MCC 118 voltage DAQ::

        from hwtest_mcc import create_mcc118

        instrument = create_mcc118(
            address=0,
            sample_rate=1000.0,
            channels=[{"id": 0, "name": "voltage_in"}],
            source_id="my_daq",
        )
        instrument.open()
        identity = instrument.get_identity()
        print(f"Connected to {identity.model} (S/N: {identity.serial})")

Note:
    These drivers require the ``daqhats`` library from MCC, which is only
    available on Linux and requires hardware access to the SPI bus.
"""

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
from hwtest_mcc.scanner import HatInfo, scan_hats

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
    # Scanner
    "HatInfo",
    "scan_hats",
]
