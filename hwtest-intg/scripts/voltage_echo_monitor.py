"""Voltage echo: rack drives a voltage, the UUT reads it and echoes it back
on its DAC, the rack measures the echo (bounds in voltage_echo_monitor.yaml).

    python voltage_echo_monitor.py                     # one pass
    python voltage_echo_monitor.py --mode hass         # until failure / Ctrl-C
    python voltage_echo_monitor.py --emulate           # no hardware
"""

import hwtest
from _bench import drive_rack_dac, drive_uut_dac, read_rack_adc, read_uut_adc, zero_outputs

test = hwtest.Test("voltage_echo_monitor.yaml")


@test.setup
def start(ctx):
    ctx.log(f"UUT {ctx.uut.url}: {ctx.uut.health()['status']}")
    zero_outputs(ctx)


@test.step
def echo(ctx, state):
    settle = ctx.param("settling_time_seconds")
    drive_rack_dac(ctx, state.target_voltage)
    ctx.sleep(settle)
    drive_uut_dac(ctx, read_uut_adc(ctx))
    ctx.sleep(settle)
    ctx.measure(echo_voltage=read_rack_adc(ctx))


@test.teardown
def stop(ctx):
    zero_outputs(ctx)


if __name__ == "__main__":
    test.main()
