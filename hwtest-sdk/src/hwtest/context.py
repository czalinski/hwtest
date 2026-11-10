"""What a test step receives: the context (``ctx``) and the current state."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import os

from hwtest_core._native import CanBus, Rack, Session, UutClient
from hwtest_core.spec import MonitorState
from hwtest_core.types.monitor import MonitorResult


class State:
    """The environmental state being tested.

    Parameters from the YAML ``monitor_states`` entry are available as
    attributes, e.g. ``state.target_voltage``.
    """

    def __init__(self, state: MonitorState) -> None:
        self._state = state

    @property
    def id(self) -> str:
        """State id (the YAML key)."""
        return self._state.id

    @property
    def name(self) -> str:
        """Human-readable name."""
        return self._state.name

    @property
    def description(self) -> str:
        """Description."""
        return self._state.description

    @property
    def parameters(self) -> dict[str, Any]:
        """All per-state parameters."""
        return self._state.parameters

    def __getattr__(self, name: str) -> Any:
        params = self._state.parameters
        if name in params:
            return params[name]
        raise AttributeError(
            f"state {self._state.id!r} has no parameter {name!r} "
            f"(defined: {', '.join(params) or 'none'})"
        )

    def __repr__(self) -> str:
        return f"State({self.id!r}, {self.parameters})"


class Context:
    """Everything a test step needs.

    Attributes:
        rack: The test rack (instruments by logical channel name).
    """

    def __init__(self, session: Session, rack: Rack | None, extras: dict[str, Any]) -> None:
        self._session = session
        self._rack = rack
        self._extras = extras

    @property
    def rack(self) -> Rack:
        """The rack named by the test definition's ``rack:`` entry."""
        if self._rack is None:
            raise RuntimeError(
                "this test has no rack: add 'rack: <rack class>' to the test definition "
                "or pass --rack"
            )
        return self._rack

    @property
    def uut(self) -> UutClient:
        """The UUT simulator client (from ``UUT_URL``, set by the runner)."""
        if "uut" not in self._extras:
            self._extras["uut"] = UutClient()
        uut: UutClient = self._extras["uut"]
        return uut

    @property
    def can(self) -> CanBus:
        """The rack's CAN bus (``CAN_INTERFACE``, default ``can0``)."""
        if "can" not in self._extras:
            self._extras["can"] = CanBus(os.environ.get("CAN_INTERFACE", "can0"))
        bus: CanBus = self._extras["can"]
        return bus

    def param(self, name: str) -> Any:
        """A parameter: the current state's value if it overrides it, else ``case_parameters``."""
        return self._session.param(name)

    def calibration(self, name: str, default: float = 1.0) -> float:
        """A calibration factor from the rack instance file (``--serial``)."""
        return self.rack.calibration(name, default)

    def sleep(self, seconds: float) -> None:
        """Wait, while still responding to Ctrl-C / stop requests."""
        self._session.sleep(float(seconds))

    def measure(self, topic: str | None = None, **values: float) -> list[MonitorResult]:
        """Record measurements, e.g. ``ctx.measure(echo_voltage=v)``.

        Values are checked against the YAML monitor bounds for the current
        state, logged, and published. Returns the monitor results. A failure
        ends the run after the current step.
        """
        return self._session.measure(values, topic)

    @contextmanager
    def transition(self, description: str = "ramping") -> Iterator[None]:
        """Mark a ramp into the current state; monitors skip inside the block::

        with ctx.transition("chamber to 85 C"):
            chamber.set(85); wait_until_stable()
        """
        self._session.begin_transition(description)
        try:
            yield
        finally:
            self._session.end_transition()

    def log(self, message: str, level: str = "info") -> None:
        """Show a message in the runner (and the console)."""
        print(message, flush=True)
        self._session.log(message, level)

    def stop(self) -> None:
        """End the run after the current step (as if the operator pressed Stop)."""
        self._session.request_stop()

    @property
    def mode(self) -> str:
        """``functional``, ``hass`` or ``halt``."""
        return self._session.mode

    @property
    def cycle(self) -> int:
        """Number of completed passes through the state sequence."""
        return self._session.cycle

    @property
    def run_id(self) -> str:
        """Run id (also the log directory name)."""
        return self._session.run_id

    @property
    def units(self) -> dict[int, str]:
        """UUT serial numbers by slot (``--unit 0=SN123``)."""
        return self._session.units

    def __getattr__(self, name: str) -> Any:
        extras = self.__dict__.get("_extras", {})
        if name in extras:
            return extras[name]
        raise AttributeError(f"Context has no attribute {name!r}")
