# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

hwtest is a monorepo for hardware test automation tools designed for HASS (Highly Accelerated Stress Screening) and HALT (Highly Accelerated Life Testing) via instrument automation. The system runs on Linux (SBCs like Orange Pi 5, Beelink, and Docker containers) with Python 3.10+.

## Build and Development Commands

All commands should be run from the `hwtest-core/` directory after activating the virtual environment:

```bash
# Setup
cd hwtest-core
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Testing
python3 -m pytest tests/unit/                    # All tests
python3 -m pytest tests/unit/test_telemetry.py  # Single file
python3 -m pytest tests/unit/test_common_types.py::TestTimestamp::test_now  # Single test
python3 -m pytest tests/unit/ --cov=src/hwtest_core  # With coverage

# Linting and formatting (run all before commits)
python3 -m black --line-length 100 src/ tests/
python3 -m mypy --strict src/
python3 -m pylint src/
```

## Architecture

### System Layers

1. **Test Execution**: Test cases control environmental conditions (temperature, vibration) and UUT state
2. **Measurement**: Instruments collect telemetry, publish to NATS JetStream (<25ms software latency budget)
3. **Monitoring**: Monitors evaluate telemetry against state-dependent thresholds
4. **Persistence**: Loggers archive telemetry (CSV or InfluxDB)

### Core Library Structure (hwtest-core)

**Types** (`src/hwtest_core/types/`):
- `common.py`: Timestamp (nanosecond precision), SourceId, ChannelId, StateId, DataType enum
- `telemetry.py`: TelemetryValue, TelemetryMessage (batch with sequence numbers)
- `state.py`: EnvironmentalState, StateTransition
- `threshold.py`: Threshold (bounds), StateThresholds (per-state)
- `monitor.py`: MonitorVerdict, MonitorResult, ThresholdViolation
- `streaming.py`: StreamField, StreamSchema (CRC32 ID), StreamData (binary protocol)

**Interfaces** (`src/hwtest_core/interfaces/`): Protocol-based definitions for TelemetryPublisher/Subscriber, StatePublisher/Subscriber, Monitor, ThresholdProvider, StreamPublisher/Subscriber

### Key Design Patterns

- **Protocol-based interfaces**: Uses `typing.Protocol` for decoupled implementations
- **Immutable data types**: Frozen dataclasses with `to_dict`/`from_dict` serialization
- **State-dependent thresholds**: Measurement norms vary by environmental state
- **Transition states**: Evaluation suspended during state changes to avoid false failures
- **Channel aliasing**: Logical names (e.g., "dut_power") decouple tests from physical hardware

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
