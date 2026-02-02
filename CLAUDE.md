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
pip install -e "./hwtest-core[dev]" -e "./hwtest-scpi[dev]" -e "./hwtest-bkprecision[dev]" -e "./hwtest-mcc[dev]" -e "./hwtest-waveshare[dev]" -e "./hwtest-rack[dev]" -e "./hwtest-uut[dev]" -e "./hwtest-intg[dev]"

# Testing (run from each package directory)
cd hwtest-core && python3 -m pytest tests/unit/ -v && cd ..
cd hwtest-scpi && python3 -m pytest tests/unit/ -v && cd ..
cd hwtest-bkprecision && python3 -m pytest tests/unit/ -v && cd ..
cd hwtest-mcc && python3 -m pytest tests/unit/ -v && cd ..
cd hwtest-waveshare && python3 -m pytest tests/unit/ -v && cd ..
cd hwtest-rack && python3 -m pytest tests/unit/ -v && cd ..
cd hwtest-uut && python3 -m pytest tests/unit/ -v && cd ..

# Integration tests (requires hardware setup)
cd hwtest-intg && UUT_URL=http://<uut-ip>:8080 python3 -m pytest tests/integration/ -v -m integration && cd ..

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
  ├── hwtest-waveshare  (depends on hwtest-core; optional spidev, lgpio)
  │     └── hwtest-uut  (depends on hwtest-waveshare; fastapi, uvicorn, python-can, smbus2)
  ├── hwtest-rack  (depends on hwtest-core; fastapi, uvicorn, pyyaml)
  └── hwtest-intg  (depends on hwtest-core, hwtest-rack; python-can, httpx, pytest)
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

### Waveshare HAT Drivers (hwtest-waveshare)

Drivers for Waveshare HAT boards (Raspberry Pi 5 compatible via lgpio):

- **High-Precision AD/DA Board** (`high_precision_ad_da.py`): Combined ADC/DAC instrument
  - `HighPrecisionAdDaInstrument`: Unified driver for ADC reads and DAC writes
  - `AdcChannel`, `DacChannel`: Channel mapping with aliases
  - `HighPrecisionAdDaConfig`: Full board configuration
  - `create_instrument()`: Factory entry point

- **ADS1256** (`ads1256.py`): 8-channel 24-bit ADC (30 kSPS max)
  - `Ads1256`: Low-level SPI driver
  - `Ads1256Gain` enum: GAIN_1, GAIN_2, GAIN_4, GAIN_8, GAIN_16, GAIN_32, GAIN_64
  - `Ads1256DataRate` enum: SPS_2_5 to SPS_30000
  - Single-ended and differential measurements
  - `read_voltage()`, `read_differential()`, `read_all_channels()`

- **DAC8532** (`dac8532.py`): 2-channel 16-bit DAC (0-5V output)
  - `Dac8532`: Low-level SPI driver
  - `write_voltage()`, `write_raw()`, `write_both()`
  - Software readback of last written values

- **GPIO Abstraction** (`gpio.py`): Pi 5 compatible GPIO layer
  - Uses `lgpio` library for RP1 chip compatibility
  - `Gpio`: Pin management with setup/input/output/close

All Waveshare drivers implement `get_identity()` returning `InstrumentIdentity` with manufacturer="Waveshare".

### UUT Simulator (hwtest-uut)

Simulated Unit Under Test for integration testing, designed for Raspberry Pi Zero:

- **CAN Interface** (`can_interface.py`): SocketCAN wrapper with async support
  - `CanInterface`: Send/receive CAN messages via python-can
  - `CanMessage`: Message dataclass with arbitration ID, data, extended ID, CAN FD support
  - `CanConfig`: Interface configuration (interface name, bitrate, FD mode)
  - Echo mode with configurable ID offset and message filtering
  - Async receive loop with callbacks
  - Automatic heartbeat at 10 Hz (configurable) with 8-byte incrementing counter

- **ADS1263 ADC** (`ads1263.py`): 32-bit high-precision ADC driver
  - `Ads1263`: SPI driver with software-controlled chip select for SPI bus sharing
  - `Ads1263Config`: Pin mapping, reference voltage, gain, and data rate
  - `Ads1263Gain` enum: GAIN_1, GAIN_2, GAIN_4, GAIN_8, GAIN_16, GAIN_32
  - `Ads1263DataRate` enum: SPS_2_5 to SPS_38400
  - 10 single-ended channels (AIN0-AIN9) plus internal references
  - `read_voltage()`, `read_differential()`, `read_all_channels()`, `read_raw()`
  - Uses spidev0.1 with GPIO22 for software CS to coexist with CAN HAT on spidev0.0

- **MCP23017 GPIO Expander** (`mcp23017.py`): 16-bit I2C GPIO driver
  - `Mcp23017`: I2C driver via smbus2
  - `Mcp23017Config`: I2C bus and address configuration (0x20-0x27)
  - `PinDirection` enum: INPUT, OUTPUT
  - Per-pin direction, pull-up resistor control
  - Port-level and 16-bit read/write operations
  - `set_pin_direction()`, `write_pin()`, `read_pin()`, `set_pullup()`
  - `write_port()`, `read_port()`, `write_all()`, `read_all()`

- **UUT Simulator** (`simulator.py`): Main integration class
  - `UutSimulator`: Combines CAN, GPIO, DAC, and ADC interfaces
  - `SimulatorConfig`: Enable/disable individual interfaces
  - CAN echo mode for loopback testing
  - DAC voltage output (via hwtest-waveshare)
  - ADC voltage reading (via hwtest-waveshare or ADS1263)
  - GPIO digital I/O (via MCP23017)

