"""Tests for test definition loading (Rust-backed)."""

from pathlib import Path

import pytest

from hwtest_core.errors import SpecError
from hwtest_core.types.bounds import Special, WithinRange
from hwtest_testcase import TestDefinition, find_definition_file, load_definition

REPO = Path(__file__).resolve().parents[3]
ECHO_YAML = REPO / "hwtest-intg" / "configs" / "voltage_echo_monitor.yaml"

DEFINITION = """
test_case: {id: demo, name: Demo, version: "2.0"}
rack: bench_rack
case_parameters: {settle_s: 0.1, retries: 3}
monitor_states:
  cold: {name: Cold, target_voltage: 1.0, settle_s: 0.5}
  hot: {name: Hot, description: Hot soak, target_voltage: 4.0}
state_sequence: [hot, cold]
monitors:
  uut_mon:
    kind: bounds
    kwargs.2: {channel: v}
    configuration:
      default: {v: {special: any}}
      cold: {v: {within_range: [1.0, 0.1]}}
  sys_mon:
    module: hwtest_testcase
    class: Monitor
    kwargs: {}
    configuration:
      hot: {t: {less_than: 85}}
loggers:
  influx: {kind: influxdb, ignore_offsite: true, kwargs: {token: "${HWTEST_TEST_TOKEN:abc}"}}
  csv: {module: hwtest_logger, class: CsvStreamLogger, topics: [demo]}
  disabled: {kind: csv, enabled: false}
"""


@pytest.fixture
def definition() -> TestDefinition:
    return TestDefinition.from_yaml_str(DEFINITION)


class TestLoading:
    def test_repository_definition(self) -> None:
        d = TestDefinition.from_yaml(ECHO_YAML)
        assert d.test_case.id == "voltage_echo_monitor"
        assert d.rack_id == "pi5_mcc_intg_a_rack"
        assert d.state_sequence == ["minimum", "middle", "maximum"]
        assert d.source_path == ECHO_YAML
        assert [s.target_voltage for s in d.get_states_in_sequence()] == [1.0, 2.5, 4.0]

    def test_find_and_load(self, tmp_path: Path) -> None:
        (tmp_path / "demo_definition.yml").write_text(DEFINITION)
        assert find_definition_file("demo", [tmp_path]) == tmp_path / "demo_definition.yml"
        assert find_definition_file("absent", [tmp_path]) is None
        assert load_definition("demo", search_paths=[tmp_path]).test_case.name == "Demo"
        with pytest.raises(FileNotFoundError, match="TEST_DEFINITION_PATH"):
            load_definition("absent", search_paths=[tmp_path])

    def test_from_dict(self) -> None:
        d = TestDefinition.from_dict({"test_case": {"id": "x"}, "monitor_states": {"a": {}}})
        assert d.state_sequence == ["a"]


class TestModel:
    def test_info_and_parameters(self, definition: TestDefinition) -> None:
        assert (definition.test_case.id, definition.test_case.version) == ("demo", "2.0")
        assert definition.case_parameters == {"settle_s": 0.1, "retries": 3}
        assert definition.get_parameter("settle_s") == 0.1
        assert definition.get_parameter("settle_s", "cold") == 0.5
        assert definition.get_parameter("settle_s", "hot") == 0.1
        assert definition.get_parameter("target_voltage", "hot") == 4.0
        with pytest.raises(KeyError):
            definition.get_parameter("missing")

    def test_states(self, definition: TestDefinition) -> None:
        hot = definition.get_state("hot")
        assert (hot.name, hot.description, hot.target_voltage) == ("Hot", "Hot soak", 4.0)
        assert definition.get_state("cold").parameters == {"target_voltage": 1.0, "settle_s": 0.5}
        assert [s.id for s in definition.get_states_in_sequence()] == ["hot", "cold"]
        env = definition.get_state("cold").to_environmental_state()
        assert env.state_id == "cold" and env.metadata["target_voltage"] == 1.0
        with pytest.raises(KeyError):
            definition.get_state("warm")

    def test_monitors(self, definition: TestDefinition) -> None:
        uut = definition.get_monitor("uut_mon")
        assert (uut.kind, uut.slot_number, uut.is_uut_monitor) == ("bounds", 2, True)
        assert uut.kwargs == {"channel": "v"}
        assert uut.get_bounds("cold", "v") == WithinRange(1.0, 0.1)
        assert uut.get_bounds("hot", "v") == Special("any")  # falls back to default
        assert uut.get_bounds("hot", "other") is None
        assert uut.get_all_fields() == {"v"}
        sys_mon = definition.get_monitor("sys_mon")
        assert (sys_mon.kind, sys_mon.module, sys_mon.class_name) == (
            "bounds",
            "hwtest_testcase",
            "Monitor",
        )
        assert sys_mon.is_system_monitor

    def test_loggers(self, definition: TestDefinition) -> None:
        assert [lg.name for lg in definition.get_enabled_loggers()] == ["influx", "csv"]
        assert [lg.name for lg in definition.get_enabled_loggers(offsite=True)] == ["csv"]
        csv = definition.get_logger("csv")
        assert (csv.kind, csv.module, csv.class_name, csv.topics) == (
            "csv",
            "hwtest_logger",
            "CsvStreamLogger",
            ["demo"],
        )
        influx = definition.get_logger("influx")
        assert influx.kwargs == {"token": "${HWTEST_TEST_TOKEN:abc}"}
        assert influx.resolved_kwargs() == {"token": "abc"}

    def test_logger_env_override(
        self, definition: TestDefinition, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HWTEST_TEST_TOKEN", "secret")
        assert definition.get_logger("influx").resolved_kwargs() == {"token": "secret"}


class TestValidation:
    def test_all_problems_reported_with_locations(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "test_case: {id: t}\n"
            "monitor_states: {low: {}, middle: {}}\n"
            "state_sequence: [low, midle]\n"
            "monitors:\n"
            "  m:\n"
            "    configuration:\n"
            "      low: {v: {within_range: [1.0]}}\n"
        )
        with pytest.raises(SpecError) as err:
            TestDefinition.from_yaml(bad)
        msg = str(err.value)
        assert msg.startswith(f"{bad}: 2 problems:")
        assert f'{bad}:3:23: state_sequence[1]: unknown state "midle"' in msg
        assert 'did you mean "middle"?' in msg
        assert f"{bad}:7:16: monitors.m.configuration.low.v: within_range: expected" in msg

    def test_spec_error_is_value_error(self) -> None:
        with pytest.raises(ValueError):
            TestDefinition.from_yaml_str("test_case: {id: t}\nmonitor_sates: {}\n")

    def test_typo_in_special_kind_rejected(self) -> None:
        with pytest.raises(SpecError, match="Unknown special bound kind"):
            TestDefinition.from_yaml_str(
                "test_case: {id: t}\nmonitor_states: {s: {}}\n"
                "monitors: {m: {configuration: {s: {v: {special: anyy}}}}}\n"
            )
