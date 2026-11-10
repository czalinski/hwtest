"""End-to-end tests of the hwtest SDK on an emulated bench."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import hwtest
from hwtest import HardwareError, Rack, Test

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HWTEST_TEST_LOG_DIR", str(tmp_path / "logs"))
    return tmp_path / "logs"


def make_test(gain: float = 1.0) -> Test:
    test = Test(FIXTURES / "loop_test.yaml")

    @test.setup
    def setup(ctx: hwtest.Context) -> None:
        ctx.rack.emu_connect_analog(0, 0, 4, 0, gain=gain)

    @test.step
    def step(ctx: hwtest.Context, state: hwtest.State) -> None:
        ctx.rack.write("stimulus", state.target_voltage)
        ctx.measure(response=ctx.rack.read("response"), target=state.target_voltage)

    return test


class TestRun:
    def test_functional_pass_with_csv_log(self, log_dir: Path) -> None:
        summary = make_test().run(emulate=True, run_id="r1")
        assert summary.passed, summary
        assert summary.stats["cycles_completed"] == 1
        assert summary.stats["passes"] == 2
        csv = (log_dir / "functional" / "loop_test" / "r1" / "loop_test.csv").read_text()
        rows = csv.strip().splitlines()
        assert rows[0] == "timestamp_ns,response,target"
        assert [r.split(",")[1:] for r in rows[1:]] == [["1", "1"], ["4", "4"]]

    def test_failure_stops_hass(self) -> None:
        summary = make_test(gain=0.9).run(mode="hass", emulate=True)
        assert summary.outcome == "failed"
        assert summary.exit_code == 1
        assert summary.message.startswith("Failed: response=0.9000 outside [0.9500, 1.0500]")
        assert summary.stats["cycles_completed"] == 0

    def test_hass_cycles(self) -> None:
        summary = make_test().run(mode="halt", cycles=3, emulate=True, loggers=False)
        assert (summary.outcome, summary.stats["cycles_completed"]) == ("passed", 3)

    def test_step_error_reported(self) -> None:
        test = Test(FIXTURES / "loop_test.yaml")

        @test.step
        def step(ctx: hwtest.Context, state: hwtest.State) -> None:
            ctx.rack.read("respnse")

        summary = test.run(emulate=True, loggers=False)
        assert summary.outcome == "error"
        assert summary.exit_code == 2
        assert 'unknown channel "respnse"' in summary.message

    def test_ctx_api(self) -> None:
        test = Test(FIXTURES / "loop_test.yaml")
        seen: dict[str, object] = {}

        @test.step
        def step(ctx: hwtest.Context, state: hwtest.State) -> None:
            seen["param"] = ctx.param("settle_s")
            seen["state"] = (state.id, state.name, state.target_voltage)
            seen["mode"] = ctx.mode
            ctx.rack.write("dut_power", True)
            seen["power"] = ctx.rack.read("dut_power")
            seen["psu"] = ctx.rack.psu_status("vin")["setpoint"]
            with ctx.transition("ramp"):
                r = ctx.measure(response=99.0)
                seen["verdict"] = r[0].verdict.value
            with pytest.raises(AttributeError, match="has no parameter 'voltage'"):
                state.voltage  # pylint: disable=pointless-statement
            ctx.stop()

        summary = test.run(emulate=True, loggers=False)
        assert summary.outcome == "stopped"
        assert seen == {
            "param": 0.0,
            "state": ("low", "Low", 1.0),
            "mode": "functional",
            "power": True,
            "psu": 0.0,
            "verdict": "skip",
        }

    def test_no_step_defined(self) -> None:
        with pytest.raises(RuntimeError, match="@test.step"):
            Test(FIXTURES / "loop_test.yaml").run(emulate=True)


class TestRack:
    def test_standalone_rack(self) -> None:
        rack = Rack.open(FIXTURES / "bench_rack.yaml", emulate=True)
        assert rack.ready and rack.emulated
        assert {c["name"] for c in rack.channels()} >= {"stimulus", "response", "vin", "door"}
        rack.emu_set_digital_input(0, 1, True)
        assert rack.read("door") is True
        rack.emu_set_analog_input(4, 0, 2.5)
        assert rack.read("response") == 2.5
        with pytest.raises(HardwareError, match="INPUT"):
            rack.write("door", True)
        with pytest.raises(HardwareError, match="exceeds limit 13"):
            rack.write("vin", 20.0)
        with pytest.raises(HardwareError, match="digital: write True or False"):
            rack.write("dut_power", "on")
        assert rack.status()["state"] == "ready"
        rack.close()

    def test_python_escape_hatch_driver(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "labdrivers.py").write_text(
            "class Meter:\n"
            "    model = 'Lab Meter'\n"
            "    def __init__(self, offset): self.offset = offset; self.out = 0.0\n"
            "    def get_identity(self): return {'manufacturer': 'Lab', 'model': 'M1', 'serial': '7'}\n"
            "    def channels(self): return [{'name': 'meter_in', 'id': 0, 'kind': 'analog_in'},\n"
            "                                {'name': 'meter_out', 'id': 1, 'kind': 'analog_out'}]\n"
            "    def read(self, name): return self.out + self.offset\n"
            "    def write(self, name, v): self.out = v\n"
            "def make(offset, channels): return Meter(offset)\n"
        )
        (tmp_path / "rack.yaml").write_text(
            "rack: {id: py}\ninstruments:\n  meter:\n    driver: 'labdrivers:make'\n"
            "    identity: {manufacturer: Lab, model: M1}\n"
            "    kwargs: {offset: 0.5, channels: [{id: 0, name: meter_in}, {id: 1, name: meter_out}]}\n"
        )
        (tmp_path / "t.yaml").write_text(
            "test_case: {id: py_driver}\nmonitor_states: {s: {}}\n"
            "monitors: {m: {configuration: {s: {meter_in: {within_range: [2.5, 0.01]}}}}}\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        test = Test(tmp_path / "t.yaml", rack=tmp_path / "rack.yaml")

        @test.step
        def step(ctx: hwtest.Context, state: hwtest.State) -> None:
            ctx.rack.write("meter_out", 2.0)
            ctx.measure(meter_in=ctx.rack.read("meter_in"))

        assert test.run(emulate=True).passed


class TestCommandLine:
    def run_script(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        e = {**os.environ, **(env or {})}
        return subprocess.run(
            [sys.executable, str(FIXTURES / "loop_script.py"), "--emulate", *args],
            capture_output=True,
            text=True,
            env=e,
            timeout=60,
            check=False,
        )

    def test_exit_code_and_summary(self) -> None:
        r = self.run_script("--no-loggers")
        assert r.returncode == 0, r.stderr
        assert "loop_test [functional] PASSED" in r.stdout

    def test_events_for_runner(self) -> None:
        r = self.run_script(
            "--mode", "hass", "--cycles", "2", "--no-loggers", env={"HWTEST_EVENTS": "1"}
        )
        assert r.returncode == 0, r.stderr
        events = [
            json.loads(l[len("@@hwtest ") :])
            for l in r.stdout.splitlines()
            if l.startswith("@@hwtest ")
        ]
        kinds = [e["event"] for e in events]
        assert kinds[0] == "run_started" and kinds[-1] == "run_finished"
        assert kinds.count("cycle_completed") == 2
        assert events[-1]["outcome"] == "passed"
        assert any(e["event"] == "measurement" and e["values"]["target"] == 4.0 for e in events)

    def test_bad_option(self) -> None:
        r = self.run_script("--unit", "x")
        assert r.returncode == 2 and "SLOT=SERIAL" in r.stderr


def test_check_command(tmp_path: Path) -> None:
    from hwtest.cli import main

    bad = tmp_path / "bad.yaml"
    bad.write_text("test_case: {id: t}\nstate_sequence: [nope]\n")
    assert main(["check", str(FIXTURES / "loop_test.yaml"), str(FIXTURES / "bench_rack.yaml")]) == 0
    assert main(["check", str(bad)]) == 1
