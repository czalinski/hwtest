"""Unit tests for rack configuration loading."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from hwtest_rack.config import (
    ExpectedIdentity,
    InstrumentConfig,
    RackConfig,
    load_config,
)


class TestExpectedIdentity:
    def test_create(self) -> None:
        identity = ExpectedIdentity(manufacturer="Acme", model="Widget")
        assert identity.manufacturer == "Acme"
        assert identity.model == "Widget"

    def test_frozen(self) -> None:
        identity = ExpectedIdentity(manufacturer="Acme", model="Widget")
        with pytest.raises(AttributeError):
            identity.manufacturer = "Other"  # type: ignore[misc]


class TestInstrumentConfig:
    def test_create_minimal(self) -> None:
        config = InstrumentConfig(
            name="psu01",
            driver="hwtest_bkprecision.psu:create_instrument",
            identity=ExpectedIdentity("B&K Precision", "9115"),
        )
        assert config.name == "psu01"
        assert config.driver == "hwtest_bkprecision.psu:create_instrument"
        assert config.identity.manufacturer == "B&K Precision"
        assert config.kwargs == {}

    def test_create_with_kwargs(self) -> None:
        config = InstrumentConfig(
            name="psu01",
            driver="hwtest_bkprecision.psu:create_instrument",
            identity=ExpectedIdentity("B&K Precision", "9115"),
            kwargs={"visa_address": "TCPIP::192.168.1.100::5025::SOCKET"},
        )
        assert config.kwargs["visa_address"] == "TCPIP::192.168.1.100::5025::SOCKET"


class TestRackConfig:
    def test_create(self) -> None:
        config = RackConfig(
            rack_id="test-rack-01",
            description="Test rack",
            instruments=(
                InstrumentConfig(
                    name="psu01",
                    driver="hwtest_bkprecision.psu:create_instrument",
                    identity=ExpectedIdentity("B&K Precision", "9115"),
                ),
            ),
        )
        assert config.rack_id == "test-rack-01"
        assert config.description == "Test rack"
        assert len(config.instruments) == 1


class TestLoadConfig:
    def test_load_valid_config(self) -> None:
        yaml_content = """
rack:
  id: "test-rack-01"
  description: "Integration test rack"

instruments:
  psu01:
    driver: "hwtest_bkprecision.psu:create_instrument"
    identity:
      manufacturer: "B&K Precision"
      model: "9115"
    kwargs:
      visa_address: "TCPIP::192.168.1.100::5025::SOCKET"

  daq01:
    driver: "hwtest_mcc.mcc118:create_instrument"
    identity:
      manufacturer: "Measurement Computing"
      model: "MCC 118"
    kwargs:
      address: 0
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            config = load_config(f.name)

        assert config.rack_id == "test-rack-01"
        assert config.description == "Integration test rack"
        assert len(config.instruments) == 2

        psu = next(i for i in config.instruments if i.name == "psu01")
        assert psu.driver == "hwtest_bkprecision.psu:create_instrument"
        assert psu.identity.manufacturer == "B&K Precision"
        assert psu.identity.model == "9115"
        assert psu.kwargs["visa_address"] == "TCPIP::192.168.1.100::5025::SOCKET"

        daq = next(i for i in config.instruments if i.name == "daq01")
        assert daq.identity.manufacturer == "Measurement Computing"
        assert daq.kwargs["address"] == 0

    def test_load_minimal_config(self) -> None:
        yaml_content = """
rack:
  id: "minimal-rack"

instruments: {}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            config = load_config(f.name)

        assert config.rack_id == "minimal-rack"
        assert config.description == ""
        assert len(config.instruments) == 0

    def test_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")

    def test_missing_rack_id(self) -> None:
        yaml_content = """
rack:
  description: "Missing ID"

instruments: {}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            with pytest.raises(ValueError, match="rack.id"):
                load_config(f.name)

    def test_missing_driver(self) -> None:
        yaml_content = """
rack:
  id: "test"

instruments:
  inst01:
    identity:
      manufacturer: "Acme"
      model: "Widget"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            with pytest.raises(ValueError, match="driver"):
                load_config(f.name)

    def test_missing_identity_manufacturer(self) -> None:
        yaml_content = """
rack:
  id: "test"

instruments:
  inst01:
    driver: "some.module:func"
    identity:
      model: "Widget"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            with pytest.raises(ValueError, match="manufacturer"):
                load_config(f.name)

    def test_missing_identity_model(self) -> None:
        yaml_content = """
rack:
  id: "test"

instruments:
  inst01:
    driver: "some.module:func"
    identity:
      manufacturer: "Acme"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            with pytest.raises(ValueError, match="model"):
                load_config(f.name)

    def test_invalid_yaml(self) -> None:
        yaml_content = "not: valid: yaml: syntax"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            with pytest.raises(Exception):  # YAML parse error
                load_config(f.name)

    def test_instruments_not_mapping(self) -> None:
        yaml_content = """
rack:
  id: "test"

instruments:
  - item1
  - item2
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            with pytest.raises(ValueError, match="instruments must be a mapping"):
                load_config(f.name)
