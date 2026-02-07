"""Rack instance configuration for per-unit calibration.

This module manages rack instance configurations that are specific to a
physical rack unit, separate from the rack class configuration which
describes the design/model.

Rack Class vs Rack Instance:
    - Rack Class (e.g., pi5_mcc_intg_a_rack.yaml): Defines what instruments
      and channels exist on this type of rack. Stored in code tree.
    - Rack Instance (e.g., pi5_mcc_intg_a_001.yaml): Defines calibration
      factors specific to a physical unit. Stored outside code tree.

Default search paths for instance configs:
    1. /etc/hwtest/racks/          (system-wide)
    2. ~/.config/hwtest/racks/     (user)
    3. HWTEST_RACK_INSTANCE_PATH environment variable

Example instance YAML:

    rack_instance:
      serial_number: "001"
      rack_class: "pi5_mcc_intg_a"
      description: "Integration lab bench A"

    calibration:
      uut_adc_scale_factor: 2.0
      mcc118_scale_factor: 1.487

    calibration_metadata:
      calibrated_at: "2024-02-07T10:30:00Z"
      calibrated_by: "auto"
      reference_instrument: "mcc152_dac"
      notes: "Calibrated using 1.0V, 2.5V, 4.0V reference points"
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def _get_search_paths() -> list[Path]:
    """Get search paths for rack instance configurations.

    Returns:
        List of directories to search, in priority order.
    """
    paths: list[Path] = []

    # Environment variable override (highest priority)
    env_path = os.environ.get("HWTEST_RACK_INSTANCE_PATH")
    if env_path:
        for p in env_path.split(":"):
            if p:
                paths.append(Path(p))

    # User config directory
    home = Path.home()
    paths.append(home / ".config" / "hwtest" / "racks")

    # System config directory
    paths.append(Path("/etc/hwtest/racks"))

    return paths


@dataclass(frozen=True)
class RackInstanceInfo:
    """Rack instance identification.

    Attributes:
        serial_number: Unique serial number for this physical rack.
        rack_class: Reference to the rack class (e.g., "pi5_mcc_intg_a").
        description: Human-readable description of this rack instance.
    """

    serial_number: str
    rack_class: str
    description: str = ""


@dataclass(frozen=True)
class CalibrationMetadata:
    """Metadata about when and how calibration was performed.

    Attributes:
        calibrated_at: ISO timestamp of calibration.
        calibrated_by: Who or what performed the calibration.
        reference_instrument: Instrument used as calibration reference.
        notes: Additional notes about calibration procedure.
    """

    calibrated_at: str = ""
    calibrated_by: str = ""
    reference_instrument: str = ""
    notes: str = ""


@dataclass
class RackInstanceConfig:
    """Configuration for a specific rack instance.

    Contains the serial number, calibration factors, and metadata
    specific to one physical rack unit.

    Attributes:
        instance: Rack instance identification info.
        calibration: Map of calibration factor names to values.
        metadata: Information about the calibration procedure.
        source_path: Path to the YAML file (if loaded from file).
    """

    instance: RackInstanceInfo
    calibration: dict[str, float] = field(default_factory=dict)
    metadata: CalibrationMetadata = field(default_factory=CalibrationMetadata)
    source_path: Path | None = None

    def get_calibration(self, name: str, default: float = 1.0) -> float:
        """Get a calibration factor by name.

        Args:
            name: Factor name (e.g., "mcc118_scale_factor").
            default: Default value if factor not defined.

        Returns:
            The calibration factor value.
        """
        return self.calibration.get(name, default)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for YAML serialization.

        Returns:
            Dictionary representation of the config.
        """
        return {
            "rack_instance": {
                "serial_number": self.instance.serial_number,
                "rack_class": self.instance.rack_class,
                "description": self.instance.description,
            },
            "calibration": dict(self.calibration),
            "calibration_metadata": {
                "calibrated_at": self.metadata.calibrated_at,
                "calibrated_by": self.metadata.calibrated_by,
                "reference_instrument": self.metadata.reference_instrument,
                "notes": self.metadata.notes,
            },
        }

    def save(self, path: Path | None = None) -> Path:
        """Save the instance config to a YAML file.

        Args:
            path: Path to save to. If None, uses source_path or generates
                a path in the user config directory.

        Returns:
            Path where the file was saved.

        Raises:
            ValueError: If no path specified and no source_path set.
        """
        if path is None:
            path = self.source_path

        if path is None:
            # Generate path in user config directory
            config_dir = Path.home() / ".config" / "hwtest" / "racks"
            config_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{self.instance.rack_class}_{self.instance.serial_number}.yaml"
            path = config_dir / filename

        # Ensure parent directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)

        return path

    @classmethod
    def from_yaml(cls, path: str | Path) -> RackInstanceConfig:
        """Load a rack instance config from a YAML file.

        Args:
            path: Path to the YAML file.

        Returns:
            Parsed RackInstanceConfig.

        Raises:
            FileNotFoundError: If file doesn't exist.
            yaml.YAMLError: If YAML parsing fails.
        """
        path = Path(path)

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        return cls._parse(data, source_path=path)

    @classmethod
    def _parse(cls, data: dict[str, Any], source_path: Path | None) -> RackInstanceConfig:
        """Parse instance config from dictionary.

        Args:
            data: Raw dictionary data.
            source_path: Source file path (if any).

        Returns:
            Parsed RackInstanceConfig.
        """
        # Parse instance info
        inst_data = data.get("rack_instance", {})
        instance = RackInstanceInfo(
            serial_number=str(inst_data.get("serial_number", "unknown")),
            rack_class=inst_data.get("rack_class", "unknown"),
            description=inst_data.get("description", ""),
        )

        # Parse calibration factors
        cal_data = data.get("calibration", {})
        calibration = {k: float(v) for k, v in cal_data.items() if isinstance(v, (int, float))}

        # Parse metadata
        meta_data = data.get("calibration_metadata", {})
        metadata = CalibrationMetadata(
            calibrated_at=meta_data.get("calibrated_at", ""),
            calibrated_by=meta_data.get("calibrated_by", ""),
            reference_instrument=meta_data.get("reference_instrument", ""),
            notes=meta_data.get("notes", ""),
        )

        return cls(
            instance=instance,
            calibration=calibration,
            metadata=metadata,
            source_path=source_path,
        )

    @classmethod
    def create_new(
        cls,
        serial_number: str,
        rack_class: str,
        description: str = "",
    ) -> RackInstanceConfig:
        """Create a new rack instance config with default calibration.

        Args:
            serial_number: Unique serial number for this rack.
            rack_class: Reference to the rack class.
            description: Human-readable description.

        Returns:
            New RackInstanceConfig with default calibration values.
        """
        return cls(
            instance=RackInstanceInfo(
                serial_number=serial_number,
                rack_class=rack_class,
                description=description,
            ),
            calibration={
                "uut_adc_scale_factor": 1.0,
                "mcc118_scale_factor": 1.0,
            },
            metadata=CalibrationMetadata(
                calibrated_at=datetime.now(timezone.utc).isoformat(),
                calibrated_by="manual",
                notes="Default calibration - not yet calibrated",
            ),
        )


