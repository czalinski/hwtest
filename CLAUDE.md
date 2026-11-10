# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

hwtest is a monorepo for hardware test automation for HASS (Highly Accelerated Stress Screening) and HALT (Highly Accelerated Life Testing) via instrument automation. It runs on Linux (Raspberry Pi 4/5, Orange Pi 5, Beelink, Docker) with Rust (stable, MSRV in root `Cargo.toml`) and Python 3.10+.

**Language split** (see `docs/coding-requirements.md`): all framework code is **Rust** in `crates/`, exposed to Python through **one PyO3 extension** (`hwtest_core._native`). The only Python is **test cases for specific hardware**, written by hardware test engineers/designers (not software developers) as short, **synchronous** scripts using the `hwtest` SDK; specs and bounds are **YAML**. The Python script is the process entry point; the Rust runner launches scripts as subprocesses. Drivers are Rust, with a YAML-selectable Python driver escape hatch for one-off instruments. Don't add framework logic in Python.

**Migration status:** complete (Phases 0–5: aarch64 builds, YAML specs + monitor engine, NATS/loggers/results DB, drivers + rack, run engine + SDK + runner, UUT simulator + CAN). Next: integration lab (NATS, InfluxDB, results DB services) and the custom test PCB. Work is committed and pushed straight to `main`.

## Build and Development Commands

```bash
# Rust toolchain: https://rustup.rs
# Python: one venv; hwtest-core builds the Rust extension via maturin.
# Re-run `pip install -e ./hwtest-core` after changing Rust code used by Python.
python3 -m venv .venv && source .venv/bin/activate
pip install -e "./hwtest-core[dev]" -e "./hwtest-sdk[dev]"

# Rust (from repo root)
cargo test                                  # all crates (NATS tests need HWTEST_NATS_URL)
cargo clippy --workspace --all-targets      # pedantic, must be clean
cargo fmt --all --check

# Python
cd hwtest-core && python -m pytest tests/unit -q && cd ..
cd hwtest-sdk && python -m pytest tests -q && cd ..
cd hwtest-intg && python -m pytest tests -q && cd ..   # bench scripts, fully emulated

# Lint (from a Python package dir)
python -m black --check --line-length 100 src/ tests/
python -m mypy --strict src/
python -m pylint src/

# Validate YAML (any kind: definitions, racks, stations, instances)
hwtest check configs/*.yaml
cargo run -p hwtest-spec --example check_spec -- path/to/*.yaml

# Release artifacts: abi3 wheels + aarch64/x86_64 binaries into dist/ (zig cross-build)
scripts/build_release.sh [--target aarch64|x86_64|all]
```

## Architecture

### System layers

1. **Test execution**: a Python script drives environmental conditions and UUT state through `hwtest`
2. **Measurement**: instruments (Rust drivers) produce telemetry; streaming over NATS JetStream (<25 ms software latency budget)
3. **Monitoring**: YAML bounds evaluated per environmental state in Rust; evaluation suspended during transitions
4. **Persistence**: CSV / InfluxDB loggers, SQLite results database

### Crates (`crates/`)

| Crate | Contents |
|-------|----------|
| `hwtest-core` | Types (Timestamp, ids, InstrumentIdentity), telemetry, state, thresholds, bounds (`within_range`, `good_values`, `special`, ...; fail-closed on NaN/inf), monitor engine (`StateBounds`, `BoundsMonitor`), binary streaming protocol. Golden wire fixtures in `tests/fixtures/python_golden.json` |
| `hwtest-spec` | All engineer-facing YAML (serde-saphyr, YAML 1.2): `testdef.rs`, `rack.rs`, `instance.rs` (per-serial calibration), `station.rs`, `env.rs` (`${VAR:default}`), `search.rs` (search paths) |
| `hwtest-nats` | async-nats: stream publisher/subscriber (schema rebroadcast 1 s), state pub/sub, telemetry monitor |
| `hwtest-logger` | CSV and InfluxDB (line protocol over HTTP) loggers; `hwtest-import-csv` |
| `hwtest-db` | rusqlite results DB using `sql/test_results_schema.sql` |
| `hwtest-instruments` | Drivers + emulators: MCC 118/134/152 (libdaqhats via libloading — the only `unsafe`, in `daqhats/ffi.rs`), Waveshare AD/DA (ADS1256/DAC8532 over spidev/gpiocdev), SCPI (TCP/serial/USBTMC) + BK 9100 PSU, SocketCAN + in-process `virtual:<name>` CAN bus, MCP23017. `registry.rs` maps rack YAML driver strings (incl. legacy `hwtest_mcc.mcc152:create_instrument`) to drivers; `HWTEST_EMULATE=1` emulates everything. Bins: `hwtest-hat-scan`, `hwtest-bk-emulator` |
| `hwtest-rack` | Rack runtime (open/verify identities, logical channels, calibration), HTTP server + NATS streaming. Bin `hwtest-rack` (`serve`/`check`/`init`/`calibrate`/`info`/`list`; a leading `.yaml` means `serve`) |
| `hwtest-run` | Run engine behind the SDK: `Session` (state sequence, cycles for HASS/HALT, measure → monitors → loggers/NATS/DB, transitions, stop/abort), event lines `@@hwtest {json}` on stdout |
| `hwtest-runner` | Station web service: operator picks test + mode, runner spawns the script (env `HWTEST_EVENTS/MODE/RUN_ID/RACK/RACK_SERIAL`, `UUT_URL`, station `environment:`), stop = SIGINT then SIGKILL. Bin `hwtest-runner station.yaml` |
| `hwtest-uut-sim` | UUT simulator (Pi 4 + Waveshare AD/DA + CAN HAT, or `--emulate`): CAN heartbeat 0x100 @10 Hz + echo, DAC/ADC, MCP23017 GPIO, fault injection; same REST API as the old Python simulator plus `/emulation/*`; blocking `UutClient`; `BackgroundServer` for embedding. Bins `hwtest-uut-sim`, `pi4-waveshare-sim` |

