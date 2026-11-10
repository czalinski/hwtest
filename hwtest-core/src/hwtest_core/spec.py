"""YAML test specifications (implemented in Rust).

Loading is strict: every problem in a file is reported at once, each with the
file, line, column and YAML key path, and raised as ``SpecError``::

    voltage_echo.yaml: 2 problems:
      voltage_echo.yaml:41:9: state_sequence[1]: unknown state "midle" (...; did you mean "middle"?)
      voltage_echo.yaml:63:26: monitors.echo.configuration.maximum.echo_voltage: within_range: ...

Classes:
    TestDefinition: A validated test definition (``from_yaml``, ``from_dict``).
    TestCaseInfo, MonitorState, MonitorDef, LoggerDef: Parts of a definition.
    Monitor: State-dependent bounds monitor built from a ``MonitorDef``.

Functions:
    load_definition: Load by explicit path or find by test id.
    find_definition_file: Locate ``<id>.yaml`` / ``<id>_definition.yaml``.
    check_definitions: Validate files, returning ``[(path, error or None)]``.

YAML 1.2 rules apply: write booleans as ``true``/``false`` (``yes``/``no`` are text).
"""

from hwtest_core._native import (
    LoggerDef,
    Monitor,
    MonitorDef,
    MonitorState,
    TestCaseInfo,
    TestDefinition,
    check_definitions,
    find_definition_file,
    load_definition,
)

__all__ = [
    "LoggerDef",
    "Monitor",
    "MonitorDef",
    "MonitorState",
    "TestCaseInfo",
    "TestDefinition",
    "check_definitions",
    "find_definition_file",
    "load_definition",
]
