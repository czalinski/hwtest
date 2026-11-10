"""Runs the bench scripts against the emulated rack and an in-process UUT
simulator (no hardware): ``python -m pytest tests/``."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

import hwtest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load(name: str) -> ModuleType:
    """Imports a bench script without running it."""
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(f"bench_{name}", SCRIPTS / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


@pytest.fixture(name="uut")
def fixture_uut(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[hwtest.UutSimulator]:
    bus = f"virtual:{request.node.name}"
    with hwtest.UutSimulator(can_interface=bus) as sim:
        monkeypatch.setenv("UUT_URL", sim.url)
        monkeypatch.setenv("CAN_INTERFACE", bus)
        yield sim


def run(name: str, **kwargs: object) -> hwtest.RunSummary:
    summary: hwtest.RunSummary = load(name).test.run(emulate=True, loggers=False, **kwargs)
    return summary


@pytest.mark.parametrize("script", ["voltage_echo_monitor", "analog_paths", "can_communication"])
def test_script_passes(uut: hwtest.UutSimulator, script: str) -> None:
    summary = run(script)
    assert summary.passed, str(summary)


def test_echo_hass_cycles(uut: hwtest.UutSimulator) -> None:
    summary = run("voltage_echo_monitor", mode="hass", cycles=2)
    assert summary.passed and summary.stats["cycles_completed"] == 2, str(summary)


def test_echo_detects_injected_fault(uut: hwtest.UutSimulator) -> None:
    hwtest.UutClient(uut.url).failure_configure(delay_seconds=0.01, duration_seconds=0.0)
    summary = run("voltage_echo_monitor")
    assert summary.outcome == "failed" and "echo_voltage" in summary.message, str(summary)
