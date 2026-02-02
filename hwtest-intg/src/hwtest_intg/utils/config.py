"""Configuration loading utilities for integration tests."""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import Any

import yaml


def load_rack_config(name: str) -> dict[str, Any]:
    """Load a rack configuration from package resources.

    Args:
        name: Configuration file name (with or without .yaml extension).

    Returns:
        Parsed YAML configuration as a dictionary.

    Raises:
        FileNotFoundError: If the configuration file doesn't exist.
        ValueError: If the configuration is invalid.

    Example:
        >>> config = load_rack_config("pi5_mcc_intg_a_rack")
        >>> print(config["rack"]["id"])
        'pi5-mcc-intg-a'
    """
    if not name.endswith(".yaml"):
        name = f"{name}.yaml"

    try:
        # Use importlib.resources to load from package
        files = importlib.resources.files("hwtest_intg.configs")
        config_path = files.joinpath(name)

        with importlib.resources.as_file(config_path) as path:
            if not path.exists():
                raise FileNotFoundError(f"Configuration file not found: {name}")
            with open(path, encoding="utf-8") as f:
                config: dict[str, Any] = yaml.safe_load(f)

        if config is None:
            raise ValueError(f"Configuration file is empty: {name}")

        return config

    except TypeError:
        # Fallback for older Python versions or when running from source
        package_dir = Path(__file__).parent.parent
        config_file = package_dir / "configs" / name

        if not config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {name}")

        with open(config_file, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        if config is None:
            raise ValueError(f"Configuration file is empty: {name}")

        return config


def get_config_path(name: str) -> Path:
    """Get the filesystem path to a configuration file.

    This is useful when you need to pass the config path to another tool
    like hwtest-rack that expects a file path.

    Args:
        name: Configuration file name (with or without .yaml extension).

    Returns:
        Path to the configuration file.

    Raises:
        FileNotFoundError: If the configuration file doesn't exist.
    """
    if not name.endswith(".yaml"):
        name = f"{name}.yaml"

    try:
        files = importlib.resources.files("hwtest_intg.configs")
        config_path = files.joinpath(name)

        with importlib.resources.as_file(config_path) as path:
            if not path.exists():
                raise FileNotFoundError(f"Configuration file not found: {name}")
            return Path(path)

    except TypeError:
        package_dir = Path(__file__).parent.parent
        config_file = package_dir / "configs" / name

        if not config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {name}")

        return config_file