### Python packages

- **`hwtest-core/`** — the PyO3 extension. `native/src/` holds bindings only (`conv.rs`, one file per domain, `spec.rs`, `sdk.rs`), built by maturin as `hwtest_core._native` (abi3-py310, `multiple-pymethods`). `src/hwtest_core/types/*.py` re-export native classes; `_enums.py` (`DataType`, `ValueQuality`, `BoundType`, `MonitorVerdict`) and `errors.py` stay Python and are raised from Rust via `import_exception!`. `_native.pyi` holds the stubs — **keep it in sync with the bindings**. Classes are frozen pyclasses except `WithinBaseline`. Intentional differences from the original Python are in `hwtest-core/README.md`.
- **`hwtest-sdk/`** (distribution `hwtest`) — what test scripts import: `Test` (definition YAML + `@test.setup/step/teardown`, `run()`, `main()` CLI with `--mode/--cycles/--serial/--emulate/--nats/--db/--unit/--offsite/--no-loggers`), `Context` (`ctx.rack`, `ctx.uut`, `ctx.can`, `ctx.param`, `ctx.calibration`, `ctx.measure`, `ctx.transition`, `ctx.sleep`, `ctx.log`), `State` (YAML state parameters as attributes), `Rack`, `CanBus`, `UutClient`, `UutSimulator`, `HardwareError`; `hwtest check` / `hwtest run` CLI. Classes named `Test*` set `__test__ = False`.
- **`hwtest-intg/`** — test cases for the integration bench (not a package): `configs/` (rack, station, definitions) + `scripts/` + `tests/test_bench_emulated.py`. See its README.

### Writing a test case

```yaml
# configs/voltage_echo_monitor.yaml (abridged)
test_case: {id: voltage_echo_monitor, name: Voltage Echo Monitor Test}
rack: pi5_mcc_intg_a_rack
case_parameters: {settling_time_seconds: 0.025}
monitor_states:
  minimum: {name: Minimum Voltage, target_voltage: 1.0}
  maximum: {name: Maximum Voltage, target_voltage: 4.0}
monitors:
  echo_voltage_monitor:
    kind: bounds
    kwargs.0: {}                        # judges the UUT in slot 0
    configuration:
      minimum: {echo_voltage: {within_range: [1.0, 0.30]}}
      maximum: {echo_voltage: {within_range: [4.0, 0.30]}}
loggers:
  csv_logger: {kind: csv, kwargs: {output_dir: "${CSV_LOG_DIR:./logs}"}}
  influx: {kind: influxdb, enabled: "${TELEMETRY_ENABLED:false}", kwargs: {...}}
```

```python
import hwtest
test = hwtest.Test("voltage_echo_monitor.yaml")

@test.step
def echo(ctx, state):
    ctx.rack.write("rack_dac", state.target_voltage)
    ctx.sleep(ctx.param("settling_time_seconds"))
    ctx.uut.dac_write(0, ctx.uut.adc_read(0))
    ctx.measure(echo_voltage=ctx.rack.read("rack_adc"))

if __name__ == "__main__":
    test.main()        # python script.py --mode hass --serial 001 [--emulate]
```

