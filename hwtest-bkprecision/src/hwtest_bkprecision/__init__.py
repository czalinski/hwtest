"""BK Precision DC power supply driver and emulator for hwtest.

This package provides drivers and emulators for BK Precision 9100 series
DC power supplies, including single-channel (9115) and multi-channel (9130B)
models.

Modules:
    psu: High-level driver for single-channel PSU operations.
    psu_channel: Multi-channel wrapper with logical channel naming.
    emulator: In-process SCPI emulator for testing without hardware.
    server: TCP server for exposing emulators to external tools.

Example:
    Connect to a real instrument::

        from hwtest_bkprecision import create_instrument

        psu = create_instrument("TCPIP::192.168.1.100::5025::SOCKET")
        psu.set_voltage(12.0)
        psu.enable_output()

    Use an emulator for testing::

        from hwtest_bkprecision import make_9115_emulator, EmulatorServer

        emulator = make_9115_emulator()
        server = EmulatorServer(emulator, port=0)
        server.start()
"""

from hwtest_bkprecision.emulator import (
    BkDcPsuEmulator,
    BkDcPsuEmulatorConfig,
    make_9115_emulator,
    make_9130b_emulator,
)
from hwtest_bkprecision.psu import BkDcPsu, create_instrument
from hwtest_bkprecision.psu_channel import (
    BkDcPsuChannel,
    BkMultiChannelPsu,
    PsuChannelConfig,
    create_multichannel_instrument,
)
from hwtest_bkprecision.server import EmulatorServer

__all__ = [
    # Emulator
    "BkDcPsuEmulator",
    "BkDcPsuEmulatorConfig",
    "make_9115_emulator",
    "make_9130b_emulator",
    # Single-channel driver
    "BkDcPsu",
    "create_instrument",
    # Multi-channel driver with logical naming
    "BkDcPsuChannel",
    "BkMultiChannelPsu",
    "PsuChannelConfig",
    "create_multichannel_instrument",
    # Server
    "EmulatorServer",
]
