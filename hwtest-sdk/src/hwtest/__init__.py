"""hwtest: write hardware test cases in plain Python.

A test case is a YAML definition (states, parameters, bounds) plus a short
Python script with the hardware steps. Everything else - running the state
sequence in functional/HASS/HALT mode, checking measurements against the YAML
bounds, logging, NATS publishing, results database, stopping cleanly on Ctrl-C
- is done by the framework::

    import hwtest

    test = hwtest.Test("voltage_echo_monitor.yaml")

    @test.step
    def echo(ctx, state):
        ctx.rack.write("rack_dac", state.target_voltage)
        ctx.sleep(ctx.param("settling_time_seconds"))
        ctx.measure(echo_voltage=ctx.rack.read("rack_adc"))

    if __name__ == "__main__":
        test.main()

Run it with ``python voltage_echo.py --mode hass`` (``--help`` lists options).
"""

from hwtest_core._native import HardwareError, Rack
from hwtest_core.errors import SpecError

from hwtest.context import Context, State
from hwtest.test import RunSummary, Test

__version__ = "0.1.0"

__all__ = [
    "Context",
    "HardwareError",
    "Rack",
    "RunSummary",
    "SpecError",
    "State",
    "Test",
]