def find_instance_config(
    rack_class: str,
    serial_number: str | None = None,
    search_paths: list[str | Path] | None = None,
) -> Path | None:
    """Find a rack instance configuration file.

    Args:
        rack_class: Rack class identifier (e.g., "pi5_mcc_intg_a").
        serial_number: Optional serial number. If not specified, finds
            the first matching rack class.
        search_paths: Directories to search. If None, uses default paths.

    Returns:
        Path to the instance config file, or None if not found.
    """
    if search_paths is None:
        search_paths = _get_search_paths()

    # Build filename patterns to search for
    patterns: list[str] = []
    if serial_number:
        patterns.append(f"{rack_class}_{serial_number}.yaml")
        patterns.append(f"{rack_class}_{serial_number}.yml")
    else:
        # Match any serial number for this rack class
        patterns.append(f"{rack_class}_*.yaml")
        patterns.append(f"{rack_class}_*.yml")

    for search_dir in search_paths:
        search_dir = Path(search_dir)
        if not search_dir.is_dir():
            continue

        # Try exact matches first
        if serial_number:
            for pattern in patterns:
                candidate = search_dir / pattern
                if candidate.is_file():
                    return candidate
        else:
            # Glob for any matching file
            for pattern in patterns:
                matches = list(search_dir.glob(pattern))
                if matches:
                    # Return first match (could sort by serial number)
                    return sorted(matches)[0]

    return None


def load_instance_config(
    rack_class: str,
    serial_number: str | None = None,
    path: str | Path | None = None,
    search_paths: list[str | Path] | None = None,
) -> RackInstanceConfig:
    """Load a rack instance configuration.

    Args:
        rack_class: Rack class identifier.
        serial_number: Optional serial number.
        path: Explicit path to config file (overrides search).
        search_paths: Directories to search if path not given.

    Returns:
        Loaded RackInstanceConfig.

    Raises:
        FileNotFoundError: If instance config not found.
    """
    if path is not None:
        return RackInstanceConfig.from_yaml(path)

    found = find_instance_config(rack_class, serial_number, search_paths)
    if found is None:
        raise FileNotFoundError(
            f"Rack instance config not found for class '{rack_class}'"
            + (f" serial '{serial_number}'" if serial_number else "")
            + ". Set HWTEST_RACK_INSTANCE_PATH or create config in ~/.config/hwtest/racks/"
        )

    return RackInstanceConfig.from_yaml(found)