YAML errors list **every** problem as `file:line:col: key.path: message` with "did you mean" suggestions (`SpecError`, a `ValueError`). Measurements are published under topic = test case id unless `ctx.measure(topic=...)`. Results DB slots are numbered from 1; YAML `kwargs.N` from 0 (mapped N+1).

### Rack configuration

```yaml
rack: {id: "pi5-mcc-intg-a", description: "..."}
instruments:
  dio_controller:
    driver: "mcc152"                       # built-ins: mcc118 mcc134 mcc152 bk_psu waveshare_adda
    identity: {manufacturer: "Measurement Computing", model: "MCC 152"}
    kwargs:
      address: 0
      analog_channels: [{id: 0, name: "rack_dac", initial_voltage: 0.0}]
  scope:
    driver: "my_drivers.scope:create"      # "module:function" = Python escape-hatch driver
```

Legacy `hwtest_<pkg>.<module>:create_instrument` strings for the old Python drivers map to the built-ins.

Logical channel names must be unique across the rack. Calibration factors per rack serial live in `~/.config/hwtest/racks/<class>_<serial>.yaml` or `/etc/hwtest/racks/` (`hwtest-rack init|calibrate`).

### Binary streaming protocol

- Big-endian byte order
- Schema message (0x01): periodic retransmission every 1 second
- Data message (0x02): packed samples with implicit timestamps
- CRC32-based schema ID for change detection

### Deployment

