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
pip install -e "./hwtest-core[dev]" -e "./hwtest-scpi[dev]" -e "./hwtest-bkprecision[dev]"

# Testing (run from each package directory)
cd hwtest-core && python3 -m pytest tests/unit/ -v && cd ..
cd hwtest-scpi && python3 -m pytest tests/unit/ -v && cd ..
cd hwtest-bkprecision && python3 -m pytest tests/unit/ -v && cd ..

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
  └── hwtest-scpi  (depends on hwtest-core; optional pyvisa)
        └── hwtest-bkprecision  (depends on hwtest-scpi)
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

### Key Design Patterns

- **Protocol-based interfaces**: Uses `typing.Protocol` for decoupled implementations
- **Immutable data types**: Frozen dataclasses with `to_dict`/`from_dict` serialization
- **State-dependent thresholds**: Measurement norms vary by environmental state
- **Transition states**: Evaluation suspended during state changes to avoid false failures
- **Channel aliasing**: Logical names (e.g., "dut_power") decouple tests from physical hardware
- **Instrument identity verification**: Rack confirms manufacturer/model via `*IDN?` at startup
- **Factory entry points**: Each instrument driver exposes a `create_instrument()` function for dynamic loading by the test rack and programmatic use in tests
- **SCPI command normalization**: Emulators accept any valid SCPI form (long/short, optional segments) by normalizing to canonical short-form keys

### Test Rack

The test rack runs as a standalone service given a YAML config at startup. It dynamically loads instrument driver classes from the Python namespace using `importlib`. Each instrument entry in the YAML specifies a `driver` (module path + function), expected `identity` (manufacturer/model), and `kwargs` (visa_address, etc.). At startup the rack verifies each instrument's identity via `*IDN?` and flags errors if the wrong instrument type is detected. Communication between rack and instruments uses NATS JetStream for operational telemetry (captured by loggers) and direct function calls for initialization metadata.

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
