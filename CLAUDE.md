# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

hwtest is a monorepo for hardware test automation tools designed for HASS (Highly Accelerated Stress Screening) and HALT (Highly Accelerated Life Testing) via instrument automation. The system runs on Linux (SBCs like Orange Pi 5, Beelink, and Docker containers) with Python 3.10+.

## Build and Development Commands

Each package has its own directory. Use a shared venv or per-package venvs. Example for the full stack:

```bash
# Setup (shared venv)
python3 -m venv .venv
source .venv/bin/activate
pip install -e "./hwtest-core[dev]" -e "./hwtest-scpi[dev]" -e "./hwtest-bkprecision[dev]" -e "./hwtest-mcc[dev]" -e "./hwtest-rack[dev]"

# Testing (run from each package directory)
cd hwtest-core && python3 -m pytest tests/unit/ -v && cd ..
cd hwtest-scpi && python3 -m pytest tests/unit/ -v && cd ..
cd hwtest-bkprecision && python3 -m pytest tests/unit/ -v && cd ..
cd hwtest-mcc && python3 -m pytest tests/unit/ -v && cd ..
cd hwtest-rack && python3 -m pytest tests/unit/ -v && cd ..

# Single file / single test
python3 -m pytest hwtest-core/tests/unit/test_common_types.py
python3 -m pytest hwtest-core/tests/unit/test_common_types.py::TestTimestamp::test_now

# Linting and formatting (run from each package directory)
python3 -m black --check --line-length 100 src/ tests/
python3 -m mypy --strict src/
python3 -m pylint src/
```

## Architecture

### System Layers

1. **Test Execution**: Test cases control environmental conditions (temperature, vibration) and UUT state
2. **Measurement**: Instruments collect telemetry, publish to NATS JetStream (<25ms software latency budget)
3. **Monitoring**: Monitors evaluate telemetry against state-dependent thresholds
4. **Persistence**: Loggers archive telemetry (CSV or InfluxDB)

### Package Dependency Chain

```
hwtest-core  (stdlib-only, no external deps)
  ├── hwtest-scpi  (depends on hwtest-core; optional pyvisa)
  │     └── hwtest-bkprecision  (depends on hwtest-scpi)
  ├── hwtest-mcc  (depends on hwtest-core; optional daqhats)
  └── hwtest-rack  (depends on hwtest-core; fastapi, uvicorn, pyyaml)
```

### Core Library (hwtest-core)

**Types** (`src/hwtest_core/types/`):
- `common.py`: Timestamp (nanosecond precision), SourceId, ChannelId, StateId, DataType enum, InstrumentIdentity
- `telemetry.py`: TelemetryValue, TelemetryMessage (batch with sequence numbers)
- `state.py`: EnvironmentalState, StateTransition
- `threshold.py`: Threshold (bounds), StateThresholds (per-state)
- `monitor.py`: MonitorVerdict, MonitorResult, ThresholdViolation
- `streaming.py`: StreamField, StreamSchema (CRC32 ID), StreamData (binary protocol)

**Interfaces** (`src/hwtest_core/interfaces/`): Protocol-based definitions for TelemetryPublisher/Subscriber, StatePublisher/Subscriber, Monitor, ThresholdProvider, StreamPublisher/Subscriber

### SCPI Library (hwtest-scpi)

- `ScpiConnection`: Command/query interface with automatic error checking (`SYST:ERR?`), typed query methods (`query_number`, `query_bool`, `query_int`, `query_numbers`), and `get_identity()` for `*IDN?` parsing
- `ScpiTransport` Protocol: `write(str)`, `read() -> str`, `close()` — implemented by `VisaResource` and emulators
- `VisaResource`: PyVISA-backed transport for real instruments
- `parse_idn_response()`: Parses `*IDN?` responses into `InstrumentIdentity`

### Instrument Drivers (hwtest-bkprecision)

- `BkDcPsu`: High-level driver for BK Precision 9100 series DC power supplies (9115, 9130B)
- `BkDcPsuEmulator`: In-process emulator implementing `ScpiTransport` with SCPI command normalization
- `EmulatorServer`: TCP server wrapping any emulator for external VISA/telnet access
- `create_instrument(visa_address)`: Factory entry point for test rack dynamic loading

### MCC DAQ HAT Drivers (hwtest-mcc)

Drivers for Measurement Computing DAQ HAT boards (Raspberry Pi / Orange Pi compatible):

- **MCC 118** (`mcc118.py`): 8-channel voltage DAQ (±10V, 100kS/s aggregate)
  - `Mcc118Instrument`: Continuous scanning with `StreamData` publishing
  - `Mcc118Channel`, `Mcc118Config`: Channel mapping and configuration
  - `create_instrument()`: Factory entry point

