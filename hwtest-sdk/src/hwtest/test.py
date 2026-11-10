"""``hwtest.Test``: a test case = YAML definition + Python steps."""

from __future__ import annotations

import argparse
import importlib
import os
import signal
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any, NoReturn

from hwtest_core._native import Rack, Session
from hwtest_core.errors import SpecError
from hwtest_core.spec import MonitorState, TestDefinition

from hwtest.context import Context, State

StepFn = Callable[[Context, State], None]
HookFn = Callable[[Context], None]

_TRUE = {"1", "true", "yes", "on"}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


@dataclass
class RunSummary:
    """Result of a run.

    Attributes:
        outcome: ``passed``, ``failed``, ``stopped`` or ``error``.
        message: Explanation (first failure, error text, ...).
        stats: Counters: cycles_completed, measurements, passes, failures, skips.
    """

    outcome: str
    message: str
    run_id: str
    test_case: str
    mode: str
    stats: dict[str, int] = field(default_factory=dict)
    first_failure: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        """True if the run completed without failures."""
        return self.outcome == "passed"

    @property
    def exit_code(self) -> int:
        """0 = passed or stopped, 1 = failed, 2 = error."""
        return {"passed": 0, "stopped": 0, "failed": 1}.get(self.outcome, 2)

    def __str__(self) -> str:
        s = self.stats
        return (
            f"{self.test_case} [{self.mode}] {self.outcome.upper()}: {self.message} "
            f"({s.get('cycles_completed', 0)} cycle(s), {s.get('passes', 0)} pass, "
            f"{s.get('failures', 0)} fail)"
        )


def _caller_dir() -> Path:
    frame = sys._getframe(2)  # pylint: disable=protected-access
    filename = frame.f_globals.get("__file__")
    return Path(filename).resolve().parent if filename else Path.cwd()


def _resolve(path: str | Path, base: Path) -> Path:
    p = Path(path)
    if p.is_absolute() or p.exists():
        return p
    for candidate in (base / p, base / "configs" / p, base.parent / "configs" / p):
        if candidate.exists():
            return candidate
    return p


