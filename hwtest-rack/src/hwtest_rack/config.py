"""YAML configuration loading for test racks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ExpectedIdentity:
    """Expected instrument identity for verification.

    Args:
        manufacturer: Expected manufacturer name.
        model: Expected model name/number.
    """

    manufacturer: str
    model: str


@dataclass(frozen=True)
class InstrumentConfig:
    """Configuration for a single instrument in the rack.

    Args:
        name: Unique instrument name within the rack.
        driver: Driver path in "module:function" format.
        identity: Expected identity for verification.
        kwargs: Additional keyword arguments passed to the driver factory.
    """

    name: str
    driver: str
    identity: ExpectedIdentity
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RackConfig:
    """Configuration for a test rack.

    Args:
        rack_id: Unique identifier for this rack.
        description: Human-readable description.
        instruments: Instrument configurations.
    """

    rack_id: str
    description: str
    instruments: tuple[InstrumentConfig, ...]


def load_config(path: str | Path) -> RackConfig:
    """Load rack configuration from a YAML file.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        Parsed rack configuration.

    Raises:
        FileNotFoundError: If the config file doesn't exist.
        ValueError: If the config is invalid or missing required fields.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError("Config must be a YAML mapping")

    # Parse rack section
    rack_section = data.get("rack", {})
    rack_id = rack_section.get("id")
    if not rack_id:
        raise ValueError("Missing required field: rack.id")
    description = rack_section.get("description", "")

    # Parse instruments section
    instruments_data = data.get("instruments", {})
    if not isinstance(instruments_data, dict):
        raise ValueError("instruments must be a mapping")

    instruments: list[InstrumentConfig] = []
    for name, inst_data in instruments_data.items():
        if not isinstance(inst_data, dict):
            raise ValueError(f"Instrument '{name}' must be a mapping")

        driver = inst_data.get("driver")
        if not driver:
            raise ValueError(f"Instrument '{name}' missing required field: driver")

        identity_data = inst_data.get("identity", {})
        if not identity_data.get("manufacturer"):
            raise ValueError(f"Instrument '{name}' missing required field: identity.manufacturer")
        if not identity_data.get("model"):
            raise ValueError(f"Instrument '{name}' missing required field: identity.model")

        identity = ExpectedIdentity(
            manufacturer=identity_data["manufacturer"],
            model=identity_data["model"],
        )

        kwargs = inst_data.get("kwargs", {})
        if not isinstance(kwargs, dict):
            raise ValueError(f"Instrument '{name}' kwargs must be a mapping")

        instruments.append(
            InstrumentConfig(
                name=name,
                driver=driver,
                identity=identity,
                kwargs=kwargs,
            )
        )

    return RackConfig(
        rack_id=rack_id,
        description=description,
        instruments=tuple(instruments),
    )
