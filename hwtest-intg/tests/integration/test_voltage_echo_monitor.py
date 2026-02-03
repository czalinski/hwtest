"""Voltage echo monitor test with HALT/HASS support.

This test runs the analog voltage echo loop with state-based monitoring.
Supports three modes:
- Functional: Single pass through all states (default)
- HALT: Continuous loop until failure or cancelled
- HASS: Continuous loop until failure or cancelled

Environmental States:
- MINIMUM: 1.0V target voltage
- MIDDLE: 2.5V target voltage
- MAXIMUM: 4.0V target voltage

The monitor fails if the echoed voltage is not within the threshold for the current state.

Hardware Wiring:
    MCC 152 Analog Out 0 → UUT ADS1256 Channel 0
    UUT DAC8532 Channel 0 → MCC 118 Channel 0

Environment Variables:
    UUT_URL: URL of the UUT simulator (default: http://localhost:8080)
    MCC152_ADDRESS: MCC 152 HAT address (default: 0)
    MCC118_ADDRESS: MCC 118 HAT address (default: 4)
    TEST_MODE: Test mode - 'functional', 'halt', or 'hass' (default: functional)

Usage:
    # Functional test (one pass)
    pytest test_voltage_echo_monitor.py -v

    # HALT mode (continuous until failure)
    TEST_MODE=halt pytest test_voltage_echo_monitor.py -v -s

    # HASS mode (continuous until failure)
    TEST_MODE=hass pytest test_voltage_echo_monitor.py -v -s
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generator

import pytest

from hwtest_core.types.common import ChannelId, MonitorId, StateId, Timestamp
from hwtest_core.types.monitor import MonitorResult, MonitorVerdict, ThresholdViolation
from hwtest_core.types.state import EnvironmentalState, StateTransition
from hwtest_core.types.threshold import BoundType, StateThresholds, Threshold, ThresholdBound

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# Voltage levels for each state
VOLTAGE_MINIMUM = 1.0  # V
VOLTAGE_MIDDLE = 2.5   # V
VOLTAGE_MAXIMUM = 4.0  # V

# Tolerance for voltage matching (±150mV per leg, ×2 for full loop)
VOLTAGE_TOLERANCE = 0.30  # V (±300mV for full round-trip)

# Calibration factors
UUT_ADC_SCALE_FACTOR = 2.0   # Input voltage divider on Waveshare AD/DA
MCC118_SCALE_FACTOR = 1.5    # Attenuation on MCC 118 input

# Settling time between voltage changes
SETTLING_TIME = 0.1  # seconds

# Channel ID for the echoed voltage measurement
ECHO_VOLTAGE_CHANNEL = ChannelId("echo_voltage")


# =============================================================================
# Test Mode
# =============================================================================

class ExecutionMode(Enum):
    """Test execution mode."""
    FUNCTIONAL = "functional"  # Single pass
    HALT = "halt"              # Continuous until failure
    HASS = "hass"              # Continuous until failure


def get_execution_mode() -> ExecutionMode:
    """Get test mode from environment variable."""
    mode_str = os.environ.get("TEST_MODE", "functional").lower()
    try:
        return ExecutionMode(mode_str)
    except ValueError:
        logger.warning(f"Unknown TEST_MODE '{mode_str}', defaulting to functional")
        return ExecutionMode.FUNCTIONAL


# =============================================================================
# Environmental States
# =============================================================================

STATE_MINIMUM = EnvironmentalState(
    state_id=StateId("minimum"),
    name="Minimum",
    description=f"Minimum voltage level ({VOLTAGE_MINIMUM}V)",
    metadata={"target_voltage": VOLTAGE_MINIMUM},
)

STATE_MIDDLE = EnvironmentalState(
    state_id=StateId("middle"),
    name="Middle",
    description=f"Middle voltage level ({VOLTAGE_MIDDLE}V)",
    metadata={"target_voltage": VOLTAGE_MIDDLE},
)

STATE_MAXIMUM = EnvironmentalState(
    state_id=StateId("maximum"),
    name="Maximum",
    description=f"Maximum voltage level ({VOLTAGE_MAXIMUM}V)",
    metadata={"target_voltage": VOLTAGE_MAXIMUM},
)

# Ordered list of states for cycling
STATES = [STATE_MINIMUM, STATE_MIDDLE, STATE_MAXIMUM]


def create_transition_state(from_state: EnvironmentalState, to_state: EnvironmentalState) -> EnvironmentalState:
    """Create a transition state between two environmental states."""
    return EnvironmentalState(
        state_id=StateId(f"transition_{from_state.state_id}_to_{to_state.state_id}"),
        name=f"Transition",
        description=f"Transitioning from {from_state.name} to {to_state.name}",
        is_transition=True,
        metadata={
            "from_voltage": from_state.metadata.get("target_voltage"),
            "to_voltage": to_state.metadata.get("target_voltage"),
        },
    )


# =============================================================================
# State Thresholds
# =============================================================================

def create_voltage_threshold(target_voltage: float, tolerance: float) -> Threshold:
    """Create a threshold for voltage measurement."""
    return Threshold(
        channel=ECHO_VOLTAGE_CHANNEL,
        low=ThresholdBound(value=target_voltage - tolerance, bound_type=BoundType.INCLUSIVE),
        high=ThresholdBound(value=target_voltage + tolerance, bound_type=BoundType.INCLUSIVE),
    )


# Thresholds for each state
THRESHOLDS_MINIMUM = StateThresholds(
    state_id=STATE_MINIMUM.state_id,
    thresholds={
        ECHO_VOLTAGE_CHANNEL: create_voltage_threshold(VOLTAGE_MINIMUM, VOLTAGE_TOLERANCE),
    },
)

THRESHOLDS_MIDDLE = StateThresholds(
    state_id=STATE_MIDDLE.state_id,
    thresholds={
        ECHO_VOLTAGE_CHANNEL: create_voltage_threshold(VOLTAGE_MIDDLE, VOLTAGE_TOLERANCE),
    },
)

THRESHOLDS_MAXIMUM = StateThresholds(
    state_id=STATE_MAXIMUM.state_id,
    thresholds={
        ECHO_VOLTAGE_CHANNEL: create_voltage_threshold(VOLTAGE_MAXIMUM, VOLTAGE_TOLERANCE),
    },
)

# Map state IDs to thresholds
STATE_THRESHOLDS: dict[StateId, StateThresholds] = {
    STATE_MINIMUM.state_id: THRESHOLDS_MINIMUM,
    STATE_MIDDLE.state_id: THRESHOLDS_MIDDLE,
    STATE_MAXIMUM.state_id: THRESHOLDS_MAXIMUM,
}


# =============================================================================
# Voltage Echo Monitor
# =============================================================================

@dataclass
class VoltageEchoMonitor:
    """Monitor that evaluates echoed voltage against state-dependent thresholds.

    This monitor checks that the voltage read back through the echo loop
    matches the expected voltage for the current environmental state.
    """

    monitor_id: MonitorId = MonitorId("voltage_echo_monitor")

    def evaluate(
        self,
        measured_voltage: float,
        state: EnvironmentalState,
    ) -> MonitorResult:
        """Evaluate the measured voltage against the current state's threshold.

        Args:
            measured_voltage: The voltage measured at the end of the echo loop.
            state: The current environmental state.

        Returns:
            MonitorResult with PASS, FAIL, or SKIP verdict.
        """
        timestamp = Timestamp.now()

        # Skip evaluation during transitions
        if state.is_transition:
            return MonitorResult(
                monitor_id=self.monitor_id,
                verdict=MonitorVerdict.SKIP,
                timestamp=timestamp,
                state_id=state.state_id,
                message="Skipping evaluation during state transition",
            )

        # Get thresholds for current state
        thresholds = STATE_THRESHOLDS.get(state.state_id)
        if thresholds is None:
            return MonitorResult(
                monitor_id=self.monitor_id,
                verdict=MonitorVerdict.ERROR,
                timestamp=timestamp,
                state_id=state.state_id,
                message=f"No thresholds defined for state {state.state_id}",
            )

        # Check the measured voltage against threshold
        threshold = thresholds.get_threshold(ECHO_VOLTAGE_CHANNEL)
        if threshold is None:
            return MonitorResult(
                monitor_id=self.monitor_id,
                verdict=MonitorVerdict.ERROR,
                timestamp=timestamp,
                state_id=state.state_id,
                message=f"No threshold defined for channel {ECHO_VOLTAGE_CHANNEL}",
            )

        if threshold.check(measured_voltage):
            target = state.metadata.get("target_voltage", "unknown")
            return MonitorResult(
                monitor_id=self.monitor_id,
                verdict=MonitorVerdict.PASS,
                timestamp=timestamp,
                state_id=state.state_id,
                message=f"Voltage {measured_voltage:.3f}V within threshold for {state.name} (target: {target}V)",
            )
        else:
            # Create violation record
            low_val = threshold.low.value if threshold.low else float("-inf")
            high_val = threshold.high.value if threshold.high else float("inf")
            violation = ThresholdViolation(
                channel=ECHO_VOLTAGE_CHANNEL,
                value=measured_voltage,
                threshold=threshold,
                message=f"Voltage {measured_voltage:.3f}V outside [{low_val:.3f}V, {high_val:.3f}V]",
            )
            return MonitorResult(
                monitor_id=self.monitor_id,
                verdict=MonitorVerdict.FAIL,
                timestamp=timestamp,
                state_id=state.state_id,
                violations=(violation,),
                message=f"Voltage out of range for {state.name}",
            )


# =============================================================================
# Test Statistics
# =============================================================================

@dataclass
class RunStatistics:
    """Statistics for test execution."""

    cycles_completed: int = 0
    states_tested: int = 0
    passes: int = 0
    failures: int = 0
    skips: int = 0
    errors: int = 0

    def record(self, result: MonitorResult) -> None:
        """Record a monitor result."""
        self.states_tested += 1
        if result.verdict == MonitorVerdict.PASS:
            self.passes += 1
        elif result.verdict == MonitorVerdict.FAIL:
            self.failures += 1
        elif result.verdict == MonitorVerdict.SKIP:
            self.skips += 1
        elif result.verdict == MonitorVerdict.ERROR:
            self.errors += 1

    def summary(self) -> str:
        """Return a summary string."""
        return (
            f"Cycles: {self.cycles_completed}, "
            f"States: {self.states_tested}, "
            f"Pass: {self.passes}, "
            f"Fail: {self.failures}, "
            f"Skip: {self.skips}, "
            f"Error: {self.errors}"
        )


# =============================================================================
# Cancellation Support
# =============================================================================

class CancellationToken:
    """Token for cooperative cancellation of continuous tests."""

    def __init__(self) -> None:
        self._cancelled = False
        self._original_handler: Any = None

    def cancel(self) -> None:
        """Request cancellation."""
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation was requested."""
        return self._cancelled

    def install_signal_handler(self) -> None:
        """Install SIGINT handler for graceful cancellation."""
        def handler(signum: int, frame: Any) -> None:
            logger.info("Cancellation requested (Ctrl+C)")
            self.cancel()

        self._original_handler = signal.signal(signal.SIGINT, handler)

    def restore_signal_handler(self) -> None:
        """Restore original signal handler."""
        if self._original_handler is not None:
            signal.signal(signal.SIGINT, self._original_handler)