- **MCC 134** (`mcc134.py`): 4-channel thermocouple DAQ
  - `Mcc134Instrument`: Polling-based temperature reads with `StreamData` publishing
  - `ThermocoupleType` enum: TYPE_J, TYPE_K, TYPE_T, TYPE_E, TYPE_R, TYPE_S, TYPE_B, TYPE_N
  - `Mcc134Channel`, `Mcc134Config`: Channel mapping with thermocouple type selection
  - `create_instrument()`: Factory entry point

- **MCC 152** (`mcc152.py`): 8 digital I/O + 2 analog outputs (0-5V)
  - `Mcc152Instrument`: Synchronous control interface (not streaming)
  - `DioDirection` enum: INPUT, OUTPUT
  - `dio_read()`, `dio_write()`, `analog_write()`: Channel operations by name or ID
  - `create_instrument()`: Factory entry point

All MCC drivers implement `get_identity()` returning `InstrumentIdentity` with manufacturer="Measurement Computing".

### Test Rack Service (hwtest-rack)

FastAPI-based service for rack orchestration and instrument discovery:

- **Config** (`config.py`): YAML configuration loading
  - `RackConfig`: Rack ID, description, instrument list
  - `InstrumentConfig`: Driver path, expected identity, kwargs
  - `load_config(path)`: Parse YAML into config objects

- **Loader** (`loader.py`): Dynamic driver loading via `importlib`
  - `load_driver("module:function")`: Import and return factory callable

- **Rack** (`rack.py`): Instrument lifecycle management
  - `Rack`: Initialize instruments, verify identities, track status
  - `ManagedInstrument`: State tracking (pending/initializing/ready/error/closed)

- **Server** (`server.py`): REST API endpoints
  - `GET /`: HTML dashboard with instrument status
  - `GET /health`: Health check (returns rack state)
  - `GET /status`: Full rack status JSON
  - `GET /instruments`: List all instruments
  - `GET /instruments/{name}`: Get specific instrument details

- **Models** (`models.py`): Pydantic response models
  - `InstrumentStatus`, `RackStatus`, `HealthResponse`, `IdentityModel`

### Key Design Patterns

- **Protocol-based interfaces**: Uses `typing.Protocol` for decoupled implementations
- **Immutable data types**: Frozen dataclasses with `to_dict`/`from_dict` serialization
- **State-dependent thresholds**: Measurement norms vary by environmental state
- **Transition states**: Evaluation suspended during state changes to avoid false failures
- **Channel aliasing**: Logical names (e.g., "dut_power") decouple tests from physical hardware
- **Instrument identity verification**: Rack confirms manufacturer/model at startup via `get_identity()` (SCPI instruments use `*IDN?`, MCC HATs use daqhats serial API)
- **Factory entry points**: Each instrument driver exposes a `create_instrument()` function for dynamic loading by the test rack and programmatic use in tests
- **SCPI command normalization**: Emulators accept any valid SCPI form (long/short, optional segments) by normalizing to canonical short-form keys

### Rack Configuration Format

Example YAML config (`configs/orange_pi_5_rack.yaml`):

```yaml
rack:
  id: "orange-pi-5-integration"
  description: "Orange Pi 5 integration test rack"

instruments:
  dio_controller:
    driver: "hwtest_mcc.mcc152:create_instrument"
    identity:
      manufacturer: "Measurement Computing"
      model: "MCC 152"
    kwargs:
      address: 0
      source_id: "dio_controller"
      dio_channels:
        - {id: 0, name: "relay_power", direction: "OUTPUT"}
      analog_channels:
        - {id: 0, name: "control_voltage", initial_voltage: 0.0}

  voltage_daq:
    driver: "hwtest_mcc.mcc118:create_instrument"
    identity:
      manufacturer: "Measurement Computing"
      model: "MCC 118"
    kwargs:
      address: 1
      sample_rate: 1000.0
      source_id: "voltage_daq"
      channels:
        - {id: 0, name: "dut_voltage"}
```

Run the rack server:
```bash
hwtest-rack configs/orange_pi_5_rack.yaml --port 8000
# Or: python -m hwtest_rack.server configs/orange_pi_5_rack.yaml
```

### Binary Streaming Protocol

- Big-endian byte order
- Schema message (0x01): Periodic retransmission every 1 second
- Data message (0x02): Packed samples with implicit timestamps
- CRC32-based schema ID for change detection

## Code Standards

- PEP 8 with 100 character line length
- Full type hints required (`mypy --strict`)
- Google-style docstrings for public APIs
- Tests mirror source structure in `tests/unit/`
- Minimal external dependencies (core library is stdlib-only)
