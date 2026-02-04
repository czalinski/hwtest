"""BK Precision DC power supply driver and emulator for hwtest."""

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
