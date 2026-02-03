"""Integration tests for analog voltage signal path.

Tests the analog voltage path between the test rack and UUT:
- Rack MCC 152 DAC output → UUT ADS1256 ADC input
- UUT DAC8532 output → Rack MCC 118 ADC input (optional)

Hardware Wiring:
    MCC 152 Analog Out 0 → UUT ADS1256 Channel 0
    UUT DAC8532 Channel 0 → MCC 118 Channel 0 (optional)

Environment Variables:
    UUT_URL: URL of the UUT simulator (default: http://192.168.68.94:8080)
    MCC152_ADDRESS: MCC 152 HAT address (default: 0)
    MCC118_ADDRESS: MCC 118 HAT address (default: 4)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Generator

import pytest

logger = logging.getLogger(__name__)

# Test voltage steps and tolerance
TEST_VOLTAGES = [1.0, 2.5, 4.0]
VOLTAGE_TOLERANCE = 0.15  # ±150mV tolerance for the full signal path

# Calibration factors for hardware-specific scaling
# Waveshare AD/DA board has input voltage divider (~2:1)
UUT_ADC_SCALE_FACTOR = 2.0  # Multiply UUT ADC reading by this to get actual voltage
# MCC 118 reads UUT DAC output with some attenuation
MCC118_SCALE_FACTOR = 1.5  # Multiply MCC 118 reading by this to get actual voltage


def get_mcc152_address() -> int:
    """Get MCC 152 address from environment."""
    return int(os.environ.get("MCC152_ADDRESS", "0"))


def get_mcc118_address() -> int:
    """Get MCC 118 address from environment."""
    return int(os.environ.get("MCC118_ADDRESS", "4"))


@pytest.fixture
def mcc152_dac() -> Generator[Any, None, None]:
    """Provide an MCC 152 HAT for DAC output.

    Uses the daqhats library directly for simple analog output control.
    """
    try:
        import daqhats  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("daqhats library not installed")

    address = get_mcc152_address()
    try:
        hat = daqhats.mcc152(address)
    except Exception as exc:
        pytest.skip(f"MCC 152 not found at address {address}: {exc}")

    # Set initial output to 0V
    hat.a_out_write(0, 0.0)

    yield hat

    # Reset to 0V on cleanup
    try:
        hat.a_out_write(0, 0.0)
    except Exception:
        pass


@pytest.fixture
def mcc118_adc() -> Generator[Any, None, None]:
    """Provide an MCC 118 HAT for ADC input.

    Uses the daqhats library directly for single voltage reads.
    """
    try:
        import daqhats  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("daqhats library not installed")

    address = get_mcc118_address()
    try:
        hat = daqhats.mcc118(address)
    except Exception as exc:
        pytest.skip(f"MCC 118 not found at address {address}: {exc}")

    yield hat


class TestAnalogVoltageEcho:
    """Test analog voltage echo through the UUT.

    This test verifies the signal path:
    Rack MCC 152 DAC → UUT ADS1256 ADC → UUT DAC8532 → (optionally) Rack MCC 118 ADC
    """

    @pytest.mark.asyncio
    async def test_rack_to_uut_voltage_path(
        self,
        uut_client: Any,
        mcc152_dac: Any,
    ) -> None:
        """Test that UUT can read voltages output by the rack.

        Signal path: MCC 152 DAC Ch0 → UUT ADS1256 ADC Ch0

        Steps:
        1. Set voltage on MCC 152 DAC channel 0
        2. Read voltage on UUT ADS1256 ADC channel 0
        3. Verify reading matches within tolerance
        """
        for target_voltage in TEST_VOLTAGES:
            # Set output voltage on rack DAC
            mcc152_dac.a_out_write(0, target_voltage)
            logger.info(f"Set MCC 152 DAC to {target_voltage}V")

            # Allow settling time
            time.sleep(0.1)

            # Read voltage on UUT ADC (apply calibration for input voltage divider)
            uut_raw_voltage = await uut_client.adc_read(0)
            uut_voltage = uut_raw_voltage * UUT_ADC_SCALE_FACTOR
            logger.info(f"UUT ADC read: {uut_raw_voltage}V (calibrated: {uut_voltage}V, expected: {target_voltage}V)")

            # Verify within tolerance
            assert abs(uut_voltage - target_voltage) <= VOLTAGE_TOLERANCE, (
                f"Voltage mismatch: expected {target_voltage}V ± {VOLTAGE_TOLERANCE}V, "
                f"got {uut_voltage}V (raw: {uut_raw_voltage}V, error: {abs(uut_voltage - target_voltage):.3f}V)"
            )

        # Reset DAC to 0V
        mcc152_dac.a_out_write(0, 0.0)

    @pytest.mark.asyncio
    async def test_full_voltage_echo_loop(
        self,
        uut_client: Any,
        mcc152_dac: Any,
        mcc118_adc: Any,
    ) -> None:
        """Test full voltage echo loop through UUT.

        Signal path:
        MCC 152 DAC → UUT ADS1256 ADC → UUT DAC8532 → MCC 118 ADC

        Steps:
        1. Set voltage on MCC 152 DAC channel 0
        2. Read voltage on UUT ADS1256 ADC channel 0
        3. Write that voltage to UUT DAC8532 channel 0
        4. Read echoed voltage on MCC 118 ADC channel 0
        5. Verify the echo matches the original within tolerance
        """
        for target_voltage in TEST_VOLTAGES:
            # Step 1: Set output voltage on rack DAC
            mcc152_dac.a_out_write(0, target_voltage)
            logger.info(f"Set MCC 152 DAC to {target_voltage}V")
            time.sleep(0.1)

            # Step 2: Read voltage on UUT ADC (apply calibration)
            uut_adc_raw = await uut_client.adc_read(0)
            uut_adc_voltage = uut_adc_raw * UUT_ADC_SCALE_FACTOR
            logger.info(f"UUT ADC read: {uut_adc_raw}V (calibrated: {uut_adc_voltage}V)")

            # Step 3: Write calibrated voltage to UUT DAC (echo the actual value)
            await uut_client.dac_write(0, uut_adc_voltage)
            logger.info(f"UUT DAC write: {uut_adc_voltage}V")
            time.sleep(0.1)

            # Step 4: Read echoed voltage on rack ADC (apply calibration)
            rack_adc_raw = mcc118_adc.a_in_read(0)
            rack_adc_voltage = rack_adc_raw * MCC118_SCALE_FACTOR
            logger.info(f"MCC 118 ADC read: {rack_adc_raw}V (calibrated: {rack_adc_voltage}V)")

            # Step 5: Verify echo matches original
            # Allow double tolerance for the full round-trip
            full_loop_tolerance = VOLTAGE_TOLERANCE * 2
            assert abs(rack_adc_voltage - target_voltage) <= full_loop_tolerance, (
                f"Echo voltage mismatch: expected {target_voltage}V ± {full_loop_tolerance}V, "
                f"got {rack_adc_voltage}V (raw: {rack_adc_raw}V, error: {abs(rack_adc_voltage - target_voltage):.3f}V)"
            )

        # Reset DAC to 0V
        mcc152_dac.a_out_write(0, 0.0)
        await uut_client.dac_write(0, 0.0)


class TestUutDacOutput:
    """Test UUT DAC output can be read by rack."""

    @pytest.mark.asyncio
    async def test_uut_to_rack_voltage_path(
        self,
        uut_client: Any,
        mcc118_adc: Any,
    ) -> None:
        """Test that rack can read voltages output by the UUT.

        Signal path: UUT DAC8532 Ch0 → MCC 118 ADC Ch0

        Steps:
        1. Set voltage on UUT DAC channel 0
        2. Read voltage on MCC 118 ADC channel 0
        3. Verify reading matches within tolerance
        """
        for target_voltage in TEST_VOLTAGES:
            # Set output voltage on UUT DAC
            await uut_client.dac_write(0, target_voltage)
            logger.info(f"Set UUT DAC to {target_voltage}V")

            # Allow settling time
            time.sleep(0.1)

            # Read voltage on rack ADC (apply calibration)
            rack_raw_voltage = mcc118_adc.a_in_read(0)
            rack_voltage = rack_raw_voltage * MCC118_SCALE_FACTOR
            logger.info(f"MCC 118 ADC read: {rack_raw_voltage}V (calibrated: {rack_voltage}V, expected: {target_voltage}V)")

            # Verify within tolerance
            assert abs(rack_voltage - target_voltage) <= VOLTAGE_TOLERANCE, (
                f"Voltage mismatch: expected {target_voltage}V ± {VOLTAGE_TOLERANCE}V, "
                f"got {rack_voltage}V (raw: {rack_raw_voltage}V, error: {abs(rack_voltage - target_voltage):.3f}V)"
            )

        # Reset UUT DAC to 0V
        await uut_client.dac_write(0, 0.0)
