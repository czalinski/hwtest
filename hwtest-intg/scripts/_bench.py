"""Bench wiring shared by the bench scripts (Pi 5 rack <-> Pi 4 UUT).

    rack_dac (MCC 152 AO0)  ->  UUT ADS1256 CH0
    UUT DAC8532 CH0         ->  rack_adc (MCC 118 address 4, CH0)

With ``--emulate`` there are no wires, so these helpers copy the voltages
across by hand (the UUT simulator must run with ``--emulate``).
"""

from __future__ import annotations

from hwtest import Context

MCC118_ADDRESS = 4
MCC118_RACK_ADC_CHANNEL = 0
UUT_ADC_CHANNEL = 0
UUT_DAC_CHANNEL = 0


def uut_adc_scale(ctx: Context) -> float:
    """UUT ADC calibration factor (ideally ~1.0)."""
    return ctx.calibration("uut_adc_scale_factor", 1.0)


def rack_adc_scale(ctx: Context) -> float:
    """MCC 118 calibration."""
    return ctx.calibration("mcc118_scale_factor", 1.0)


def drive_rack_dac(ctx: Context, volts: float) -> None:
    """Rack output toward the UUT ADC."""
    ctx.rack.write("rack_dac", volts)
    if ctx.rack.emulated:
        ctx.uut.emu_adc_input(UUT_ADC_CHANNEL, volts / uut_adc_scale(ctx))


def drive_uut_dac(ctx: Context, volts: float) -> None:
    """UUT output toward the rack ADC (clamped to the DAC's 0-5 V)."""
    ctx.uut.dac_write(UUT_DAC_CHANNEL, min(max(volts, 0.0), 5.0))
    if ctx.rack.emulated:
        ctx.rack.emu_set_analog_input(
            MCC118_ADDRESS, MCC118_RACK_ADC_CHANNEL, ctx.uut.emu_dac_output(UUT_DAC_CHANNEL)
        )


def read_uut_adc(ctx: Context) -> float:
    """Calibrated UUT ADC voltage."""
    return ctx.uut.adc_read(UUT_ADC_CHANNEL) * uut_adc_scale(ctx)


def read_rack_adc(ctx: Context) -> float:
    """Calibrated rack ADC voltage."""
    return float(ctx.rack.read("rack_adc")) * rack_adc_scale(ctx)


def zero_outputs(ctx: Context) -> None:
    """Both outputs to 0 V (teardown)."""
    drive_rack_dac(ctx, 0.0)
    drive_uut_dac(ctx, 0.0)