- `deploy/pi5_mcc_intg_a/`: rack server as systemd (`setup.sh native`: binaries from `dist/` or cargo, venv with the wheels) or Docker (Rust builder + libdaqhats built from MCC source). Settings in `/etc/hwtest/rack.env` (`HWTEST_RACK_SERIAL`, `HWTEST_NATS_URL`). Root `Dockerfile` = same image for the Orange Pi 5 rack.
- `deploy/systemd/hwtest-uut.service`: UUT simulator on the Pi 4 (`/usr/local/bin/hwtest-uut-sim`).
- `deploy/docker-compose.yml`: dev InfluxDB + Grafana (InfluxDB http://localhost:8086, Grafana http://localhost:3000, admin / hwtest-dev-password, token `hwtest-dev-token`).
- libdaqhats must be installed on MCC racks (https://github.com/mccdaq/daqhats, `sudo ./install.sh`).

## Hardware Setup

**Raspberry Pi 5 Setup**: The Waveshare HATs require device tree overlays in `/boot/firmware/config.txt`:
```
# High-Precision AD/DA uses SPI0 (no overlay needed, just dtparam=spi=on)
dtparam=spi=on

# 2-CH CAN FD HAT (if using)
dtoverlay=waveshare-can-fd-hat-mode-a

# 2-CH RS-232 HAT (if using)
dtoverlay=sc16is752-spi1,int_pin=24
```

**Raspberry Pi 4 UUT Setup (CAN FD + AD/DA)**: Recommended UUT configuration using Pi 4 with CAN FD and analog I/O:

Hardware stack (directly stacked):
- Raspberry Pi 4
- 2-CH CAN FD HAT rev 2.1 (MCP2518FD controller)
- High-Precision AD/DA board (ADS1256 ADC + DAC8532 DAC)

Configuration in `/boot/firmware/config.txt`:
```
# Enable SPI
dtparam=spi=on

# 2-CH CAN FD HAT - uses MCP2518FD on SPI0
dtoverlay=spi1-1cs
dtoverlay=mcp251xfd,spi0-0,interrupt=25
dtoverlay=mcp251xfd,spi1-0,interrupt=24

# High-Precision AD/DA board uses SPI0 CE1 - no overlay needed
```

Bring up CAN interface:
```bash
# Standard CAN (500 kbps) - compatible with MCP2515-based rack
sudo ip link set can0 up type can bitrate 500000

# Or CAN FD mode (500 kbps arbitration, 2 Mbps data)
sudo ip link set can0 up type can bitrate 500000 dbitrate 2000000 fd on
```

Run the UUT simulator with High-Precision AD/DA:
```bash
hwtest-uut-sim --port 8080 --can-interface can0 --waveshare-adda --no-gpio
```

The simulator uses the Rust ADS1256/DAC8532 drivers (`hwtest-instruments::waveshare`).

**MCC DAQ HATs + Waveshare CAN HAT Coexistence (Raspberry Pi 5)**: MCC HATs and Waveshare CAN HATs both use SPI0, but can coexist with hardware modification. This configuration has been tested and verified on Raspberry Pi 5.

**Verified hardware stack:**
- Raspberry Pi 5
- MCC 152 (address 0) - Digital I/O + Analog Out
- MCC 134 (address 1) - Thermocouple DAQ
- MCC 118 (address 4) - Voltage DAQ
- Waveshare RS485 CAN HAT (B) - modified for CE1

MCC HAT GPIO usage:
| Function | GPIO Pin |
|----------|----------|
| SPI Chip Select | GPIO8 (CE0) - all addresses share this |
| Address A0 | GPIO12 |
| Address A1 | GPIO13 |
| Address A2 | GPIO26 |
| SPI MISO/MOSI/SCLK | GPIO9/10/11 |

The MCC library uses GPIO12/13/26 to select which board (address 0-7) responds, then communicates via SPI0 CE0. All MCC HATs share CE0, leaving CE1 available.

**Waveshare RS485 CAN HAT (B) hardware modification**:
1. Move the 0Ω resistor on the back from CE0 (GPIO8) position to CE1 (GPIO7) position
2. Keep interrupt on GPIO25 (do NOT use GPIO13, which conflicts with MCC A1)

Configuration in `/boot/firmware/config.txt` (Pi 5):
```
# Enable SPI
dtparam=spi=on

# MCP2515 CAN on spi0.1 (CE1/GPIO7) - modified CAN HAT
dtoverlay=mcp2515-can1,oscillator=12000000,interrupt=25
```

Resulting pin allocation:
| Device | SPI | CS Pin | Other GPIOs |
|--------|-----|--------|-------------|
| MCC HATs | spi0.0 | CE0 (GPIO8) | GPIO12/13/26 (address) |
| CAN HAT (modified) | spi0.1 | CE1 (GPIO7) | GPIO25 (interrupt) |

The `mcp2515-can1` overlay parameters:
- `oscillator`: Crystal frequency in Hz (12000000 for Waveshare RS485 CAN HAT B)
- `spimaxfrequency`: SPI clock rate (default 10000000)
- `interrupt`: GPIO pin for INT (default 25)

Bring up CAN interface:
```bash
sudo ip link set can0 up type can bitrate 500000
```

Verify MCC HAT detection:
```bash
python3 -c "from daqhats import hat_list, HatIDs; [print(f'Address {h.address}: {HatIDs(h.id).name}') for h in hat_list()]"
```


## Code Standards

- Rust: `cargo fmt`, `cargo clippy` (pedantic) clean, doc comments on public items, `unsafe` only in `daqhats/ffi.rs` with `// SAFETY:` comments
- Python: PEP 8 / black at 100 columns, `mypy --strict`, pylint clean, Google-style docstrings
- Bindings are conversion glue only; the crates are the single source of truth for formats and evaluation
- Wire formats are a contract: change `crates/hwtest-core/tests/fixtures/` deliberately

## Testing Philosophy

### Prefer Integration Tests and Simulators Over Mocking

This project prioritizes **real hardware testing** and **behavioral simulators** over unit tests with mocks. The testing hierarchy (in order of preference):

1. **Integration tests with real hardware** - Tests that exercise actual instruments and communication paths
2. **Integration tests with simulators** - Tests using in-process or networked simulators that faithfully replicate hardware behavior
3. **Unit tests without mocks** - Pure logic tests for data types, algorithms, and transformations
4. **Unit tests with mocks** - Only when hardware dependencies make other approaches impractical

**Why minimize mocking:**
- Mocks test the test, not the code - they verify you called methods correctly, not that the system works
- Mocks hide integration bugs that only surface with real hardware timing and state
- Mock-heavy tests are brittle and require maintenance when implementations change
- Hardware behavior is subtle - simulators capture edge cases that mocks miss

**When mocking is acceptable:**
- Testing error handling paths that are hard to trigger with real hardware
- Isolating a specific component during initial development
- Testing timeout and retry logic


### Simulators and emulators

Prefer behavioural emulators to mocks. Available without hardware:
- `HWTEST_EMULATE=1` / `--emulate`: every rack driver (MCC HATs, Waveshare board at SPI level, BK PSU over SCPI, MCP23017); `Rack.emu_*` hooks set inputs and wire outputs to inputs
- `hwtest-uut-sim --emulate` or `hwtest.UutSimulator(can_interface="virtual:x")` in-process; `CanBus("virtual:x")` shares the bus
- `hwtest-bk-emulator`: BK PSU over TCP for VISA-style tools
- Real services: NATS (`docker run -p 4222:4222 nats -js`, then `HWTEST_NATS_URL=nats://localhost:4222 cargo test -p hwtest-nats`), InfluxDB via `deploy/docker-compose.yml`

Coverage of the Python layer: `python scripts/run_coverage.py [--analyze-mocks]`.
