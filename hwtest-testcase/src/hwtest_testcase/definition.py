"""Test case definition loader.

This module provides utilities for loading test case definitions from YAML files.
Test definitions externalize parameters that may need tuning (voltages, tolerances,
timing) from the Python test code, enabling:

- Non-developer review of test parameters
- Parameter changes without code review
- Clear documentation for managers and engineers
- External configuration sources

Test case YAML defines test logic (states, monitors, parameters).
Rack YAML defines hardware specifics (instruments, channels).
Rack instance YAML defines per-unit calibration.

Example test case YAML structure:

    test_case:
      id: voltage_echo_monitor
      name: Voltage Echo Monitor Test
      version: "1.0.0"

    rack: pi5_mcc_intg_a_rack

    case_parameters:
      settling_time_seconds: 0.025

    monitor_states:
      minimum:
        name: Minimum Voltage
        target_voltage: 1.0
      middle:
        name: Middle Voltage
        target_voltage: 2.5

    state_sequence:
      - minimum
      - middle

    monitors:
      echo_voltage_monitor:
        module: hwtest_intg.monitors.echo_voltage
        class: EchoVoltageMonitor
        kwargs:
          channel: echo_voltage
        configuration:
          default:
            echo_voltage:
              special: any
          minimum:
            echo_voltage:
              within_range: [1.0, 0.30]

Usage:

    from hwtest_testcase.definition import TestDefinition

    definition = TestDefinition.from_yaml("path/to/test.yaml")

    # Access parameters
    settling_time = definition.case_parameters["settling_time_seconds"]

    # Get rack reference
    rack_id = definition.rack_id

    # Get monitor configuration
    monitor = definition.monitors["echo_voltage_monitor"]
    bounds = monitor.get_bounds("minimum", "echo_voltage")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# =============================================================================
# Bound Specifications
# =============================================================================


@dataclass(frozen=True)
class BoundSpec:
    """Specification for a measurement bound.

    Supports multiple bound types:
    - within_range: [center, tolerance] - value within center ± tolerance
    - good_interval: [low, high] - value within [low, high]
    - less_than: value - value < threshold
    - greater_than: value - value > threshold
    - good_values: [list] - value in discrete set
    - special: "any" - always pass (no check)

    Attributes:
        bound_type: Type of bound (within_range, good_interval, etc.).
        value: The bound value(s) - interpretation depends on bound_type.
    """

    bound_type: str
    value: Any

    def check(self, measured: float) -> bool:
        """Check if a measured value satisfies this bound.

        Args:
            measured: The measured value to check.

        Returns:
            True if the value satisfies the bound.
        """
        if self.bound_type == "special" and self.value == "any":
            return True

        if self.bound_type == "within_range":
            center, tolerance = self.value
            return abs(measured - center) <= tolerance

        if self.bound_type == "good_interval":
            low, high = self.value
            return low <= measured <= high

        if self.bound_type == "less_than":
            return measured < self.value

        if self.bound_type == "greater_than":
            return measured > self.value

        if self.bound_type == "good_values":
            return measured in self.value

        # Unknown bound type - fail safe
        return False

    @property
    def is_any(self) -> bool:
        """Check if this is a 'special: any' bound (always pass)."""
        return self.bound_type == "special" and self.value == "any"

    def to_interval(self) -> tuple[float, float] | None:
        """Convert to [low, high] interval if possible.

        Returns:
            Tuple of (low, high) bounds, or None if not representable.
        """
        if self.bound_type == "within_range":
            center, tolerance = self.value
            return (center - tolerance, center + tolerance)

        if self.bound_type == "good_interval":
            return tuple(self.value)

        if self.bound_type == "less_than":
            return (float("-inf"), self.value)

        if self.bound_type == "greater_than":
            return (self.value, float("inf"))

        return None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BoundSpec:
        """Parse a bound specification from a dictionary.

        Args:
            data: Dictionary with exactly one key (bound type) and value.

        Returns:
            Parsed BoundSpec.

        Raises:
            ValueError: If dictionary doesn't have exactly one key.
        """
        if len(data) != 1:
            raise ValueError(f"Bound spec must have exactly one key, got: {list(data.keys())}")

        bound_type = next(iter(data.keys()))
        value = data[bound_type]

        return cls(bound_type=bound_type, value=value)


# =============================================================================
# Monitor Configuration
# =============================================================================


@dataclass
class MonitorDef:
    """Definition for a monitor loaded from YAML.

    Monitors can be either system monitors or UUT monitors:
    - System monitors (kwargs:) detect test system failures
    - UUT monitors (kwargs.N:) detect UUT failures for slot N

    Attributes:
        name: Monitor name (key in the monitors dict).
        module: Python module containing the monitor class.
        class_name: Name of the monitor class.
        kwargs: Arguments passed to monitor constructor.
        configuration: Per-state bounds for each field.
        slot_number: UUT slot number (None for system monitors).
    """

    name: str
    module: str
    class_name: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    configuration: dict[str, dict[str, BoundSpec]] = field(default_factory=dict)
    slot_number: int | None = None

    @property
    def is_uut_monitor(self) -> bool:
        """Check if this is a UUT monitor (associated with a slot).

        Returns:
            True if this monitor detects UUT failures.
        """
        return self.slot_number is not None

    @property
    def is_system_monitor(self) -> bool:
        """Check if this is a system monitor (no slot association).

        Returns:
            True if this monitor detects test system failures.
        """
        return self.slot_number is None

    def get_bounds(self, state_id: str, field_name: str) -> BoundSpec | None:
        """Get bounds for a field in a specific state.

        Falls back to 'default' state if the specific state doesn't define bounds.

        Args:
            state_id: The state identifier.
            field_name: The field name to get bounds for.

        Returns:
            BoundSpec for the field, or None if not defined.
        """
        # Try specific state first
        if state_id in self.configuration:
            state_bounds = self.configuration[state_id]
            if field_name in state_bounds:
                return state_bounds[field_name]

        # Fall back to default
        if "default" in self.configuration:
            default_bounds = self.configuration["default"]
            if field_name in default_bounds:
                return default_bounds[field_name]

        return None

    def get_all_fields(self) -> set[str]:
        """Get all field names that have bounds defined.

        Returns:
            Set of field names.
        """
        fields: set[str] = set()
        for state_bounds in self.configuration.values():
            fields.update(state_bounds.keys())
        return fields


# =============================================================================
# State Definition
# =============================================================================


@dataclass(frozen=True)
class MonitorState:
    """Environmental state definition.

    Attributes:
        id: Unique identifier for the state.
        name: Human-readable name.
        description: Detailed description of the state.
        target_voltage: Target voltage for this state (V).
        parameters: State-specific parameter overrides.
        metadata: Additional key-value data for the state.
    """

    id: str
    name: str
    description: str = ""
    target_voltage: float = 0.0
    parameters: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Test Case Info
# =============================================================================


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


# =============================================================================
# Test Definition
# =============================================================================


@dataclass
class TestDefinition:
    """Complete test case definition loaded from YAML.

    This class represents all the configuration for a test case, including
    parameters, states, and monitor configurations.

    Attributes:
        test_case: Test case metadata.
        rack_id: Reference to the rack configuration.
        case_parameters: Global test parameters.
        monitor_states: Map of state IDs to state definitions.
        state_sequence: Ordered list of state IDs for test execution.
        monitors: Map of monitor names to monitor definitions.
        functional_requirements: Point-in-time check definitions (optional).
        source_path: Path to the YAML file (if loaded from file).
    """

    test_case: TestCaseInfo
    rack_id: str | None
    case_parameters: dict[str, Any]
    monitor_states: dict[str, MonitorState]
    state_sequence: list[str]
    monitors: dict[str, MonitorDef]
    functional_requirements: dict[str, Any] = field(default_factory=dict)
    source_path: Path | None = None

    def get_state(self, state_id: str) -> MonitorState:
        """Get a state definition by ID.

        Args:
            state_id: The state identifier.

        Returns:
            The state definition.

        Raises:
            KeyError: If state not found.
        """
        return self.monitor_states[state_id]

    def get_monitor(self, monitor_name: str) -> MonitorDef:
        """Get a monitor definition by name.

        Args:
            monitor_name: The monitor name.

        Returns:
            The monitor definition.

        Raises:
            KeyError: If monitor not found.
        """
        return self.monitors[monitor_name]

    def get_parameter(self, name: str, state_id: str | None = None) -> Any:
        """Get a parameter value, with optional state override.

        Args:
            name: Parameter name.
            state_id: Optional state ID to check for override.

        Returns:
            Parameter value (state override if present, else global).

        Raises:
            KeyError: If parameter not found.
        """
        # Check state override first
        if state_id and state_id in self.monitor_states:
            state = self.monitor_states[state_id]
            if name in state.parameters:
                return state.parameters[name]

        # Fall back to global
        return self.case_parameters[name]

    def get_states_in_sequence(self) -> list[MonitorState]:
        """Get states in execution order.

        Returns:
            List of MonitorState objects in sequence order.
        """
        return [self.monitor_states[sid] for sid in self.state_sequence]

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
        """
        path = Path(path)

        with open(path, encoding="utf-8") as f:
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

        # Parse case parameters
        case_parameters = dict(data.get("case_parameters", {}))

        # Parse monitor states
        monitor_states: dict[str, MonitorState] = {}
        for state_id, state_data in data.get("monitor_states", {}).items():
            # Extract parameter overrides (anything not a known field)
            known_keys = {"name", "description", "target_voltage"}
            parameters = {k: v for k, v in state_data.items() if k not in known_keys}

            monitor_states[state_id] = MonitorState(
                id=state_id,
                name=state_data.get("name", state_id),
                description=state_data.get("description", ""),
                target_voltage=state_data.get("target_voltage", 0.0),
                parameters=parameters,
            )

        # Parse state sequence
        state_sequence = list(data.get("state_sequence", list(monitor_states.keys())))

        # Parse monitors
        monitors: dict[str, MonitorDef] = {}
        for monitor_name, monitor_data in data.get("monitors", {}).items():
            configuration: dict[str, dict[str, BoundSpec]] = {}

            for state_id, state_config in monitor_data.get("configuration", {}).items():
                field_bounds: dict[str, BoundSpec] = {}
                for field_name, bound_data in state_config.items():
                    field_bounds[field_name] = BoundSpec.from_dict(bound_data)
                configuration[state_id] = field_bounds

            # Parse kwargs - look for "kwargs" or "kwargs.N" pattern
            # "kwargs" = system monitor, "kwargs.N" = UUT monitor for slot N
            kwargs: dict[str, Any] = {}
            slot_number: int | None = None

            for key in monitor_data.keys():
                if key == "kwargs":
                    kwargs = dict(monitor_data[key] or {})
                    break
                elif key.startswith("kwargs."):
                    try:
                        slot_number = int(key.split(".", 1)[1])
                        kwargs = dict(monitor_data[key] or {})
                        break
                    except (ValueError, IndexError):
                        # Invalid slot number format, skip
                        pass

            monitors[monitor_name] = MonitorDef(
                name=monitor_name,
                module=monitor_data.get("module", ""),
                class_name=monitor_data.get("class", ""),
                kwargs=kwargs,
                configuration=configuration,
                slot_number=slot_number,
            )

        # Parse functional requirements (optional)
        functional_requirements = dict(data.get("functional_requirements", {}))

        return cls(
            test_case=test_case,
            rack_id=rack_id,
            case_parameters=case_parameters,
            monitor_states=monitor_states,
            state_sequence=state_sequence,
            monitors=monitors,
            functional_requirements=functional_requirements,
            source_path=source_path,
        )


# =============================================================================
# Utility Functions
# =============================================================================


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
