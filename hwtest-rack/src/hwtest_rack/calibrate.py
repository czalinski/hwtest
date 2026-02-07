"""Automated calibration for rack instruments.

This module provides calibration routines that use known reference sources
to calculate and store calibration factors for a rack instance.

Calibration Procedure (MCC 118 using MCC 152 DAC reference):
    1. Output known voltages from MCC 152 DAC (0-5V range)
    2. Read back on MCC 118 ADC
    3. Calculate scale factor: output_voltage / measured_voltage
    4. Average across multiple reference points
    5. Store in rack instance configuration

Usage:
    from hwtest_rack.calibrate import calibrate_mcc118

    # Run calibration
    result = calibrate_mcc118(
        mcc152_address=0,
        mcc152_channel=0,
        mcc118_address=4,
        mcc118_channel=0,
        reference_voltages=[1.0, 2.5, 4.0],
    )

    # Save to rack instance config
    instance_config.calibration["mcc118_scale_factor"] = result.scale_factor
    instance_config.save()
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CalibrationPoint:
    """A single calibration measurement point.

    Attributes:
        reference_voltage: The known reference voltage (V).
        measured_voltage: The raw measured voltage (V).
        scale_factor: Calculated scale factor for this point.
    """

    reference_voltage: float
    measured_voltage: float
    scale_factor: float


@dataclass
class CalibrationResult:
    """Result of a calibration procedure.

    Attributes:
        scale_factor: The calculated scale factor (average across points).
        points: Individual calibration point measurements.
        reference_instrument: Instrument used as reference.
        timestamp: When calibration was performed.
        success: Whether calibration completed successfully.
        error: Error message if calibration failed.
    """

    scale_factor: float
    points: tuple[CalibrationPoint, ...]
    reference_instrument: str
    timestamp: str
    success: bool = True
    error: str = ""

    @property
    def notes(self) -> str:
        """Generate calibration notes from points."""
        if not self.points:
            return "No calibration points"
        voltages = ", ".join(f"{p.reference_voltage}V" for p in self.points)
        return f"Calibrated using {voltages} reference points"


def calibrate_mcc118(
    mcc152_address: int = 0,
    mcc152_channel: int = 0,
    mcc118_address: int = 4,
    mcc118_channel: int = 0,
    reference_voltages: list[float] | None = None,
    settling_time: float = 0.1,
    samples_per_point: int = 10,
) -> CalibrationResult:
    """Calibrate MCC 118 ADC using MCC 152 DAC as reference.

    Outputs known voltages from MCC 152 DAC and measures them with MCC 118 ADC
    to calculate a scale factor that corrects for signal path attenuation.

    Args:
        mcc152_address: MCC 152 HAT address (0-7).
        mcc152_channel: MCC 152 analog output channel (0 or 1).
        mcc118_address: MCC 118 HAT address (0-7).
        mcc118_channel: MCC 118 analog input channel (0-7).
        reference_voltages: Voltages to use as reference points.
            Default: [1.0, 2.5, 4.0] covering the useful range.
        settling_time: Time to wait after setting voltage (seconds).
        samples_per_point: Number of samples to average per point.

    Returns:
        CalibrationResult with calculated scale factor.
    """
    if reference_voltages is None:
        reference_voltages = [1.0, 2.5, 4.0]

    timestamp = datetime.now(timezone.utc).isoformat()

    # Try to import daqhats
    try:
        import daqhats  # type: ignore[import-not-found]
    except ImportError:
        return CalibrationResult(
            scale_factor=1.0,
            points=(),
            reference_instrument="mcc152",
            timestamp=timestamp,
            success=False,
            error="daqhats library not installed",
        )

    # Initialize HATs
    try:
        mcc152 = daqhats.mcc152(mcc152_address)
        mcc118 = daqhats.mcc118(mcc118_address)
    except Exception as exc:
        return CalibrationResult(
            scale_factor=1.0,
            points=(),
            reference_instrument="mcc152",
            timestamp=timestamp,
            success=False,
            error=f"Failed to initialize HATs: {exc}",
        )

    points: list[CalibrationPoint] = []

    try:
        for ref_voltage in reference_voltages:
            logger.info(f"Calibrating at {ref_voltage}V...")

            # Set reference voltage
            mcc152.a_out_write(mcc152_channel, ref_voltage)
            time.sleep(settling_time)

            # Take multiple samples and average
            samples: list[float] = []
            for _ in range(samples_per_point):
                reading = mcc118.a_in_read(mcc118_channel)
                samples.append(reading)
                time.sleep(0.01)  # 10ms between samples

            measured = sum(samples) / len(samples)

            # Calculate scale factor for this point
            if measured > 0.001:  # Avoid division by near-zero
                point_scale = ref_voltage / measured
            else:
                logger.warning(f"Very low reading at {ref_voltage}V: {measured}V")
                point_scale = 1.0

            logger.info(
                f"  Reference: {ref_voltage:.3f}V, "
                f"Measured: {measured:.3f}V, "
                f"Scale: {point_scale:.4f}"
            )

            points.append(
                CalibrationPoint(
                    reference_voltage=ref_voltage,
                    measured_voltage=measured,
                    scale_factor=point_scale,
                )
            )

    finally:
        # Reset DAC to 0V
        try:
            mcc152.a_out_write(mcc152_channel, 0.0)
        except Exception:
            pass

    # Calculate average scale factor
    if points:
        avg_scale = sum(p.scale_factor for p in points) / len(points)
    else:
        avg_scale = 1.0

    logger.info(f"Calibration complete. Average scale factor: {avg_scale:.4f}")

    return CalibrationResult(
        scale_factor=avg_scale,
        points=tuple(points),
        reference_instrument="mcc152",
        timestamp=timestamp,
        success=True,
    )


def calibrate_with_external_reference(
    mcc118_address: int = 4,
    mcc118_channel: int = 0,
    reference_voltage: float = 2.5,
    samples: int = 100,
) -> CalibrationResult:
    """Calibrate MCC 118 using an external known voltage reference.

    Use this when a calibrated external voltage source is available.
    The user must apply the reference voltage before calling this function.

    Args:
        mcc118_address: MCC 118 HAT address (0-7).
        mcc118_channel: MCC 118 analog input channel (0-7).
        reference_voltage: The known voltage being applied (V).
        samples: Number of samples to average.

    Returns:
        CalibrationResult with calculated scale factor.
    """
    timestamp = datetime.now(timezone.utc).isoformat()

    try:
        import daqhats  # type: ignore[import-not-found]
    except ImportError:
        return CalibrationResult(
            scale_factor=1.0,
            points=(),
            reference_instrument="external",
            timestamp=timestamp,
            success=False,
            error="daqhats library not installed",
        )

    try:
        mcc118 = daqhats.mcc118(mcc118_address)
    except Exception as exc:
        return CalibrationResult(
            scale_factor=1.0,
            points=(),
            reference_instrument="external",
            timestamp=timestamp,
            success=False,
            error=f"Failed to initialize MCC 118: {exc}",
        )

    # Take samples
    readings: list[float] = []
    for _ in range(samples):
        reading = mcc118.a_in_read(mcc118_channel)
        readings.append(reading)
        time.sleep(0.01)

    measured = sum(readings) / len(readings)

    if measured > 0.001:
        scale_factor = reference_voltage / measured
    else:
        scale_factor = 1.0

    point = CalibrationPoint(
        reference_voltage=reference_voltage,
        measured_voltage=measured,
        scale_factor=scale_factor,
    )

    logger.info(
        f"External calibration: Reference={reference_voltage:.3f}V, "
        f"Measured={measured:.3f}V, Scale={scale_factor:.4f}"
    )

    return CalibrationResult(
        scale_factor=scale_factor,
        points=(point,),
        reference_instrument="external",
        timestamp=timestamp,
        success=True,
    )
