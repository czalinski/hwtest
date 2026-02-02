"""hwtest-uut: UUT (Unit Under Test) simulator for hardware integration testing.

This package provides a simulated unit under test that can be used for
integration testing of hardware test automation systems. It integrates:

- CAN bus interface for message sending/receiving/echoing
- DAC output for analog signal generation
- ADC input for analog signal measurement
- Digital I/O via MCP23017 GPIO expander

The simulator is controlled via a REST API over WiFi/Ethernet.
"""

from hwtest_uut.can_interface import CanConfig, CanInterface, CanMessage
from hwtest_uut.mcp23017 import Mcp23017, Mcp23017Config, PinDirection
from hwtest_uut.simulator import SimulatorConfig, UutSimulator

__version__ = "0.1.0"

__all__ = [
    # CAN
    "CanConfig",
    "CanInterface",
    "CanMessage",
    # GPIO
    "Mcp23017",
    "Mcp23017Config",
    "PinDirection",
    # Simulator
    "SimulatorConfig",
    "UutSimulator",
]