# =============================================================================
# Fixtures
# =============================================================================

def get_mcc152_address() -> int:
    """Get MCC 152 address from environment."""
    return int(os.environ.get("MCC152_ADDRESS", "0"))


def get_mcc118_address() -> int:
    """Get MCC 118 address from environment."""
    return int(os.environ.get("MCC118_ADDRESS", "4"))


@pytest.fixture
def mcc152_dac() -> Generator[Any, None, None]:
    """Provide an MCC 152 HAT for DAC output."""
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
    """Provide an MCC 118 HAT for ADC input."""
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


@pytest.fixture
def voltage_monitor() -> VoltageEchoMonitor:
    """Provide a voltage echo monitor."""
    return VoltageEchoMonitor()


@pytest.fixture
def cancellation_token() -> Generator[CancellationToken, None, None]:
    """Provide a cancellation token with signal handler."""
    token = CancellationToken()
    token.install_signal_handler()
    yield token
    token.restore_signal_handler()


# =============================================================================
# Test Class
# =============================================================================

class TestVoltageEchoMonitor:
    """Test voltage echo with state-based monitoring.

    Supports three modes:
    - Functional: Single pass through all states
    - HALT: Continuous loop until failure or cancelled
    - HASS: Continuous loop until failure or cancelled
    """

    async def _run_echo_cycle(
        self,
        uut_client: Any,
        mcc152_dac: Any,
        mcc118_adc: Any,
        state: EnvironmentalState,
    ) -> float:
        """Run one echo cycle for a given state.

        Returns the measured voltage at the end of the echo loop.
        """
        target_voltage = state.metadata.get("target_voltage", 0.0)

        # Step 1: Set output voltage on rack DAC
        mcc152_dac.a_out_write(0, target_voltage)
        logger.debug(f"Set MCC 152 DAC to {target_voltage}V")
        time.sleep(SETTLING_TIME)

        # Step 2: Read voltage on UUT ADC (apply calibration)
        uut_adc_raw = await uut_client.adc_read(0)
        uut_adc_voltage = uut_adc_raw * UUT_ADC_SCALE_FACTOR
        logger.debug(f"UUT ADC read: {uut_adc_raw}V (calibrated: {uut_adc_voltage}V)")

        # Step 3: Write calibrated voltage to UUT DAC (echo)
        await uut_client.dac_write(0, uut_adc_voltage)
        logger.debug(f"UUT DAC write: {uut_adc_voltage}V")
        time.sleep(SETTLING_TIME)

        # Step 4: Read echoed voltage on rack ADC (apply calibration)
        rack_adc_raw = mcc118_adc.a_in_read(0)
        measured_voltage = rack_adc_raw * MCC118_SCALE_FACTOR
        logger.debug(f"MCC 118 ADC read: {rack_adc_raw}V (calibrated: {measured_voltage}V)")

        return measured_voltage

    async def _run_single_pass(
        self,
        uut_client: Any,
        mcc152_dac: Any,
        mcc118_adc: Any,
        monitor: VoltageEchoMonitor,
        stats: RunStatistics,
    ) -> MonitorResult | None:
        """Run a single pass through all states.

        Returns the first failing result, or None if all passed.
        """
        for i, state in enumerate(STATES):
            # Log state transition
            if i > 0:
                prev_state = STATES[i - 1]
                transition = StateTransition(
                    from_state=prev_state.state_id,
                    to_state=state.state_id,
                    timestamp=Timestamp.now(),
                    reason=f"Cycling to {state.name}",
                )
                logger.info(f"State transition: {prev_state.name} -> {state.name}")

            logger.info(f"Testing state: {state.name} (target: {state.metadata.get('target_voltage')}V)")

            # Run echo cycle
            measured_voltage = await self._run_echo_cycle(
                uut_client, mcc152_dac, mcc118_adc, state
            )

            # Evaluate with monitor
            result = monitor.evaluate(measured_voltage, state)
            stats.record(result)

            # Log result
            if result.passed:
                logger.info(f"  PASS: {result.message}")
            elif result.failed:
                logger.error(f"  FAIL: {result.message}")
                for violation in result.violations:
                    logger.error(f"    Violation: {violation.message}")
                return result
            else:
                logger.warning(f"  {result.verdict.name}: {result.message}")

        stats.cycles_completed += 1
        return None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_voltage_echo_monitored(
        self,
        uut_client: Any,
        mcc152_dac: Any,
        mcc118_adc: Any,
        voltage_monitor: VoltageEchoMonitor,
        cancellation_token: CancellationToken,
    ) -> None:
        """Run voltage echo test with monitoring.

        Test mode is determined by TEST_MODE environment variable:
        - functional: Single pass (default)
        - halt: Continuous until failure or cancelled
        - hass: Continuous until failure or cancelled
        """
        test_mode = get_execution_mode()
        stats = RunStatistics()

        logger.info(f"Starting voltage echo monitor test in {test_mode.value.upper()} mode")
        logger.info(f"States: {[s.name for s in STATES]}")
        logger.info(f"Voltage tolerance: ±{VOLTAGE_TOLERANCE}V")

        try:
            if test_mode == ExecutionMode.FUNCTIONAL:
                # Single pass
                failure = await self._run_single_pass(
                    uut_client, mcc152_dac, mcc118_adc, voltage_monitor, stats
                )
                if failure:
                    pytest.fail(f"Monitor failure: {failure.message}")

            else:
                # Continuous mode (HALT or HASS)
                logger.info("Press Ctrl+C to stop")
                cycle = 0

                while not cancellation_token.is_cancelled:
                    cycle += 1
                    logger.info(f"=== Cycle {cycle} ===")

                    failure = await self._run_single_pass(
                        uut_client, mcc152_dac, mcc118_adc, voltage_monitor, stats
                    )

                    if failure:
                        logger.error(f"Test failed on cycle {cycle}")
                        logger.info(f"Final statistics: {stats.summary()}")
                        pytest.fail(f"Monitor failure on cycle {cycle}: {failure.message}")

                    logger.info(f"Cycle {cycle} complete. {stats.summary()}")

                # Cancelled gracefully
                logger.info(f"Test cancelled after {stats.cycles_completed} cycles")

        finally:
            # Reset DAC outputs
            mcc152_dac.a_out_write(0, 0.0)
            await uut_client.dac_write(0, 0.0)
            logger.info(f"Final statistics: {stats.summary()}")

        # Assert at least one pass in continuous mode
        if test_mode in (ExecutionMode.HALT, ExecutionMode.HASS):
            assert stats.passes > 0, "No successful measurements recorded"