- **REST API Server** (`server.py`): FastAPI-based remote control
  - `GET /`: HTML dashboard with interface status
  - `GET /health`: Health check endpoint
  - `GET /status`: Full simulator status
  - `POST /can/send`: Send CAN message
  - `GET /can/received`: Get received messages
  - `PUT /can/echo`: Configure echo mode
  - `GET /can/heartbeat`: Get heartbeat status (running, message count, config)
  - `POST /dac/write`: Write DAC voltage
  - `GET /dac/{channel}`: Read DAC channel
  - `GET /adc/{channel}`: Read ADC channel
  - `POST /gpio/configure`: Set pin direction
  - `POST /gpio/write`: Write pin value
  - `GET /gpio/{pin}`: Read pin value

Run the UUT simulator:
```bash
uut-simulator --port 8080
# Or with options:
uut-simulator --can-interface can0 --gpio-address 0x20 --no-adc --debug
```

**Raspberry Pi 5 Setup**: The Waveshare HATs require device tree overlays in `/boot/firmware/config.txt`:
```
# High-Precision AD/DA uses SPI0 (no overlay needed, just dtparam=spi=on)
dtparam=spi=on

# 2-CH CAN FD HAT (if using)
dtoverlay=waveshare-can-fd-hat-mode-a

# 2-CH RS-232 HAT (if using)
dtoverlay=sc16is752-spi1,int_pin=24
```

**Raspberry Pi Zero Dual-HAT Setup (CAN + ADC)**: When using both RS485 CAN HAT and High-Precision AD HAT together:

Hardware stacking order (bottom to top): Pi Zero → RS485 CAN HAT → High-Precision AD HAT

Both HATs share the SPI bus but use different chip selects:
- **RS485 CAN HAT** (MCP2515): Uses kernel driver on spi0.0 with hardware CE0 (GPIO8)
- **High-Precision AD HAT** (ADS1263): Uses spidev0.1 with software CS on GPIO22

Configuration in `/boot/config.txt`:
```
# Enable SPI
dtparam=spi=on

# MCP2515 CAN controller (spi0.0, CE0/GPIO8)
dtoverlay=mcp2515-can0,oscillator=8000000,interrupt=25

# Enable spidev0.1 for ADC (software CS via GPIO22)
dtoverlay=spi0-1cs
```

GPIO pin assignments for ADS1263:
- DRDY: GPIO17 (data ready signal)
- RESET: GPIO18 (hardware reset)
- CS: GPIO22 (software-controlled chip select)

Example configuration:
```python
from hwtest_uut import Ads1263, Ads1263Config, CanInterface, CanConfig

# ADC with software CS to share SPI bus
adc_config = Ads1263Config(
    spi_bus=0,
    spi_device=1,   # spidev0.1
    cs_pin=22,      # Software CS
    drdy_pin=17,
    reset_pin=18,
)

# CAN uses kernel driver (bring up interface first)
# sudo ip link set can0 up type can bitrate 500000
can_config = CanConfig(interface="can0", bitrate=500000)
```

Bring up CAN interface:
```bash
sudo ip link set can0 up type can bitrate 500000
```

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

### Integration Tests (hwtest-intg)

Integration test package containing tests and reusable fixtures for hardware validation:

- **CAN Interface** (`can/interface.py`): Test rack CAN operations
  - `RackCanInterface`: Wraps python-can for test rack CAN bus access
  - `RackCanConfig`: Interface configuration (interface name, bitrate)
  - `wait_for_heartbeat()`: Wait for UUT heartbeat messages
  - `echo_test()`: Send message and verify echo response
  - `collect_messages()`: Collect messages for a duration

- **UUT Client** (`clients/uut_client.py`): Async HTTP client for UUT REST API
  - `UutClient`: httpx-based client for UUT simulator
  - `health()`, `status()`: Health and status endpoints
  - `can_send()`, `can_set_echo()`, `can_get_heartbeat()`: CAN control
  - `dac_write()`, `dac_read()`: DAC operations
  - `adc_read()`: ADC operations
  - `gpio_configure()`, `gpio_write()`, `gpio_read()`: GPIO operations

- **Config Utilities** (`utils/config.py`): Package resource loading
  - `load_rack_config(name)`: Load YAML configs from package resources
  - `get_config_path(name)`: Get filesystem path to config file

- **Pytest Fixtures** (`fixtures/conftest.py`): Reusable test fixtures
  - `rack_can`: Opened CAN interface fixture
  - `uut_client`: Async HTTP client fixture
  - `uut_url`: UUT URL from `UUT_URL` environment variable
  - `can_interface_name`: CAN interface from `CAN_INTERFACE` env var

- **CAN Message ID Constants**:
  - `UUT_HEARTBEAT_ID = 0x100`: UUT heartbeat message ID
  - `RACK_TEST_MSG_ID = 0x200`: Rack test message ID
  - `ECHO_ID_OFFSET = 0x10`: Offset for echoed messages

Run integration tests:
```bash
cd hwtest-intg
UUT_URL=http://192.168.68.xxx:8080 pytest tests/integration/ -v -m integration
```

The UUT simulator now automatically sends heartbeat messages at 10 Hz when CAN is enabled. The heartbeat contains an 8-byte incrementing counter.

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
