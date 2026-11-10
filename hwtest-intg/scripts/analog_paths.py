"""Analog paths, each direction on its own (bounds in analog_paths.yaml)."""

import hwtest
from _bench import drive_rack_dac, drive_uut_dac, read_rack_adc, read_uut_adc, zero_outputs

test = hwtest.Test("analog_paths.yaml")


@test.step
def paths(ctx, state):
    target = state.target_voltage
    settle = ctx.param("settling_time_seconds")

    drive_rack_dac(ctx, target)
    ctx.sleep(settle)
    rack_to_uut_error = read_uut_adc(ctx) - target

    drive_uut_dac(ctx, target)
    ctx.sleep(settle)
    uut_to_rack_error = read_rack_adc(ctx) - target

    ctx.measure(rack_to_uut_error=rack_to_uut_error, uut_to_rack_error=uut_to_rack_error)


@test.teardown
def stop(ctx):
    zero_outputs(ctx)


if __name__ == "__main__":
    test.main()
