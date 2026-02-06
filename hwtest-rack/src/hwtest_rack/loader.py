"""Dynamic instrument driver loading via importlib.

This module provides functionality to dynamically load instrument driver
factory functions at runtime using Python's importlib. This allows the
test rack to load drivers specified in YAML configuration files without
requiring static imports.

Example:
    factory = load_driver("hwtest_bkprecision.psu:create_instrument")
    instrument = factory(visa_address="TCPIP::192.168.1.100::5025::SOCKET")
"""

from __future__ import annotations

import importlib
from typing import Any, Callable


def load_driver(driver_path: str) -> Callable[..., Any]:
    """Load an instrument driver factory function from a module path.

    Args:
        driver_path: Path in "module:function" format
            (e.g., "hwtest_bkprecision.psu:create_instrument").

    Returns:
        The loaded factory function.

    Raises:
        ValueError: If the driver path format is invalid.
        ImportError: If the module cannot be imported.
        AttributeError: If the function doesn't exist in the module.
    """
    if ":" not in driver_path:
        raise ValueError(
            f"Invalid driver path '{driver_path}': must be in 'module:function' format"
        )

    module_path, func_name = driver_path.rsplit(":", 1)

    if not module_path or not func_name:
        raise ValueError(f"Invalid driver path '{driver_path}': module and function names required")

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ImportError(f"Failed to import module '{module_path}': {exc}") from exc

    try:
        factory = getattr(module, func_name)
    except AttributeError as exc:
        raise AttributeError(f"Module '{module_path}' has no attribute '{func_name}'") from exc

    if not callable(factory):
        raise TypeError(f"'{driver_path}' is not callable")

    return factory
