"""Voltage echo monitor test with HALT/HASS support.

This test runs the analog voltage echo loop with state-based monitoring.
Supports three modes:
- Functional: Single pass through all states (default)
- HALT: Continuous loop until failure or cancelled
- HASS: Continuous loop until failure or cancelled

Configuration:
    Test parameters (states, thresholds, timing) are loaded from:
        configs/voltage_echo_monitor.yaml

    Rack class configuration (instruments, channels) is loaded from:
        src/hwtest_intg/configs/pi5_mcc_intg_a_rack.yaml

    Rack instance calibration (per-unit scale factors) is loaded from:
        ~/.config/hwtest/racks/pi5_mcc_intg_a_<serial>.yaml

    Run 'hwtest-rack calibrate --serial <serial>' to calibrate a rack.

Hardware Wiring:
    MCC 152 Analog Out 0 → UUT ADS1256 Channel 0
    UUT DAC8532 Channel 0 → MCC 118 Channel 0

Environment Variables:
    UUT_URL: URL of the UUT simulator (default: http://localhost:8080)
    MCC152_ADDRESS: MCC 152 HAT address (default: 0)
    MCC118_ADDRESS: MCC 118 HAT address (default: 4)
    TEST_MODE: Test mode - 'functional', 'halt', or 'hass' (default: functional)
    TEST_DEFINITION_PATH: Additional paths to search for definition files
    RACK_CONFIG: Override path to rack class configuration file
    RACK_SERIAL: Rack instance serial number for calibration lookup
    HWTEST_RACK_INSTANCE_PATH: Additional paths to search for rack instance configs
    TELEMETRY_ENABLED: Enable InfluxDB telemetry logging (default: 0)
    INFLUXDB_URL: InfluxDB URL (default: http://localhost:8086)
    INFLUXDB_ORG: InfluxDB organization (default: hwtest)
    INFLUXDB_BUCKET: InfluxDB bucket (default: telemetry)
    INFLUXDB_TOKEN: InfluxDB authentication token (required if TELEMETRY_ENABLED=1)

Usage:
    # Functional test (one pass)
    pytest test_voltage_echo_monitor.py -v

    # HALT mode (continuous until failure)
    TEST_MODE=halt pytest test_voltage_echo_monitor.py -v -s

    # HASS mode (continuous until failure)
    TEST_MODE=hass pytest test_voltage_echo_monitor.py -v -s

    # With telemetry logging to InfluxDB
    TELEMETRY_ENABLED=1 INFLUXDB_TOKEN=<token> TEST_MODE=halt pytest test_voltage_echo_monitor.py -v -s
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, AsyncGenerator, Generator

import pytest

from hwtest_core.types.common import ChannelId, DataType, MonitorId, SourceId, StateId, Timestamp
from hwtest_core.types.monitor import MonitorResult, MonitorVerdict, ThresholdViolation
from hwtest_core.types.state import EnvironmentalState, StateTransition
from hwtest_core.types.streaming import StreamData, StreamField, StreamSchema
from hwtest_core.types.threshold import BoundType, StateThresholds, Threshold, ThresholdBound
from hwtest_testcase import TestDefinition, StateDef, load_definition
from hwtest_rack.config import RackConfig, load_config as load_rack_config
from hwtest_rack.instance import RackInstanceConfig, load_instance_config

logger = logging.getLogger(__name__)

# =============================================================================
# Test Definition Loading
# =============================================================================

# Search paths for test definition YAML files
DEFINITION_SEARCH_PATHS = [
    Path(__file__).parent.parent.parent / "configs",  # hwtest-intg/configs
    Path(__file__).parent,  # Same directory as test
]

# Search paths for rack configuration YAML files
RACK_CONFIG_SEARCH_PATHS = [
    Path(__file__).parent.parent.parent / "src" / "hwtest_intg" / "configs",  # hwtest-intg package
    Path(__file__).parent.parent.parent / "configs",  # hwtest-intg/configs
]


def get_test_definition() -> TestDefinition:
    """Load the test definition from YAML.

    Returns:
        TestDefinition with all parameters loaded from YAML.

    Raises:
        FileNotFoundError: If definition file not found.
    """
    return load_definition(
        "voltage_echo_monitor",
        search_paths=DEFINITION_SEARCH_PATHS,
    )


# Load definition at module level for use in fixtures
# This allows the YAML to be the single source of truth
_definition: TestDefinition | None = None
_rack_config: RackConfig | None = None
_rack_instance: RackInstanceConfig | None = None


def _get_definition() -> TestDefinition:
    """Get cached test definition."""
    global _definition
    if _definition is None:
        _definition = get_test_definition()
        logger.info(f"Loaded test definition from: {_definition.source_path}")
    return _definition


def _find_rack_config(rack_id: str) -> Path | None:
    """Find a rack configuration file by ID.

    Args:
        rack_id: Rack identifier (e.g., "pi5_mcc_intg_a_rack").

    Returns:
        Path to the rack config file, or None if not found.
    """
    # Check environment variable override
    env_path = os.environ.get("RACK_CONFIG")
    if env_path:
        path = Path(env_path)
        if path.is_file():
            return path

    # Search in standard paths
    filenames = [
        f"{rack_id}.yaml",
        f"{rack_id}.yml",
    ]

    for search_dir in RACK_CONFIG_SEARCH_PATHS:
        if not search_dir.is_dir():
            continue
        for filename in filenames:
            candidate = search_dir / filename
            if candidate.is_file():
                return candidate

    return None


def _get_rack_config() -> RackConfig:
    """Get cached rack configuration.

    Loads the rack config referenced by the test definition.
    """
    global _rack_config
    if _rack_config is None:
        definition = _get_definition()
        rack_id = definition.rack_id

        if rack_id is None:
            raise ValueError("Test definition does not specify a rack reference")

        rack_path = _find_rack_config(rack_id)
        if rack_path is None:
            raise FileNotFoundError(
                f"Rack configuration not found for '{rack_id}'. "
                f"Set RACK_CONFIG environment variable or add config to search paths."
            )

        _rack_config = load_rack_config(rack_path)
        logger.info(f"Loaded rack config from: {rack_path}")

    return _rack_config


def _get_rack_instance() -> RackInstanceConfig:
    """Get cached rack instance configuration.

    Loads the rack instance config for calibration data.
    Uses RACK_SERIAL environment variable or finds first matching instance.
    """
    global _rack_instance
    if _rack_instance is None:
        definition = _get_definition()
        rack_id = definition.rack_id

        if rack_id is None:
            raise ValueError("Test definition does not specify a rack reference")

        # Remove "_rack" suffix if present for instance lookup
        rack_class = rack_id.replace("_rack", "")

        serial = os.environ.get("RACK_SERIAL")

        try:
            _rack_instance = load_instance_config(rack_class, serial)
            logger.info(
                f"Loaded rack instance: {_rack_instance.instance.rack_class} "
                f"#{_rack_instance.instance.serial_number} from {_rack_instance.source_path}"
            )
        except FileNotFoundError:
            # Fall back to default calibration values
            logger.warning(
                f"Rack instance config not found for class '{rack_class}'"
                + (f" serial '{serial}'" if serial else "")
                + ". Using default calibration values."
            )
            from hwtest_rack.instance import RackInstanceInfo, CalibrationMetadata
            _rack_instance = RackInstanceConfig(
                instance=RackInstanceInfo(
                    serial_number="default",
                    rack_class=rack_class,
                    description="Default instance (no calibration file found)",
                ),
                calibration={
                    "uut_adc_scale_factor": 2.0,
                    "mcc118_scale_factor": 1.0,
                },
                metadata=CalibrationMetadata(
                    notes="Using default values - run 'hwtest-rack calibrate' to calibrate",
                ),
            )

    return _rack_instance


# =============================================================================
# Channel ID for monitoring
# =============================================================================

ECHO_VOLTAGE_CHANNEL = ChannelId("echo_voltage")


# =============================================================================
# Telemetry Configuration
# =============================================================================


def is_telemetry_enabled() -> bool:
    """Check if telemetry logging is enabled."""
    return os.environ.get("TELEMETRY_ENABLED", "0").lower() in ("1", "true", "yes")


def get_influxdb_config() -> dict[str, Any]:
    """Get InfluxDB configuration from environment variables."""
    return {
        "url": os.environ.get("INFLUXDB_URL", "http://localhost:8086"),
        "org": os.environ.get("INFLUXDB_ORG", "hwtest"),
        "bucket": os.environ.get("INFLUXDB_BUCKET", "telemetry"),
        "token": os.environ.get("INFLUXDB_TOKEN"),
    }


def get_rack_id() -> str:
    """Get rack ID from environment."""
    return os.environ.get("RACK_ID", "pi5-mcc-intg-a")


def get_uut_id() -> str:
    """Get UUT ID from environment or URL."""
    uut_id = os.environ.get("UUT_ID")
    if uut_id:
        return uut_id
    uut_url = os.environ.get("UUT_URL", "localhost")
    if "://" in uut_url:
        uut_url = uut_url.split("://")[1]
    return uut_url.split(":")[0].replace(".", "-")


# Telemetry schema for voltage echo measurements
TELEMETRY_SCHEMA = StreamSchema(
    source_id=SourceId("voltage_echo_test"),
    fields=(
        StreamField("target_voltage", DataType.F64, "V"),
        StreamField("rack_dac_voltage", DataType.F64, "V"),
        StreamField("uut_adc_voltage", DataType.F64, "V"),
        StreamField("uut_dac_voltage", DataType.F64, "V"),
        StreamField("rack_adc_voltage", DataType.F64, "V"),
    ),
)


@dataclass
class VoltageReadings:
    """Container for all voltage readings in an echo cycle."""

    target_voltage: float
    rack_dac_voltage: float
    uut_adc_voltage: float
    uut_dac_voltage: float
    rack_adc_voltage: float
    state_name: str

    def as_tuple(self) -> tuple[float, ...]:
        """Return readings as a tuple for StreamData."""
        return (
            self.target_voltage,
            self.rack_dac_voltage,
            self.uut_adc_voltage,
            self.uut_dac_voltage,
            self.rack_adc_voltage,
        )


# =============================================================================
# Test Mode
# =============================================================================


class ExecutionMode(Enum):
    """Test execution mode."""

    FUNCTIONAL = "functional"  # Single pass
    HALT = "halt"  # Continuous until failure
    HASS = "hass"  # Continuous until failure


def get_execution_mode() -> ExecutionMode:
    """Get test mode from environment variable."""
    mode_str = os.environ.get("TEST_MODE", "functional").lower()
    try:
        return ExecutionMode(mode_str)
    except ValueError:
        logger.warning(f"Unknown TEST_MODE '{mode_str}', defaulting to functional")
        return ExecutionMode.FUNCTIONAL


# =============================================================================
# Environmental States (from YAML definition)
# =============================================================================


def create_environmental_state(state_def: StateDef) -> EnvironmentalState:
    """Create an EnvironmentalState from a StateDef.

    Args:
        state_def: State definition from YAML.

    Returns:
        EnvironmentalState for use with monitoring.
    """
    return EnvironmentalState(
        state_id=StateId(state_def.id),
        name=state_def.name,
        description=state_def.description or f"{state_def.name} ({state_def.target_voltage}V)",
        metadata={"target_voltage": state_def.target_voltage, **state_def.metadata},
    )


def create_transition_state(
    from_state: EnvironmentalState, to_state: EnvironmentalState
) -> EnvironmentalState:
    """Create a transition state between two environmental states."""
    return EnvironmentalState(
        state_id=StateId(f"transition_{from_state.state_id}_to_{to_state.state_id}"),
        name="Transition",
        description=f"Transitioning from {from_state.name} to {to_state.name}",
        is_transition=True,
        metadata={
            "from_voltage": from_state.metadata.get("target_voltage"),
            "to_voltage": to_state.metadata.get("target_voltage"),
        },
    )


# =============================================================================
# State Thresholds (from YAML definition)
# =============================================================================


def create_state_thresholds(state_def: StateDef) -> StateThresholds:
    """Create StateThresholds from a StateDef.

    Args:
        state_def: State definition from YAML with threshold bounds.

    Returns:
        StateThresholds for monitoring.
    """
    thresholds: dict[ChannelId, Threshold] = {}

    echo_thresh = state_def.thresholds.get("echo_voltage")
    if echo_thresh is not None:
        thresholds[ECHO_VOLTAGE_CHANNEL] = Threshold(
            channel=ECHO_VOLTAGE_CHANNEL,
            low=ThresholdBound(value=echo_thresh.low, bound_type=BoundType.INCLUSIVE)
            if echo_thresh.low is not None
            else None,
            high=ThresholdBound(value=echo_thresh.high, bound_type=BoundType.INCLUSIVE)
            if echo_thresh.high is not None
            else None,
        )

    return StateThresholds(
        state_id=StateId(state_def.id),
        thresholds=thresholds,
    )


# =============================================================================
# Voltage Echo Monitor
# =============================================================================


@dataclass
class VoltageEchoMonitor:
    """Monitor that evaluates echoed voltage against state-dependent thresholds.

    This monitor checks that the voltage read back through the echo loop
    matches the expected voltage for the current environmental state.
    """

    state_thresholds: dict[StateId, StateThresholds]
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
        thresholds = self.state_thresholds.get(state.state_id)
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
def test_definition() -> TestDefinition:
    """Provide the test definition loaded from YAML."""
    return _get_definition()


@pytest.fixture
def rack_config() -> RackConfig:
    """Provide the rack configuration loaded from YAML."""
    return _get_rack_config()


@pytest.fixture
def rack_instance() -> RackInstanceConfig:
    """Provide the rack instance configuration with calibration data."""
    return _get_rack_instance()


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
def voltage_monitor(test_definition: TestDefinition) -> VoltageEchoMonitor:
    """Provide a voltage echo monitor with thresholds from YAML."""
    state_thresholds: dict[StateId, StateThresholds] = {}

    for state_def in test_definition.state_sequence:
        state_id = StateId(state_def.id)
        state_thresholds[state_id] = create_state_thresholds(state_def)

    return VoltageEchoMonitor(state_thresholds=state_thresholds)


@pytest.fixture
def environmental_states(test_definition: TestDefinition) -> list[EnvironmentalState]:
    """Provide environmental states from YAML definition."""
    return [create_environmental_state(s) for s in test_definition.state_sequence]


@pytest.fixture
def cancellation_token() -> Generator[CancellationToken, None, None]:
    """Provide a cancellation token with signal handler."""
    token = CancellationToken()
    token.install_signal_handler()
    yield token
    token.restore_signal_handler()


@pytest.fixture
async def telemetry_logger() -> AsyncGenerator[Any, None]:
    """Provide an InfluxDB telemetry logger if enabled.

    Yields None if telemetry is disabled or InfluxDB is not configured.
    """
    if not is_telemetry_enabled():
        logger.info("Telemetry logging disabled (set TELEMETRY_ENABLED=1 to enable)")
        yield None
        return

    try:
        from hwtest_logger import InfluxDbStreamLogger, InfluxDbStreamLoggerConfig
    except ImportError:
        logger.warning("hwtest-logger[influxdb] not installed, telemetry disabled")
        yield None
        return

    config = get_influxdb_config()
    if not config["token"]:
        logger.warning("INFLUXDB_TOKEN not set, telemetry disabled")
        yield None
        return

    logger_config = InfluxDbStreamLoggerConfig(
        url=config["url"],
        org=config["org"],
        bucket=config["bucket"],
        token=config["token"],
        measurement="voltage_echo",
        batch_size=100,
        flush_interval_ms=1000,
    )

    influx_logger = InfluxDbStreamLogger(logger_config)
    influx_logger.register_schema("voltage_echo", TELEMETRY_SCHEMA)

    test_mode = get_execution_mode()
    test_run_id = str(uuid.uuid4())[:8]

    tags = {
        "test_type": test_mode.value.upper(),
        "test_case_id": "voltage_echo_monitor",
        "test_run_id": test_run_id,
        "rack_id": get_rack_id(),
        "uut_id": get_uut_id(),
    }

    try:
        await influx_logger.start(tags)
        logger.info(
            "Telemetry logging enabled: %s (run_id: %s)",
            config["url"],
            test_run_id,
        )
        yield influx_logger
    except Exception as exc:
        logger.warning("Failed to start InfluxDB logger: %s", exc)
        yield None
    finally:
        if influx_logger.is_running:
            await influx_logger.stop()
            logger.info("Telemetry logger stopped")


# =============================================================================
# Test Class
# =============================================================================


class TestVoltageEchoMonitor:
    """Test voltage echo with state-based monitoring.

    Supports three modes:
    - Functional: Single pass through all states
    - HALT: Continuous loop until failure or cancelled
    - HASS: Continuous loop until failure or cancelled

    All parameters are loaded from configs/voltage_echo_monitor.yaml.
    """

    async def _run_echo_cycle(
        self,
        uut_client: Any,
        mcc152_dac: Any,
        mcc118_adc: Any,
        state: EnvironmentalState,
        definition: TestDefinition,
        rack_instance: RackInstanceConfig,
    ) -> VoltageReadings:
        """Run one echo cycle for a given state.

        Returns all voltage readings from the echo loop.
        """
        target_voltage = state.metadata.get("target_voltage", 0.0)
        settling_time = definition.parameters.settling_time_seconds
        uut_adc_scale = rack_instance.get_calibration("uut_adc_scale_factor", 1.0)
        mcc118_scale = rack_instance.get_calibration("mcc118_scale_factor", 1.0)

        # Step 1: Set output voltage on rack DAC
        mcc152_dac.a_out_write(0, target_voltage)
        logger.debug(f"Set MCC 152 DAC to {target_voltage}V")
        time.sleep(settling_time)

        # Step 2: Read voltage on UUT ADC (apply calibration)
        uut_adc_raw = await uut_client.adc_read(0)
        uut_adc_voltage = uut_adc_raw * uut_adc_scale
        logger.debug(f"UUT ADC read: {uut_adc_raw}V (calibrated: {uut_adc_voltage}V)")

        # Step 3: Write calibrated voltage to UUT DAC (echo)
        await uut_client.dac_write(0, uut_adc_voltage)
        logger.debug(f"UUT DAC write: {uut_adc_voltage}V")
        time.sleep(settling_time)

        # Step 4: Read echoed voltage on rack ADC (apply calibration)
        rack_adc_raw = mcc118_adc.a_in_read(0)
        measured_voltage = rack_adc_raw * mcc118_scale
        logger.debug(f"MCC 118 ADC read: {rack_adc_raw}V (calibrated: {measured_voltage}V)")

        return VoltageReadings(
            target_voltage=target_voltage,
            rack_dac_voltage=target_voltage,  # DAC was set to target
            uut_adc_voltage=uut_adc_voltage,
            uut_dac_voltage=uut_adc_voltage,  # DAC was set to calibrated ADC reading
            rack_adc_voltage=measured_voltage,
            state_name=state.name,
        )

    async def _log_telemetry(
        self,
        telemetry_logger: Any,
        readings: VoltageReadings,
    ) -> None:
        """Log voltage readings to InfluxDB if logger is available."""
        if telemetry_logger is None:
            return

        timestamp_ns = time.time_ns()
        data = StreamData(
            schema_id=TELEMETRY_SCHEMA.schema_id,
            timestamp_ns=timestamp_ns,
            period_ns=0,  # Single sample, no period
            samples=(readings.as_tuple(),),
        )

        # Add state_name as a per-point tag for efficient filtering
        extra_tags = {"state": readings.state_name.lower()}

        try:
            await telemetry_logger.log("voltage_echo", data, extra_tags=extra_tags)
        except Exception as exc:
            logger.warning("Failed to log telemetry: %s", exc)

    async def _run_single_pass(
        self,
        uut_client: Any,
        mcc152_dac: Any,
        mcc118_adc: Any,
        monitor: VoltageEchoMonitor,
        states: list[EnvironmentalState],
        definition: TestDefinition,
        rack_instance: RackInstanceConfig,
        stats: RunStatistics,
        telemetry_logger: Any = None,
    ) -> MonitorResult | None:
        """Run a single pass through all states.

        Returns the first failing result, or None if all passed.
        """
        for i, state in enumerate(states):
            # Log state transition
            if i > 0:
                prev_state = states[i - 1]
                transition = StateTransition(
                    from_state=prev_state.state_id,
                    to_state=state.state_id,
                    timestamp=Timestamp.now(),
                    reason=f"Cycling to {state.name}",
                )
                logger.info(f"State transition: {prev_state.name} -> {state.name}")

            logger.info(
                f"Testing state: {state.name} (target: {state.metadata.get('target_voltage')}V)"
            )

            # Run echo cycle
            readings = await self._run_echo_cycle(
                uut_client, mcc152_dac, mcc118_adc, state, definition, rack_instance
            )

            # Log telemetry
            await self._log_telemetry(telemetry_logger, readings)

            # Evaluate with monitor (use the final measured voltage)
            result = monitor.evaluate(readings.rack_adc_voltage, state)
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
        environmental_states: list[EnvironmentalState],
        test_definition: TestDefinition,
        rack_instance: RackInstanceConfig,
        cancellation_token: CancellationToken,
        telemetry_logger: Any,
    ) -> None:
        """Run voltage echo test with monitoring.

        Test mode is determined by TEST_MODE environment variable:
        - functional: Single pass (default)
        - halt: Continuous until failure or cancelled
        - hass: Continuous until failure or cancelled

        Test parameters are loaded from configs/voltage_echo_monitor.yaml.
        Hardware calibration is loaded from the rack instance configuration.
        """
        test_mode = get_execution_mode()
        stats = RunStatistics()
        tolerance = test_definition.parameters.voltage_tolerance

        logger.info(f"Starting voltage echo monitor test in {test_mode.value.upper()} mode")
        logger.info(f"Definition: {test_definition.source_path}")
        logger.info(
            f"Rack instance: {rack_instance.instance.rack_class} "
            f"#{rack_instance.instance.serial_number}"
        )
        logger.info(f"States: {[s.name for s in environmental_states]}")
        logger.info(f"Voltage tolerance: ±{tolerance}V")
        logger.info(f"Settling time: {test_definition.parameters.settling_time_seconds}s")
        logger.info(
            f"Calibration: UUT ADC={rack_instance.get_calibration('uut_adc_scale_factor', 1.0)}, "
            f"MCC118={rack_instance.get_calibration('mcc118_scale_factor', 1.0)}"
        )
        if telemetry_logger:
            logger.info("Telemetry logging: ENABLED")
        else:
            logger.info("Telemetry logging: DISABLED")

        try:
            if test_mode == ExecutionMode.FUNCTIONAL:
                # Single pass
                failure = await self._run_single_pass(
                    uut_client,
                    mcc152_dac,
                    mcc118_adc,
                    voltage_monitor,
                    environmental_states,
                    test_definition,
                    rack_instance,
                    stats,
                    telemetry_logger=telemetry_logger,
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
                        uut_client,
                        mcc152_dac,
                        mcc118_adc,
                        voltage_monitor,
                        environmental_states,
                        test_definition,
                        rack_instance,
                        stats,
                        telemetry_logger=telemetry_logger,
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
