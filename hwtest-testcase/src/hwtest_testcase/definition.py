"""Test definition loading (implemented in Rust; see ``hwtest_core.spec``).

YAML test definitions are parsed and validated by the Rust core. Every problem
in a file is reported at once with file, line, column and key path, raised as
``hwtest_core.errors.SpecError`` (a ``ValueError``).

Monitors and loggers may be written with the built-in ``kind:`` form or the
legacy ``module:``/``class:`` form::

    monitors:
      echo_voltage_monitor:
        kind: bounds          # same as module: hwtest_testcase / class: Monitor
        kwargs.0: {channel: echo_voltage}
        configuration:
          minimum: {echo_voltage: {within_range: [1.0, 0.3]}}
    loggers:
      csv_logger: {kind: csv, topics: [voltage_echo]}

``MonitorDef.get_bounds()`` returns the core bound check classes
(``WithinRange``, ``GoodValues``, ...) from ``hwtest_core.types.bounds``.
"""

from hwtest_core.spec import (
    LoggerDef,
    MonitorDef,
    MonitorState,
    TestCaseInfo,
    TestDefinition,
    find_definition_file,
    load_definition,
)

__all__ = [
    "LoggerDef",
    "MonitorDef",
    "MonitorState",
    "TestCaseInfo",
    "TestDefinition",
    "find_definition_file",
    "load_definition",
]
