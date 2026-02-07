"""Pi 4 + Waveshare simulator for hwtest integration testing.

This package provides a hardware simulator that runs on a Raspberry Pi 4
with Waveshare AD/DA board, used for integration testing when a real
Unit Under Test is not available. It simulates:

- CAN bus interface for message sending/receiving/echoing
- ADS1256 8-channel 24-bit ADC for analog input measurement
- DAC8532 2-channel 16-bit DAC for analog output generation
- MCP23017 16-bit I2C GPIO expander for digital I/O

The simulator exposes a REST API for remote control from the test rack.

Hardware Requirements:
    - Raspberry Pi 4 (recommended) or Pi Zero
    - Waveshare High-Precision AD/DA board (ADS1256 + DAC8532)
    - Optional: CAN HAT for CAN bus simulation
    - Optional: MCP23017 GPIO expander

Usage:
    Run as standalone service:
        pi4-waveshare-sim --port 8080

    Or programmatically:
        from hwtest_sim_pi4_waveshare import UutSimulator, SimulatorConfig
        sim = UutSimulator(config=SimulatorConfig())
        sim.start()
"""

from hwtest_sim_pi4_waveshare.ads1263 import (
    Ads1263,
    Ads1263Config,
    Ads1263DataRate,
    Ads1263Gain,
)
from hwtest_sim_pi4_waveshare.can_interface import CanConfig, CanInterface, CanMessage
from hwtest_sim_pi4_waveshare.mcp23017 import Mcp23017, Mcp23017Config, PinDirection
from hwtest_sim_pi4_waveshare.simulator import CanHeartbeatState, SimulatorConfig, UutSimulator

__version__ = "0.1.0"

__all__ = [
    # ADC
    "Ads1263",
    "Ads1263Config",
    "Ads1263DataRate",
    "Ads1263Gain",
    # CAN
    "CanConfig",
    "CanInterface",
    "CanMessage",
    # GPIO
    "Mcp23017",
    "Mcp23017Config",
    "PinDirection",
    # Simulator
    "CanHeartbeatState",
    "SimulatorConfig",
    "UutSimulator",
]