class Test:  # pylint: disable=too-many-instance-attributes
    """A test case.

    Args:
        definition: Test definition YAML (relative paths are also looked up
            next to the script and in ``configs/``).
        rack: Rack YAML file or rack class name; defaults to the definition's
            ``rack:`` entry.
    """

    __test__ = False  # not a pytest test class

    def __init__(self, definition: str | Path, *, rack: str | Path | None = None) -> None:
        base = _caller_dir()
        self._base = base
        self.definition_path = _resolve(definition, base)
        self.definition = TestDefinition.from_yaml(self.definition_path)
        self._rack_spec = rack
        self._step: StepFn | None = None
        self._setup: HookFn | None = None
        self._teardown: HookFn | None = None
        self._extras: dict[str, Any] = {}

    # --- decorators -----------------------------------------------------

    def step(self, fn: StepFn) -> StepFn:
        """Decorator: the hardware steps, called as ``fn(ctx, state)`` for each state."""
        self._step = fn
        return fn

    def setup(self, fn: HookFn) -> HookFn:
        """Decorator: called once as ``fn(ctx)`` before the first state."""
        self._setup = fn
        return fn

    def teardown(self, fn: HookFn) -> HookFn:
        """Decorator: called once as ``fn(ctx)`` at the end, even after failures."""
        self._teardown = fn
        return fn

    def add(self, name: str, value: Any) -> None:
        """Make an object available to steps as ``ctx.<name>`` (e.g. a UUT client)."""
        self._extras[name] = value

    # --- running ----------------------------------------------------------

    def _open_rack(self, rack: str | Path | None, serial: str | None, emulate: bool) -> Rack | None:
        spec = rack or self._rack_spec or self.definition.rack_id
        if spec is None:
            return None
        d = self.definition_path.resolve().parent
        search = [d, d / "configs", d.parent / "configs", self._base, self._base / "configs"]
        spec_path = Path(str(spec))
        if spec_path.suffix in (".yaml", ".yml"):
            r = Rack.open(_resolve(spec_path, self._base), serial=serial, emulate=emulate)
        else:
            r = Rack.find(str(spec), serial=serial, emulate=emulate, search_paths=search)
        for name, module, function, kwargs in r.python_drivers():
            factory = getattr(importlib.import_module(module), function)
            state = r.attach_python(name, factory(**kwargs))
            if state != "ready":
                print(f"warning: Python driver for {name!r} is not ready", file=sys.stderr)
        return r

    def run(  # pylint: disable=too-many-locals,too-many-arguments
        self,
        *,
        mode: str = "functional",
        cycles: int | None = None,
        serial: str | None = None,
        rack: str | Path | None = None,
        emulate: bool = False,
        nats: str | None = None,
        db: str | Path | None = None,
        units: dict[int, str] | None = None,
        offsite: bool = False,
        loggers: bool = True,
        run_id: str | None = None,
        events: str | None = None,
    ) -> RunSummary:
        """Run the test and return the summary (see ``main`` for the CLI)."""
        if self._step is None:
            raise RuntimeError("no step defined: decorate a function with @test.step")
        rack_obj = self._open_rack(rack, serial, emulate)
        try:
            session = Session(
                self.definition,
                rack_obj,
                mode=mode,
                max_cycles=cycles,
                run_id=run_id,
                nats_url=nats,
                db_path=str(db) if db else None,
                units=units or {},
                offsite=offsite,
                loggers=loggers,
                events=events,
            )
            ctx = Context(session, rack_obj, self._extras)
            step = self._step

            def run_step(c: Context, s: MonitorState) -> None:
                step(c, State(s))

            result = session.run(ctx, run_step, self._setup, self._teardown)
        finally:
            if rack_obj is not None:
                rack_obj.close()
        return RunSummary(**result)

    def main(self, argv: Sequence[str] | None = None) -> NoReturn:
        """Command-line entry point. Options can also come from environment
        variables (shown in ``--help``), which is how the runner passes them."""
        env = os.environ
        p = argparse.ArgumentParser(description=self.definition.test_case.name or None)
        p.add_argument(
            "--mode",
            default=env.get("HWTEST_MODE", env.get("TEST_MODE", "functional")),
            choices=["functional", "hass", "halt"],
            help="[HWTEST_MODE]",
        )
        p.add_argument("--cycles", type=int, default=None, help="stop after N cycles (hass/halt)")
        p.add_argument(
            "--serial",
            default=env.get("HWTEST_RACK_SERIAL"),
            help="rack instance serial (calibration) [HWTEST_RACK_SERIAL]",
        )
        p.add_argument(
            "--rack", default=env.get("HWTEST_RACK"), help="rack YAML or class [HWTEST_RACK]"
        )
        p.add_argument(
            "--emulate",
            action="store_true",
            default=_env_flag("HWTEST_EMULATE"),
            help="use instrument emulators [HWTEST_EMULATE]",
        )
        p.add_argument(
            "--nats", default=env.get("HWTEST_NATS_URL"), help="NATS URL [HWTEST_NATS_URL]"
        )
        p.add_argument("--db", default=env.get("HWTEST_DB"), help="results database [HWTEST_DB]")
        p.add_argument(
            "--unit",
            action="append",
            default=[],
            metavar="SLOT=SERIAL",
            help="UUT serial for a slot, e.g. 0=SN123 (repeatable)",
        )
        p.add_argument(
            "--offsite",
            action="store_true",
            default=_env_flag("HWTEST_OFFSITE") or _env_flag("OFFSITE_MODE"),
            help="skip loggers marked ignore_offsite [HWTEST_OFFSITE]",
        )
        p.add_argument("--no-loggers", action="store_true", help="do not start the YAML loggers")
        p.add_argument("--run-id", default=env.get("HWTEST_RUN_ID"), help="[HWTEST_RUN_ID]")
        args = p.parse_args(argv)

        units: dict[int, str] = {}
        for u in args.unit:
            slot, _, sn = u.partition("=")
            if not slot.isdigit() or not sn:
                p.error(f"--unit expects SLOT=SERIAL, got {u!r}")
            units[int(slot)] = sn

        def _term(_sig: int, _frame: FrameType | None) -> None:
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, _term)
        try:
            summary = self.run(
                mode=args.mode,
                cycles=args.cycles,
                serial=args.serial,
                rack=args.rack,
                emulate=args.emulate,
                nats=args.nats,
                db=args.db,
                units=units,
                offsite=args.offsite,
                loggers=not args.no_loggers,
                run_id=args.run_id,
                events="stdout" if _env_flag("HWTEST_EVENTS") else None,
            )
        except SpecError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(2)
        except KeyboardInterrupt:
            print("stopped", file=sys.stderr)
            sys.exit(0)
        print(summary, flush=True)
        sys.exit(summary.exit_code)
