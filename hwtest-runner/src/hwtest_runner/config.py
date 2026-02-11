"""Station configuration loading for hwtest-runner.

A station config ties together a rack class, rack instance, UUT, and
available test cases into a single YAML file.

Example YAML:
    station:
      id: "pi5-bench-a"
      description: "Pi 5 integration bench A"

    rack:
      config: "pi5_mcc_intg_a_rack"
      serial: "001"

    uut:
      url: "http://192.168.68.4:8080"

    test_cases:
      - id: "voltage_echo_monitor"
        name: "Voltage Echo Monitor"
        definition: "voltage_echo_monitor"
        modes: [functional, hass, halt]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class UutConfig:
    """UUT connection configuration.

    Attributes:
        url: URL of the UUT simulator REST API.
    """

    url: str


@dataclass(frozen=True)
class RackReference:
    """Reference to a rack class configuration and optional instance serial.

    Attributes:
        config: Rack class YAML name (searched via hwtest-intg config paths).
        serial: Rack instance serial number for calibration lookup.
    """

    config: str
    serial: str | None = None


@dataclass(frozen=True)
class TestCaseEntry:
    """A test case available for execution at this station.

    Attributes:
        id: Unique identifier for the test case.
        name: Human-readable display name.
        definition: Test definition YAML name (searched via hwtest-testcase paths).
        modes: Available execution modes (functional, hass, halt).
    """

    id: str
    name: str
    definition: str
    modes: list[str] = field(default_factory=lambda: ["functional"])


@dataclass(frozen=True)
class StationConfig:
    """Complete station configuration.

    Attributes:
        id: Unique station identifier.
        description: Human-readable description.
        rack: Rack class and instance reference.
        uut: UUT connection configuration.
        test_cases: Available test cases at this station.
    """

    id: str
    description: str
    rack: RackReference
    uut: UutConfig
    test_cases: list[TestCaseEntry] = field(default_factory=list)

    def get_test_case(self, test_case_id: str) -> TestCaseEntry | None:
        """Find a test case by ID.

        Args:
            test_case_id: The test case identifier.

        Returns:
            TestCaseEntry if found, None otherwise.
        """
        for tc in self.test_cases:
            if tc.id == test_case_id:
                return tc
        return None


def load_station_config(path: str | Path) -> StationConfig:
    """Load station configuration from a YAML file.

    Args:
        path: Path to the station configuration YAML file.

    Returns:
        Parsed StationConfig.

    Raises:
        FileNotFoundError: If the config file doesn't exist.
        ValueError: If required fields are missing.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Station config not found: {path}")

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError("Station config must be a YAML mapping")

    # Parse station section
    station_data = data.get("station", {})
    station_id = station_data.get("id")
    if not station_id:
        raise ValueError("Missing required field: station.id")
    description = station_data.get("description", "")

    # Parse rack section
    rack_data = data.get("rack", {})
    rack_config_name = rack_data.get("config")
    if not rack_config_name:
        raise ValueError("Missing required field: rack.config")
    rack = RackReference(
        config=rack_config_name,
        serial=rack_data.get("serial"),
    )

    # Parse uut section
    uut_data = data.get("uut", {})
    uut_url = uut_data.get("url")
    if not uut_url:
        raise ValueError("Missing required field: uut.url")
    uut = UutConfig(url=uut_url)

    # Parse test_cases section
    test_cases_data = data.get("test_cases", [])
    if not isinstance(test_cases_data, list):
        raise ValueError("test_cases must be a list")

    test_cases: list[TestCaseEntry] = []
    for tc_data in test_cases_data:
        if not isinstance(tc_data, dict):
            raise ValueError("Each test case must be a mapping")

        tc_id = tc_data.get("id")
        if not tc_id:
            raise ValueError("Test case missing required field: id")

        test_cases.append(
            TestCaseEntry(
                id=tc_id,
                name=tc_data.get("name", tc_id),
                definition=tc_data.get("definition", tc_id),
                modes=tc_data.get("modes", ["functional"]),
            )
        )

    return StationConfig(
        id=station_id,
        description=description,
        rack=rack,
        uut=uut,
        test_cases=test_cases,
    )
