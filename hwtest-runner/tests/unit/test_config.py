"""Tests for station configuration loading."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hwtest_runner.config import (
    RackReference,
    StationConfig,
    TestCaseEntry,
    UutConfig,
    load_station_config,
)


@pytest.fixture
def station_yaml(tmp_path: Path) -> Path:
    """Create a valid station config YAML file."""
    config = tmp_path / "station.yaml"
    config.write_text(
        textwrap.dedent("""\
        station:
          id: "test-station"
          description: "Test station"

        rack:
          config: "test_rack"
          serial: "001"

        uut:
          url: "http://192.168.1.100:8080"

        test_cases:
          - id: "test_a"
            name: "Test A"
            definition: "test_a_def"
            modes: [functional, hass]
          - id: "test_b"
            name: "Test B"
            definition: "test_b_def"
            modes: [functional]
        """)
    )
    return config


class TestLoadStationConfig:
    """Tests for load_station_config."""

    def test_loads_valid_config(self, station_yaml: Path) -> None:
        config = load_station_config(station_yaml)

        assert config.id == "test-station"
        assert config.description == "Test station"
        assert config.rack.config == "test_rack"
        assert config.rack.serial == "001"
        assert config.uut.url == "http://192.168.1.100:8080"
        assert len(config.test_cases) == 2

    def test_test_case_entries(self, station_yaml: Path) -> None:
        config = load_station_config(station_yaml)

        tc_a = config.test_cases[0]
        assert tc_a.id == "test_a"
        assert tc_a.name == "Test A"
        assert tc_a.definition == "test_a_def"
        assert tc_a.modes == ["functional", "hass"]

        tc_b = config.test_cases[1]
        assert tc_b.id == "test_b"
        assert tc_b.modes == ["functional"]

    def test_get_test_case_found(self, station_yaml: Path) -> None:
        config = load_station_config(station_yaml)
        tc = config.get_test_case("test_a")
        assert tc is not None
        assert tc.name == "Test A"

    def test_get_test_case_not_found(self, station_yaml: Path) -> None:
        config = load_station_config(station_yaml)
        assert config.get_test_case("nonexistent") is None

    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_station_config(tmp_path / "missing.yaml")

    def test_missing_station_id(self, tmp_path: Path) -> None:
        config = tmp_path / "bad.yaml"
        config.write_text("station:\n  description: foo\nrack:\n  config: x\nuut:\n  url: x\n")
        with pytest.raises(ValueError, match="station.id"):
            load_station_config(config)

    def test_missing_rack_config(self, tmp_path: Path) -> None:
        config = tmp_path / "bad.yaml"
        config.write_text("station:\n  id: x\nrack:\n  serial: '1'\nuut:\n  url: x\n")
        with pytest.raises(ValueError, match="rack.config"):
            load_station_config(config)

    def test_missing_uut_url(self, tmp_path: Path) -> None:
        config = tmp_path / "bad.yaml"
        config.write_text("station:\n  id: x\nrack:\n  config: y\nuut:\n  foo: bar\n")
        with pytest.raises(ValueError, match="uut.url"):
            load_station_config(config)

    def test_optional_serial(self, tmp_path: Path) -> None:
        config = tmp_path / "no_serial.yaml"
        config.write_text(
            textwrap.dedent("""\
            station:
              id: "s1"
            rack:
              config: "r1"
            uut:
              url: "http://localhost:8080"
            test_cases: []
            """)
        )
        result = load_station_config(config)
        assert result.rack.serial is None
        assert result.test_cases == []

    def test_default_modes(self, tmp_path: Path) -> None:
        config = tmp_path / "defaults.yaml"
        config.write_text(
            textwrap.dedent("""\
            station:
              id: "s1"
            rack:
              config: "r1"
            uut:
              url: "http://localhost:8080"
            test_cases:
              - id: "tc1"
                name: "TC 1"
                definition: "tc1"
            """)
        )
        result = load_station_config(config)
        assert result.test_cases[0].modes == ["functional"]
