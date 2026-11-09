"""Monitor result types (implemented in Rust).

Classes:
    MonitorVerdict: Outcome of a monitor evaluation.
    ThresholdViolation: One value outside its threshold.
    MonitorResult: Result of evaluating a monitor.
"""

from hwtest_core._enums import MonitorVerdict
from hwtest_core._native import MonitorResult, ThresholdViolation

__all__ = ["MonitorResult", "MonitorVerdict", "ThresholdViolation"]
