"""State-dependent bounds monitor (implemented in Rust; see ``hwtest_core.spec``).

``Monitor(monitor_def)`` evaluates measurements against the bounds a test
definition gives for the current environmental state:

- transition states are skipped (``SKIP``);
- fields without a value, without a bound, or bound to ``special: any`` are
  not checked;
- any failing check gives ``FAIL`` with one ``ThresholdViolation`` per field;
  nothing checked gives ``SKIP``; otherwise ``PASS``.

Non-finite measurements (NaN, inf) fail every check except ``special: any``.
"""

from hwtest_core.spec import Monitor

__all__ = ["Monitor"]
