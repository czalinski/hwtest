"""Test case definition loader.

This module provides utilities for loading test case definitions from YAML files.
Test definitions externalize parameters that may need tuning (voltages, tolerances,
timing) from the Python test code, enabling:

- Non-developer review of test parameters
- Parameter changes without code review
- Clear documentation for managers and engineers
- External configuration sources

Test case YAML defines test logic (states, thresholds, timing).
Rack YAML defines hardware specifics (instruments, channels, calibration).

Example test case YAML structure:

    test_case:
      id: voltage_echo_monitor
      name: Voltage Echo Monitor Test
      version: "1.0.0"

    rack: pi5_mcc_intg_a_rack  # Reference to rack config

    parameters:
      settling_time_seconds: 0.025
      voltage_tolerance: 0.30

    states:
      minimum:
        name: Minimum Voltage
        target_voltage: 1.0
        thresholds:
          echo_voltage:
            low: 0.70
            high: 1.30

    state_sequence:
      - minimum
      - middle
      - maximum

Usage:

    from hwtest_testcase.definition import TestDefinition

    # Load from file
    definition = TestDefinition.from_yaml("path/to/test.yaml")

    # Access parameters
    settling_time = definition.parameters.settling_time_seconds
    tolerance = definition.parameters.voltage_tolerance

    # Get rack reference (load rack config separately)
    rack_id = definition.rack_id  # e.g., "pi5_mcc_intg_a_rack"

    # Get state configuration
    state = definition.get_state("minimum")
    target_voltage = state.target_voltage
    threshold_low = state.thresholds["echo_voltage"].low

    # Iterate states in sequence
    for state in definition.state_sequence:
        print(f"Testing {state.name} at {state.target_voltage}V")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ThresholdDef:
    """Threshold definition for a measurement channel.

    Attributes:
        low: Lower bound (inclusive). None means no lower bound.
        high: Upper bound (inclusive). None means no upper bound.
    """

    low: float | None = None
    high: float | None = None

    def check(self, value: float) -> bool:
        """Check if a value is within the threshold bounds.

        Args:
            value: The value to check.

        Returns:
            True if value is within bounds.
        """
        if self.low is not None and value < self.low:
            return False
        if self.high is not None and value > self.high:
            return False
        return True


@dataclass(frozen=True)
class StateDef:
    """Environmental state definition.

    Attributes:
        id: Unique identifier for the state.
        name: Human-readable name.
        description: Detailed description of the state.
        target_voltage: Target voltage for this state (V).
        thresholds: Map of channel names to threshold definitions.
        metadata: Additional key-value data for the state.
    """

    id: str
    name: str
    description: str = ""
    target_voltage: float = 0.0
    thresholds: dict[str, ThresholdDef] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CalibrationDef:
    """Calibration factors for hardware.

    Attributes:
        uut_adc_scale_factor: Scale factor for UUT ADC readings.
        mcc118_scale_factor: Scale factor for MCC 118 ADC readings.
        extra: Additional calibration factors.
    """

    uut_adc_scale_factor: float = 1.0
    mcc118_scale_factor: float = 1.0
    extra: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ParametersDef:
    """Global test parameters.

    Attributes:
        settling_time_seconds: Time to wait after voltage changes.
        voltage_tolerance: Default voltage tolerance (±V).
        extra: Additional parameters.
    """

    settling_time_seconds: float = 0.025
    voltage_tolerance: float = 0.30
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChannelDef:
    """Hardware channel mapping.

    Attributes:
        instrument: Instrument type/name.
        channel: Channel number or identifier.
        description: Human-readable description.
    """

    instrument: str
    channel: int | str
    description: str = ""


@dataclass(frozen=True)
class TestCaseInfo:
    """Test case metadata.

    Attributes:
        id: Unique identifier.
        name: Human-readable name.
        version: Version string.
        description: Detailed description.
    """

    id: str
    name: str
    version: str = "1.0.0"
    description: str = ""


@dataclass
class TestDefinition:
    """Complete test case definition loaded from YAML.

    This class represents the test case configuration including parameters
    and states. Hardware-specific configuration (channels, calibration) is
    defined in the rack configuration file referenced by rack_id.

    Attributes:
        test_case: Test case metadata.
        parameters: Global test parameters.
        states: Map of state IDs to state definitions.
        state_sequence: Ordered list of state definitions for test execution.
        rack_id: Reference to the rack configuration (e.g., "pi5_mcc_intg_a_rack").
        calibration: Hardware calibration factors (deprecated, use rack config).
        channels: Map of logical channel names (deprecated, use rack config).
        source_path: Path to the YAML file (if loaded from file).
    """

    test_case: TestCaseInfo
    parameters: ParametersDef
    states: dict[str, StateDef]
    state_sequence: list[StateDef]
    rack_id: str | None = None
    calibration: CalibrationDef = field(default_factory=CalibrationDef)
    channels: dict[str, ChannelDef] = field(default_factory=dict)
    source_path: Path | None = None

    def get_state(self, state_id: str) -> StateDef:
        """Get a state definition by ID.

        Args:
            state_id: The state identifier.

        Returns:
            The state definition.

        Raises:
            KeyError: If state not found.
        """
        return self.states[state_id]

    def get_channel(self, channel_name: str) -> ChannelDef:
        """Get a channel definition by name.

        Args:
            channel_name: The logical channel name.

        Returns:
            The channel definition.

        Raises:
            KeyError: If channel not found.
        """
        return self.channels[channel_name]

    @classmethod
    def from_yaml(cls, path: str | Path) -> TestDefinition:
        """Load a test definition from a YAML file.

        Args:
            path: Path to the YAML file.

        Returns:
            Parsed TestDefinition.

        Raises:
            FileNotFoundError: If file doesn't exist.
            yaml.YAMLError: If YAML parsing fails.
            KeyError: If required fields are missing.
        """
        path = Path(path)

        with open(path) as f:
            data = yaml.safe_load(f)

        return cls._parse(data, source_path=path)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestDefinition:
        """Load a test definition from a dictionary.

        Args:
            data: Dictionary with test definition data.

        Returns:
            Parsed TestDefinition.
        """
        return cls._parse(data, source_path=None)

    @classmethod
    def _parse(cls, data: dict[str, Any], source_path: Path | None) -> TestDefinition:
        """Parse test definition from dictionary.

        Args:
            data: Raw dictionary data.
            source_path: Source file path (if any).

        Returns:
            Parsed TestDefinition.
        """
        # Parse test case info
        tc_data = data.get("test_case", {})
        test_case = TestCaseInfo(
            id=tc_data.get("id", "unknown"),
            name=tc_data.get("name", "Unknown Test"),
            version=tc_data.get("version", "1.0.0"),
            description=tc_data.get("description", ""),
        )

        # Parse rack reference
        rack_id = data.get("rack")

        # Parse parameters
        param_data = data.get("parameters", {})
        parameters = ParametersDef(
            settling_time_seconds=param_data.get("settling_time_seconds", 0.025),
            voltage_tolerance=param_data.get("voltage_tolerance", 0.30),
            extra={k: v for k, v in param_data.items()
                   if k not in ("settling_time_seconds", "voltage_tolerance")},
        )

        # Parse calibration (deprecated - should come from rack config)
        cal_data = data.get("calibration", {})
        calibration = CalibrationDef(
            uut_adc_scale_factor=cal_data.get("uut_adc_scale_factor", 1.0),
            mcc118_scale_factor=cal_data.get("mcc118_scale_factor", 1.0),
            extra={k: v for k, v in cal_data.items()
                   if k not in ("uut_adc_scale_factor", "mcc118_scale_factor")},
        )

        # Parse states
        states: dict[str, StateDef] = {}
        for state_id, state_data in data.get("states", {}).items():
            thresholds: dict[str, ThresholdDef] = {}
            for thresh_name, thresh_data in state_data.get("thresholds", {}).items():
                thresholds[thresh_name] = ThresholdDef(
                    low=thresh_data.get("low"),
                    high=thresh_data.get("high"),
                )

            # Collect any extra fields as metadata
            known_keys = {"name", "description", "target_voltage", "thresholds"}
            metadata = {k: v for k, v in state_data.items() if k not in known_keys}

            states[state_id] = StateDef(
                id=state_id,
                name=state_data.get("name", state_id),
                description=state_data.get("description", ""),
                target_voltage=state_data.get("target_voltage", 0.0),
                thresholds=thresholds,
                metadata=metadata,
            )

        # Parse state sequence
        sequence_ids = data.get("state_sequence", list(states.keys()))
        state_sequence = [states[sid] for sid in sequence_ids if sid in states]

        # Parse channels (deprecated - should come from rack config)
        channels: dict[str, ChannelDef] = {}
        for ch_name, ch_data in data.get("channels", {}).items():
            channels[ch_name] = ChannelDef(
                instrument=ch_data.get("instrument", "unknown"),
                channel=ch_data.get("channel", 0),
                description=ch_data.get("description", ""),
            )

        return cls(
            test_case=test_case,
            parameters=parameters,
            states=states,
            state_sequence=state_sequence,
            rack_id=rack_id,
            calibration=calibration,
            channels=channels,
            source_path=source_path,
        )


def find_definition_file(
    test_id: str,
    search_paths: list[str | Path] | None = None,
) -> Path | None:
    """Find a test definition file by test ID.

    Searches for a YAML file matching the test ID in the given search paths.

    Args:
        test_id: Test case identifier (e.g., "voltage_echo_monitor").
        search_paths: List of directories to search. If None, searches:
            - Current directory
            - ./configs/
            - Environment variable TEST_DEFINITION_PATH (colon-separated)

    Returns:
        Path to the definition file, or None if not found.
    """
    import os

    if search_paths is None:
        search_paths = [
            Path.cwd(),
            Path.cwd() / "configs",
        ]
        # Add paths from environment variable
        env_paths = os.environ.get("TEST_DEFINITION_PATH", "")
        if env_paths:
            search_paths.extend(Path(p) for p in env_paths.split(":") if p)

    # Look for the file
    filenames = [
        f"{test_id}.yaml",
        f"{test_id}.yml",
        f"{test_id}_definition.yaml",
        f"{test_id}_definition.yml",
    ]

    for search_dir in search_paths:
        search_dir = Path(search_dir)
        if not search_dir.is_dir():
            continue
        for filename in filenames:
            candidate = search_dir / filename
            if candidate.is_file():
                return candidate

    return None


def load_definition(
    test_id: str,
    path: str | Path | None = None,
    search_paths: list[str | Path] | None = None,
) -> TestDefinition:
    """Load a test definition by ID or explicit path.

    Args:
        test_id: Test case identifier.
        path: Explicit path to definition file (overrides search).
        search_paths: Directories to search if path not given.

    Returns:
        Loaded TestDefinition.

    Raises:
        FileNotFoundError: If definition file not found.
    """
    if path is not None:
        return TestDefinition.from_yaml(path)

    found = find_definition_file(test_id, search_paths)
    if found is None:
        raise FileNotFoundError(
            f"Test definition not found for '{test_id}'. "
            f"Set TEST_DEFINITION_PATH or provide explicit path."
        )

    return TestDefinition.from_yaml(found)
