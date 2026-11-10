"""Example test case: drive a voltage, measure it back."""
import hwtest

test = hwtest.Test("loop_test.yaml")


@test.setup
def power_on(ctx):
    ctx.rack.psu_apply("vin", 12.0, 0.5)
    ctx.rack.psu_output("vin", True)
    if ctx.rack.emulated:
        ctx.rack.emu_connect_analog(0, 0, 4, 0, gain=ctx.param("gain"))


@test.step
def loop(ctx, state):
    ctx.rack.write("stimulus", state.target_voltage)
    ctx.sleep(ctx.param("settle_s"))
    ctx.measure(response=ctx.rack.read("response"), target=state.target_voltage)


@test.teardown
def power_off(ctx):
    ctx.rack.psu_output("vin", False)
    ctx.rack.write("stimulus", 0.0)


if __name__ == "__main__":
    test.main()
