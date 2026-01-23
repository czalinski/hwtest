# hwtest-core Design Specification

## Purpose

Provide core data types and interface definitions for the hwtest ecosystem. This library defines the contracts that all other hwtest components implement, enabling a clean separation between API and implementation.

## Scope

### In Scope

- Data types for hardware test operations
- Interface definitions (Protocol classes, ABCs)
- Enumerations and constants
- Type aliases
- Serialization/deserialization helpers for message types

### Out of Scope

- Runnable services or applications
- External service integrations (NATS, databases, etc.)
- Hardware-specific implementations
- Instrument drivers

## Instruments

Test instruments are the primary data sources in the hwtest ecosystem. This section defines the conceptual model for instruments.

### Instrument Model

An instrument represents a physical test device (multimeter, oscilloscope, power supply, temperature sensor, etc.) with one or more measurement or control channels.

```
┌─────────────────────────────────────────────────────────────┐
│                        Instrument                           │
│  ┌─────────────┐  ┌─────────────┐       ┌─────────────┐    │
│  │  Channel 0  │  │  Channel 1  │  ...  │  Channel N  │    │
│  │  (stream)   │  │  (stream)   │       │  (stream)   │    │
│  └──────┬──────┘  └──────┬──────┘       └──────┬──────┘    │
└─────────┼────────────────┼─────────────────────┼───────────┘
          │                │                     │
          ▼                ▼                     ▼
    Schema + Data    Schema + Data         Schema + Data
     (telemetry)      (telemetry)           (telemetry)
```

### Channel Configuration

Instruments vary in how their channels are configured:

| Configuration Type | Description | Examples |
|-------------------|-------------|----------|
| **Fixed** | Hardware-defined channels and modes | 4-channel oscilloscope, 6.5-digit DMM |
| **Software-configurable** | Channels/modes set via software API | Modular DAQ, programmable MUX |
| **Hardware-configurable** | Physical switches/jumpers, but detectable via software | DIP-switch selected ranges |

### Configuration Constraints

- **Initialization only**: Channel configuration occurs at service startup
- **No dynamic changes**: Configuration cannot be modified while the service is running
- **Detection at startup**: For hardware-configurable instruments, the service detects the current configuration at initialization

This constraint simplifies the system design:
- Schemas are defined once at startup and do not change
- Consumers can cache schema information
- No need to handle mid-stream configuration changes

### Telemetry Streams

Each channel produces an independent telemetry stream using the streaming data protocol (see [Streaming Data Protocol](#streaming-data-protocol)):

1. **Schema message**: Sent at startup and every 1 second, defines the channel's data format
2. **Data messages**: Continuous stream of samples with timestamps

Multiple channels from the same instrument may share a NATS subject prefix but have distinct stream identifiers:

```
telemetry.instrument.dmm01.ch0    # Channel 0 stream
telemetry.instrument.dmm01.ch1    # Channel 1 stream
telemetry.instrument.scope01.ch0  # Different instrument
```

### Instrument Class Hierarchy

`Instrument` is an abstract base class. Specific instrument types derive from it:

```
Instrument (ABC)
├── ReadOnlyInstrument
│   ├── DMM (Digital Multimeter)
│   ├── TemperatureSensor
│   └── ...
└── CommandableInstrument
    ├── PSU (Power Supply Unit)
    ├── FunctionGenerator
    └── ...
```

### Instrument Categories

#### Read-Only Instruments

Instruments that only produce measurements (e.g., DMMs, temperature sensors):

- Start up and immediately begin publishing telemetry
- No command channel
- Each channel publishes measurement data via streaming protocol

```
┌─────────────────────────────────────────┐
│         Read-Only Instrument            │
│  ┌─────────────┐    ┌─────────────┐    │
│  │  Channel 0  │    │  Channel 1  │    │
│  └──────┬──────┘    └──────┬──────┘    │
└─────────┼───────────────────┼──────────┘
          │                   │
          ▼                   ▼
    Telemetry Out       Telemetry Out
```

#### Commandable Instruments

Instruments that accept control commands (e.g., PSUs, function generators):

- Subscribe to a command channel for control inputs
- Publish telemetry for each channel (including commanded state)
- Commands affect instrument behavior (e.g., set voltage output)

```
┌─────────────────────────────────────────┐
│       Commandable Instrument            │
│  ┌─────────────┐    ┌─────────────┐    │
│  │  Channel 0  │    │  Channel 1  │    │
│  └──────┬──────┘    └──────┬──────┘    │
│         │                   │          │
│    ┌────┴────┐         ┌────┴────┐     │
│    │ Command │         │ Command │     │
│    │ Handler │         │ Handler │     │
│    └────┬────┘         └────┬────┘     │
└─────────┼───────────────────┼──────────┘
          │                   │
    ┌─────┴─────┐       ┌─────┴─────┐
    ▼           ▼       ▼           ▼
Command In  Telemetry  Command In  Telemetry
            Out                    Out
```

### Command and Telemetry Value Model

For commandable instruments, each controllable parameter produces three related values in telemetry:

| Value Type | Description | Example |
|------------|-------------|---------|
| **desired** | The value requested by the command | 4.999 V |
| **actual_set** | The value the instrument actually set (limited by precision) | 5.000 V |
| **measured** | The physical value currently observed (affected by load, limits) | 2.000 V |

#### Example: PSU Voltage Control

```
Command: set_voltage(channel=0, voltage=4.999)

Telemetry output for channel 0:
┌────────────────────────────────────────────────────────────┐
│  voltage_desired:  4.999 V   (exact command echo)          │
│  voltage_set:      5.000 V   (instrument precision limit)  │
│  voltage_measured: 2.000 V   (current-limited output)      │
│  current_measured: 1.000 A   (at current limit)            │
└────────────────────────────────────────────────────────────┘
```

This three-value model enables:
- **Command verification**: Confirm the instrument received the command
- **Precision awareness**: Detect when instrument precision differs from requested
- **Fault detection**: Identify when physical output differs from set point (e.g., current limiting, short circuit, open load)

#### Telemetry Field Naming Convention

For commandable parameters, telemetry fields follow this naming pattern:

```
{parameter}_desired    # Commanded value
{parameter}_set        # Actual instrument setting
{parameter}_measured   # Physical measurement
```

Example schema for a PSU channel:
```python
StreamSchema(
    source_id=SourceId("psu01.ch0"),
    fields=(
        StreamField("voltage_desired", DataType.F32, "V"),
        StreamField("voltage_set", DataType.F32, "V"),
        StreamField("voltage_measured", DataType.F32, "V"),
        StreamField("current_desired", DataType.F32, "A"),
        StreamField("current_set", DataType.F32, "A"),
        StreamField("current_measured", DataType.F32, "A"),
        StreamField("output_enabled", DataType.U8, ""),
    ),
)
```

### Command Channel

Commandable instruments subscribe to a command channel for control messages:

```
command.instrument.psu01.ch0    # Commands for PSU channel 0
command.instrument.psu01.ch1    # Commands for PSU channel 1
```

Command message format and specific command types are defined per instrument class (e.g., PSU interface specification).

### Instrument Interface

Instrument drivers (implemented outside hwtest-core) should expose:

- List of available channels
- Channel configuration/capabilities
- Per-channel StreamPublisher for telemetry output
- Initialization/shutdown lifecycle

For commandable instruments, additionally:

- Per-channel command subscriber
- Command validation and execution
- Error reporting for invalid commands

## Test Rack

A **TestRack** is a concrete class representing a physical collection of instruments and components. It serves as the intermediary between test case software and the hardware.

### Role in Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Test Case Software                              │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
                          Commands / State Changes
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         TestRack Service                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │    PSU      │  │    DMM      │  │   Scope     │  │   Sensor    │    │
│  │  (BK Prec)  │  │  (Keysight) │  │  (Rigol)    │  │  (Custom)   │    │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘    │
└─────────┼────────────────┼────────────────┼────────────────┼───────────┘
          │                │                │                │
          ▼                ▼                ▼                ▼
    Physical Hardware (USB, GPIB, Serial, Ethernet, etc.)
```

### YAML Configuration

A test rack is defined via a YAML configuration file that specifies:

- Rack identification
- List of instruments with their configuration
- Connection parameters for each instrument
- Channel-specific settings

#### Example Configuration

```yaml
rack:
  id: "rack-01"
  name: "HALT Chamber Rack A"
  description: "Primary test rack for thermal/vibration testing"

instruments:
  - id: "psu01"
    type: "bk_precision_9140"
    connection:
      interface: "usb"
      port: "/dev/ttyUSB0"
      baudrate: 57600
    channels:
      - id: 0
        name: "DUT_3V3"
        voltage_limit: 3.6
        current_limit: 2.0
      - id: 1
        name: "DUT_5V"
        voltage_limit: 5.5
        current_limit: 3.0
      - id: 2
        name: "DUT_12V"
        voltage_limit: 13.0
        current_limit: 5.0

  - id: "dmm01"
    type: "keysight_34461a"
    connection:
      interface: "ethernet"
      address: "192.168.1.100"
    channels:
      - id: 0
        name: "DUT_VOLTAGE"
        mode: "dc_voltage"
        range: "10V"

  - id: "temp01"
    type: "thermocouple_reader"
    connection:
      interface: "serial"
      port: "/dev/ttyUSB1"
    channels:
      - id: 0
        name: "CHAMBER_TEMP"
      - id: 1
        name: "DUT_TEMP"
```

### TestRack Lifecycle

1. **Load Configuration**: Parse YAML file, validate structure
2. **Initialize Instruments**: Create instrument instances with configuration
3. **Connect**: Establish connections to all instruments
4. **Start Telemetry**: Begin publishing from all channels
5. **Run**: Process commands, publish telemetry (runs as service)
6. **Shutdown**: Stop telemetry, disconnect instruments, cleanup

### TestRack as a Service

The TestRack runs as an independent service process:

- **Single process per rack**: One service manages all instruments in a rack
- **Telemetry aggregation**: All instrument channels publish to NATS
- **Command routing**: Incoming commands routed to appropriate instrument/channel
- **Health monitoring**: Track instrument connectivity and errors
- **Graceful shutdown**: Clean disconnect on service termination

### NATS Subject Hierarchy

With the rack as service, subjects follow this pattern:

```
telemetry.rack.{rack_id}.{instrument_id}.{channel_id}
command.rack.{rack_id}.{instrument_id}.{channel_id}
status.rack.{rack_id}                                  # Rack health/status
status.rack.{rack_id}.{instrument_id}                  # Instrument status
```

Example:
```
telemetry.rack.rack-01.psu01.ch0      # PSU channel 0 telemetry
command.rack.rack-01.psu01.ch0        # PSU channel 0 commands
status.rack.rack-01                    # Rack-01 overall status
status.rack.rack-01.dmm01              # DMM instrument status
```

### Channel Aliasing

Test cases should refer to channels by **logical purpose** rather than physical instrument/channel identifiers. This decouples test logic from specific hardware configuration.

#### Problem

Without aliasing, test cases are tightly coupled to hardware:

```python
# Bad: Test case knows physical mapping
await rack.send_command("psu01", "ch2", SetVoltage(3.3))
voltage = await rack.get_telemetry("dmm01", "ch0")
```

If the rack is rewired or a different PSU is used, the test case must change.

#### Solution: Logical Channel Names

Test cases use logical names that describe purpose:

```python
# Good: Test case uses logical names
await rack.send_command("dut_power", SetVoltage(3.3))
voltage = await rack.get_telemetry("dut_voltage_monitor")
```

#### Mapping in Test Rack YAML

The test rack configuration defines the physical-to-logical mapping:

```yaml
rack:
  id: "rack-01"
  name: "HALT Chamber Rack A"

instruments:
  - id: "psu01"
    type: "bk_precision_9140"
    connection:
      interface: "usb"
      port: "/dev/ttyUSB0"
    channels:
      - id: 0
        alias: "dut_3v3"           # Logical name
        voltage_limit: 3.6
      - id: 1
        alias: "dut_5v"            # Logical name
        voltage_limit: 5.5
      - id: 2
        alias: "dut_power"         # Logical name
        voltage_limit: 13.0

  - id: "dmm01"
    type: "keysight_34461a"
    connection:
      interface: "ethernet"
      address: "192.168.1.100"
    channels:
      - id: 0
        alias: "dut_voltage_monitor"  # Logical name
```

#### Implementation: Rack Provides Publish Topic

To keep instrument code simple, the rack provides each channel with its publish topic at initialization:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         TestRack Initialization                          │
│                                                                         │
│  1. Load YAML config                                                    │
│  2. For each instrument channel:                                        │
│     - Determine publish topic using alias                               │
│     - Pass topic to instrument at initialization                        │
│                                                                         │
│  Instrument receives:                                                   │
│    channel_id: 0                                                        │
│    publish_topic: "telemetry.rack.rack-01.dut_3v3"    ← alias-based    │
│    command_topic: "command.rack.rack-01.dut_3v3"                        │
│                                                                         │
│  Instrument simply publishes to the provided topic.                     │
│  No aliasing logic in instrument code.                                  │
└─────────────────────────────────────────────────────────────────────────┘
```

#### Benefits

| Concern | Solution |
|---------|----------|
| **Instrument code complexity** | None - instrument just publishes to provided topic |
| **Latency** | Zero - no republishing or middleware |
| **Test case portability** | Test cases use logical names, work with any rack |
| **Rewiring flexibility** | Change YAML alias, not test code |

#### NATS Subjects with Aliasing

With aliasing, subjects use logical names:

```
telemetry.rack.rack-01.dut_power          # Was psu01.ch2
telemetry.rack.rack-01.dut_voltage_monitor # Was dmm01.ch0
command.rack.rack-01.dut_power            # Commands to PSU channel
```

#### Fallback: Physical Names

If no alias is defined, the channel uses the physical naming convention:

```yaml
channels:
  - id: 0
    # No alias defined - uses "psu01.ch0"
```

This allows gradual adoption and debugging with physical names when needed.

## Test Case

A **TestCase** is an abstract base class that specific test implementations derive from. Each test case is configured via a YAML file that defines its parameters and target test rack.

### Test Case Class Hierarchy

```
TestCase (ABC)
├── HaltTestCase
│   ├── ThermalCycleTest
│   ├── VibrationTest
│   └── CombinedStressTest
├── HassTestCase
│   ├── BurnInTest
│   └── ProductionScreening
└── FunctionalTestCase
    ├── PowerOnTest
    ├── CalibrationTest
    └── ...
```

### Relationship to Test Rack

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Test Case                                      │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  YAML Configuration                                              │    │
│  │  - rack_id: "rack-01"  ──────────────────────┐                  │    │
│  │  - parameters: { boot_delay: 5.0, ... }      │                  │    │
│  └──────────────────────────────────────────────┼──────────────────┘    │
└─────────────────────────────────────────────────┼────────────────────────┘
                                                  │
                                                  ▼
                              ┌─────────────────────────────────────┐
                              │       TestRack Service (rack-01)    │
                              │  PSU | DMM | Scope | Sensors | ...  │
                              └─────────────────────────────────────┘
```

### YAML Configuration

Each test case has a YAML file specifying:

- Test case identification and metadata
- Reference to the target test rack
- Tunable parameters specific to the DUT and test requirements
- Environmental state definitions and thresholds

#### Example Configuration

```yaml
test_case:
  id: "thermal-cycle-001"
  name: "Thermal Cycle Test"
  description: "Standard thermal cycling test for production units"
  type: "ThermalCycleTest"

rack:
  id: "rack-01"

parameters:
  # Timing parameters (tuned based on DUT characteristics)
  boot_delay_seconds: 5.0
  stabilization_time_seconds: 30.0
  measurement_interval_seconds: 1.0

  # DUT-specific values
  dut_voltage: 3.3
  dut_current_limit: 1.5

  # Test limits
  min_temperature: -40.0
  max_temperature: 85.0
  cycles: 10
  dwell_time_minutes: 15

environmental_states:
  - id: "room"
    name: "Room Temperature"
    description: "Ambient conditions"
    is_transition: false

  - id: "cold_transition"
    name: "Cooling Down"
    is_transition: true

  - id: "cold"
    name: "Cold Soak"
    description: "Minimum temperature dwell"
    is_transition: false

  - id: "hot_transition"
    name: "Heating Up"
    is_transition: true

  - id: "hot"
    name: "Hot Soak"
    description: "Maximum temperature dwell"
    is_transition: false

thresholds:
  room:
    dut_voltage:
      low: 3.2
      high: 3.4
    dut_current:
      high: 0.5

  cold:
    dut_voltage:
      low: 3.1
      high: 3.5
    dut_current:
      high: 0.6

  hot:
    dut_voltage:
      low: 3.0
      high: 3.6
    dut_current:
      high: 0.8
```

### Tunable Parameters

Parameters are values that test engineers adjust as they learn DUT characteristics:

| Parameter Type | Description | Examples |
|---------------|-------------|----------|
| **Timing** | Delays, intervals, durations | `boot_delay_seconds`, `stabilization_time` |
| **Electrical** | Voltage, current settings | `dut_voltage`, `current_limit` |
| **Environmental** | Temperature, vibration levels | `min_temperature`, `max_temperature` |
| **Test limits** | Pass/fail criteria | `max_cycles`, `dwell_time` |

These parameters:
- Are loaded at test case startup
- Can be modified between test runs (edit YAML)
- Are not changed dynamically during a test execution

### Test Case Lifecycle

1. **Load Configuration**: Parse YAML, validate parameters
2. **Connect to Rack**: Establish connection to the referenced TestRack service
3. **Initialize**: Set up monitors, configure thresholds per environmental state
4. **Execute**: Run test logic, control instruments via rack, manage state transitions
5. **Monitor**: Continuously evaluate telemetry against state-dependent thresholds
6. **Complete**: Record results, generate report
7. **Cleanup**: Release rack resources, disconnect

### Test Case Interface

Test case implementations must provide:

```python
class TestCase(ABC):
    """Abstract base class for test cases."""

    @abstractmethod
    async def setup(self) -> None:
        """Initialize test case, connect to rack."""
        ...

    @abstractmethod
    async def execute(self) -> TestResult:
        """Run the test logic."""
        ...

    @abstractmethod
    async def teardown(self) -> None:
        """Cleanup after test completion."""
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """Return current test parameters."""
        ...
```

## Loggers

Loggers are responsible for reading telemetry data and persisting it to storage. They are instantiated during test case initialization and configured via the test case YAML.

### Logger Class Hierarchy

```
Logger (ABC)
├── CsvLogger
│   └── Writes one CSV file per topic
└── InfluxDbLogger
    └── Writes to InfluxDB time-series database
```

### Logger Configuration in Test Case YAML

Loggers are defined in the test case configuration:

```yaml
test_case:
  id: "thermal-cycle-001"
  name: "Thermal Cycle Test"
  type: "HaltTest"

rack:
  id: "rack-01"

loggers:
  - type: "csv"
    output_dir: "/data/logs"

  - type: "influxdb"
    url: "http://localhost:8086"
    org: "hwtest"
    bucket: "telemetry"
    token_env: "INFLUXDB_TOKEN"   # Read token from environment variable

# ... rest of test case config
```

### Topics and Tags

The test case provides loggers with:

1. **Topics**: List of telemetry topics to subscribe to and log
2. **Tags**: Metadata for organizing and querying logged data

#### Tags

| Tag | Description | Example |
|-----|-------------|---------|
| `test_run_id` | Unique identifier for this test run | `"run-2024-01-15-143052"` |
| `test_run_start` | ISO timestamp of test start | `"2024-01-15T14:30:52Z"` |
| `test_case_id` | Test case identifier | `"thermal-cycle-001"` |
| `test_case_name` | Human-readable test name | `"Thermal Cycle Test"` |
| `test_type` | Category of test | `"HALT"`, `"HASS"`, `"functional"` |
| `rack_id` | Test rack identifier | `"rack-01"` |
| `dut_serial` | Device under test serial number | `"SN12345"` |

### CSV Logger

The CSV logger creates one file per topic, organized in folders by tags:

```
{output_dir}/
└── {test_type}/
    └── {test_case_id}/
        └── {test_run_id}/
            ├── dut_power.csv
            ├── dut_voltage_monitor.csv
            ├── chamber_temp.csv
            └── metadata.json       # Tags and test info
```

#### CSV File Format

Each CSV file contains:

```csv
timestamp_ns,voltage_desired,voltage_set,voltage_measured,current_measured
1705329052000000000,3.300,3.300,3.298,0.452
1705329052001000000,3.300,3.300,3.299,0.451
1705329052002000000,3.300,3.300,3.297,0.453
```

- First column is always `timestamp_ns`
- Remaining columns match the stream schema fields
- Header row derived from schema field names

#### metadata.json

```json
{
  "test_run_id": "run-2024-01-15-143052",
  "test_run_start": "2024-01-15T14:30:52Z",
  "test_case_id": "thermal-cycle-001",
  "test_case_name": "Thermal Cycle Test",
  "test_type": "HALT",
  "rack_id": "rack-01",
  "dut_serial": "SN12345",
  "topics": [
    "telemetry.rack.rack-01.dut_power",
    "telemetry.rack.rack-01.dut_voltage_monitor",
    "telemetry.rack.rack-01.chamber_temp"
  ]
}
```

### InfluxDB Logger

The InfluxDB logger writes telemetry data as time-series points with tags:

```
Measurement: telemetry
Tags:
  - topic: "dut_power"
  - test_run_id: "run-2024-01-15-143052"
  - test_case_id: "thermal-cycle-001"
  - test_type: "HALT"
  - rack_id: "rack-01"
  - dut_serial: "SN12345"
Fields:
  - voltage_desired: 3.300
  - voltage_set: 3.300
  - voltage_measured: 3.298
  - current_measured: 0.452
Timestamp: 1705329052000000000 (nanoseconds)
```

This enables queries like:

```flux
from(bucket: "telemetry")
  |> range(start: -1d)
  |> filter(fn: (r) => r.test_type == "HALT")
  |> filter(fn: (r) => r.topic == "dut_power")
  |> filter(fn: (r) => r._field == "voltage_measured")
```

### Logger Interface

```python
class Logger(ABC):
    """Abstract base class for telemetry loggers."""

    @abstractmethod
    async def start(self, topics: list[str], tags: dict[str, str]) -> None:
        """Start logging the specified topics with given tags.

        Args:
            topics: List of NATS subjects to subscribe to and log.
            tags: Metadata tags for organizing logged data.
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop logging and flush any buffered data."""
        ...

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """Return True if the logger is actively logging."""
        ...
```

### Logger Lifecycle

1. **Instantiate**: Test case creates logger from YAML config
2. **Start**: Test case calls `start()` with topics and tags
3. **Run**: Logger subscribes to topics, writes data as it arrives
4. **Stop**: Test case calls `stop()` at test completion
5. **Flush**: Logger ensures all buffered data is written

## Components

```
hwtest_core/
├── __init__.py
├── types/
│   ├── __init__.py
│   ├── telemetry.py      # Telemetry message types
│   ├── state.py          # Environmental state types
│   ├── threshold.py      # Threshold/limit definitions
│   └── common.py         # Shared types (timestamps, identifiers)
├── interfaces/
│   ├── __init__.py
│   ├── publisher.py      # Telemetry publishing interface
│   ├── subscriber.py     # Telemetry subscription interface
│   ├── monitor.py        # Monitor interface
│   └── state_manager.py  # Environmental state management interface
└── errors.py             # Custom exception types
```

## Data Types

### Common Types (`types/common.py`)

#### Timestamp

```python
@dataclass(frozen=True)
class Timestamp:
    """High-resolution timestamp with source tracking."""
    unix_ns: int                    # Nanoseconds since Unix epoch
    source: str                     # Identifier of the clock source

    @classmethod
    def now(cls, source: str = "local") -> "Timestamp": ...

    def to_datetime(self) -> datetime: ...
```

#### Identifier Types

```python
# Type aliases for clarity
SourceId = NewType("SourceId", str)      # Identifies a telemetry source (instrument, UUT)
ChannelId = NewType("ChannelId", str)    # Identifies a measurement channel
StateId = NewType("StateId", str)        # Identifies an environmental state
MonitorId = NewType("MonitorId", str)    # Identifies a monitor instance
```

### Telemetry Types (`types/telemetry.py`)

#### TelemetryValue

```python
@dataclass(frozen=True)
class TelemetryValue:
    """A single measurement value with metadata."""
    channel: ChannelId              # Which channel this measurement is from
    value: float                    # The measured value
    unit: str                       # Unit of measurement (e.g., "V", "A", "°C")
    source_timestamp: Timestamp     # When measurement was taken
    publish_timestamp: Timestamp | None = None  # When message was published
    quality: ValueQuality = ValueQuality.GOOD   # Data quality indicator
```

#### ValueQuality

```python
class ValueQuality(Enum):
    """Quality indicator for telemetry values."""
    GOOD = "good"                   # Normal, valid measurement
    UNCERTAIN = "uncertain"         # Measurement may be unreliable
    BAD = "bad"                     # Known-bad or missing data
    STALE = "stale"                 # Data older than expected update rate
```

#### TelemetryMessage

```python
@dataclass(frozen=True)
class TelemetryMessage:
    """A batch of telemetry values from a single source."""
    source: SourceId                # Which source produced this message
    values: tuple[TelemetryValue, ...]  # One or more values
    sequence: int                   # Sequence number for ordering/gap detection

    def to_bytes(self) -> bytes: ...

    @classmethod
    def from_bytes(cls, data: bytes) -> "TelemetryMessage": ...
```

### Environmental State Types (`types/state.py`)

#### EnvironmentalState

```python
@dataclass(frozen=True)
class EnvironmentalState:
    """Represents a discrete environmental condition."""
    state_id: StateId               # Unique identifier
    name: str                       # Human-readable name
    description: str                # Detailed description
    is_transition: bool = False     # If True, measurements are ignored
    metadata: Mapping[str, Any] = field(default_factory=dict)
```

#### StateTransition

```python
@dataclass(frozen=True)
class StateTransition:
    """Records a change in environmental state."""
    from_state: StateId | None      # Previous state (None if initial)
    to_state: StateId               # New state
    timestamp: Timestamp            # When transition occurred
    reason: str = ""                # Why the transition happened
```

### Threshold Types (`types/threshold.py`)

#### ThresholdBound

```python
class BoundType(Enum):
    """Type of threshold boundary."""
    INCLUSIVE = "inclusive"         # Value at boundary is acceptable
    EXCLUSIVE = "exclusive"         # Value at boundary is a violation

@dataclass(frozen=True)
class ThresholdBound:
    """A single boundary for a threshold."""
    value: float
    bound_type: BoundType = BoundType.INCLUSIVE
```

#### Threshold

```python
@dataclass(frozen=True)
class Threshold:
    """Defines acceptable range for a measurement."""
    channel: ChannelId              # Which channel this applies to
    low: ThresholdBound | None      # Lower limit (None = no lower limit)
    high: ThresholdBound | None     # Upper limit (None = no upper limit)

    def check(self, value: float) -> bool:
        """Returns True if value is within threshold."""
        ...
```

#### StateThresholds

```python
@dataclass(frozen=True)
class StateThresholds:
    """Collection of thresholds for a specific environmental state."""
    state_id: StateId
    thresholds: Mapping[ChannelId, Threshold]

    def get_threshold(self, channel: ChannelId) -> Threshold | None: ...
```

### Monitor Result Types

#### MonitorVerdict

```python
class MonitorVerdict(Enum):
    """Result of a monitor evaluation."""
    PASS = "pass"                   # All values within thresholds
    FAIL = "fail"                   # One or more values out of threshold
    SKIP = "skip"                   # Evaluation skipped (e.g., transition state)
    ERROR = "error"                 # Monitor encountered an error
```

#### MonitorResult

```python
@dataclass(frozen=True)
class MonitorResult:
    """Result of a single monitor evaluation."""
    monitor_id: MonitorId
    verdict: MonitorVerdict
    timestamp: Timestamp
    state_id: StateId               # Environmental state at time of evaluation
    violations: tuple[ThresholdViolation, ...] = ()
    message: str = ""

@dataclass(frozen=True)
class ThresholdViolation:
    """Details of a threshold violation."""
    channel: ChannelId
    value: float
    threshold: Threshold
    message: str = ""
```

## Interfaces

### Publisher Interface (`interfaces/publisher.py`)

```python
class TelemetryPublisher(Protocol):
    """Interface for publishing telemetry data."""

    async def publish(self, message: TelemetryMessage) -> None:
        """Publish a telemetry message."""
        ...

    async def connect(self) -> None:
        """Establish connection to the telemetry server."""
        ...

    async def disconnect(self) -> None:
        """Disconnect from the telemetry server."""
        ...

    @property
    def is_connected(self) -> bool:
        """Returns True if connected to the server."""
        ...
```

### Subscriber Interface (`interfaces/subscriber.py`)

```python
class TelemetrySubscriber(Protocol):
    """Interface for subscribing to telemetry data."""

    async def subscribe(
        self,
        sources: Iterable[SourceId] | None = None,
        channels: Iterable[ChannelId] | None = None,
    ) -> None:
        """Subscribe to telemetry. None means all."""
        ...

    async def unsubscribe(self) -> None:
        """Unsubscribe from telemetry."""
        ...

    async def receive(self) -> TelemetryMessage:
        """Receive the next telemetry message. Blocks until available."""
        ...

    def messages(self) -> AsyncIterator[TelemetryMessage]:
        """Async iterator over incoming messages."""
        ...

    async def connect(self) -> None:
        """Establish connection to the telemetry server."""
        ...

    async def disconnect(self) -> None:
        """Disconnect from the telemetry server."""
        ...
```

### State Manager Interface (`interfaces/state_manager.py`)

```python
class StatePublisher(Protocol):
    """Interface for publishing environmental state changes."""

    async def set_state(self, state: EnvironmentalState, reason: str = "") -> None:
        """Transition to a new environmental state."""
        ...

    async def get_current_state(self) -> EnvironmentalState:
        """Get the current environmental state."""
        ...

class StateSubscriber(Protocol):
    """Interface for receiving environmental state changes."""

    async def subscribe(self) -> None:
        """Subscribe to state changes."""
        ...

    async def get_current_state(self) -> EnvironmentalState:
        """Get the current environmental state."""
        ...

    def transitions(self) -> AsyncIterator[StateTransition]:
        """Async iterator over state transitions."""
        ...
```

### Monitor Interface (`interfaces/monitor.py`)

```python
class Monitor(Protocol):
    """Interface for a telemetry monitor."""

    @property
    def monitor_id(self) -> MonitorId:
        """Unique identifier for this monitor."""
        ...

    async def evaluate(
        self,
        values: Iterable[TelemetryValue],
        state: EnvironmentalState,
        thresholds: StateThresholds,
    ) -> MonitorResult:
        """Evaluate telemetry values against thresholds for the given state."""
        ...

    async def start(self) -> None:
        """Start the monitor (begin continuous evaluation)."""
        ...

    async def stop(self) -> None:
        """Stop the monitor."""
        ...
```

### Threshold Provider Interface

```python
class ThresholdProvider(Protocol):
    """Interface for retrieving thresholds by state."""

    def get_thresholds(self, state_id: StateId) -> StateThresholds | None:
        """Get thresholds for a given environmental state."""
        ...

    def get_all_states(self) -> Iterable[StateId]:
        """Get all state IDs that have defined thresholds."""
        ...
```

## Error Types (`errors.py`)

```python
class HwtestError(Exception):
    """Base exception for all hwtest errors."""
    pass

class ConnectionError(HwtestError):
    """Failed to connect to telemetry server."""
    pass

class SerializationError(HwtestError):
    """Failed to serialize/deserialize a message."""
    pass

class StateError(HwtestError):
    """Invalid state or state transition."""
    pass

class ThresholdError(HwtestError):
    """Invalid threshold definition."""
    pass
```

## Dependencies

- Python standard library only (preferred)
- `dataclasses` - included in stdlib
- `typing` - included in stdlib
- `enum` - included in stdlib

### Potential Optional Dependencies

These may be considered if justified:

| Dependency | Purpose | Justification Required |
|------------|---------|------------------------|
| `msgpack` | Fast binary serialization | If JSON performance is insufficient |
| `pydantic` | Validation and serialization | If dataclasses validation is insufficient |

Any external dependency must be documented with clear justification.

## Serialization

All message types must support serialization for transport over NATS. The default implementation uses JSON for simplicity and debuggability. Binary formats (msgpack) may be added if latency requirements demand it.

```python
# All message dataclasses implement:
def to_bytes(self) -> bytes: ...
def to_dict(self) -> dict[str, Any]: ...

@classmethod
def from_bytes(cls, data: bytes) -> Self: ...

@classmethod
def from_dict(cls, data: dict[str, Any]) -> Self: ...
```

## Streaming Data Protocol

A binary protocol for efficient streaming of time-series measurement data. Designed for simplicity on embedded producers and flexibility for consumers.

### Design Principles

- **Network byte order**: All multi-byte values are big-endian
- **Schema-first**: Schema message must be received before data can be interpreted
- **Same channel**: Schema and data messages share one NATS subject per source (no race conditions)
- **Fixed schema per session**: Data format does not change during producer lifetime; restart may change schema
- **Periodic schema retransmission**: Schema retransmitted every 1 second for late-joining consumers

### String Encoding

All strings are length-prefixed:

```
┌──────────────────────────────┐
│ length: u8                   │  (max 255 bytes)
│ data: u8[length]             │  (UTF-8 encoded)
└──────────────────────────────┘
```

### Type Codes

| Code | Type | Size (bytes) | Description |
|------|------|--------------|-------------|
| 0x01 | i8   | 1 | Signed 8-bit integer |
| 0x02 | i16  | 2 | Signed 16-bit integer |
| 0x03 | i32  | 4 | Signed 32-bit integer |
| 0x04 | i64  | 8 | Signed 64-bit integer |
| 0x05 | u8   | 1 | Unsigned 8-bit integer |
| 0x06 | u16  | 2 | Unsigned 16-bit integer |
| 0x07 | u32  | 4 | Unsigned 32-bit integer |
| 0x08 | u64  | 8 | Unsigned 64-bit integer |
| 0x09 | f32  | 4 | IEEE 754 single-precision float |
| 0x0A | f64  | 8 | IEEE 754 double-precision float |

### Message Types

#### Schema Message (0x01)

Defines the structure of subsequent data messages. Must be transmitted:
- At producer startup
- Every 1 second thereafter
- On the same NATS subject as data messages

```
┌─────────────────────────────────────────────────────────────┐
│ SCHEMA MESSAGE                                              │
├─────────────────────────────────────────────────────────────┤
│ msg_type: u8 = 0x01                                         │
│ schema_id: u32           (CRC32 of field definitions)       │
│ source_id: string        (length-prefixed, producer ID)     │
│ field_count: u16                                            │
│ fields[field_count]:                                        │
│   ├─ name: string        (length-prefixed, field name)      │
│   ├─ dtype: u8           (type code from table above)       │
│   └─ unit: string        (length-prefixed, e.g., "V", "mA") │
└─────────────────────────────────────────────────────────────┘
```

**schema_id calculation**: CRC32 computed over the concatenation of all field definitions (name + dtype + unit) in order. This allows consumers to detect schema changes without comparing full field lists.

#### Data Message (0x02)

Contains one or more sample vectors. Each vector contains one value per field defined in the schema.

```
┌─────────────────────────────────────────────────────────────┐
│ DATA MESSAGE                                                │
├─────────────────────────────────────────────────────────────┤
│ msg_type: u8 = 0x02                                         │
│ schema_id: u32           (must match received schema)       │
│ timestamp_ns: u64        (tick count of first sample, ns)   │
│ period_ns: u64           (time delta between samples, ns)   │
│ sample_count: u16        (number of sample vectors)         │
│ samples[sample_count]:                                      │
│   └─ values[field_count]: (packed binary per schema types)  │
└─────────────────────────────────────────────────────────────┘
```

**Timestamp model**:
- `timestamp_ns`: Absolute timestamp of the first sample in nanoseconds
- `period_ns`: Fixed interval between consecutive samples
- Sample N has timestamp: `timestamp_ns + (N * period_ns)`

**Values packing**: Each sample vector contains values in schema field order, packed with no padding. Consumer uses schema to determine offsets and sizes.

### Wire Format Example

**Schema** for a 3-channel voltage monitor:

| Field | Name | Type | Unit |
|-------|------|------|------|
| 0 | "ch0_voltage" | f32 | "V" |
| 1 | "ch1_voltage" | f32 | "V" |
| 2 | "ch2_voltage" | f32 | "V" |

**Data message** with 2 samples, 1ms apart:

```
msg_type:     0x02
schema_id:    0x1A2B3C4D
timestamp_ns: 1704067200000000000  (first sample)
period_ns:    1000000              (1ms = 1,000,000ns)
sample_count: 2
samples[0]:   [3.30, 5.02, 12.1]   (12 bytes: 3x f32)
samples[1]:   [3.29, 5.01, 12.0]   (12 bytes: 3x f32)
```

Total data payload: 1 + 4 + 8 + 8 + 2 + 24 = 47 bytes for 6 measurements.

### Consumer Behavior

1. Subscribe to source's NATS subject
2. Wait for schema message (msg_type = 0x01)
3. Parse schema, build field index (name → offset mapping)
4. Process data messages:
   - Validate schema_id matches
   - Extract only needed fields by calculated offset
   - Compute per-sample timestamps from base + (index × period)
5. Handle schema_id mismatch:
   - Discard data messages until new schema received
   - Log warning (producer may have restarted)

### Producer Behavior

1. Define schema at startup (fields, types, units)
2. Compute schema_id (CRC32)
3. Publish schema message
4. Start periodic timer (1 second) to republish schema
5. Publish data messages as samples are acquired
6. Schema must not change during producer lifetime

### Python Types

```python
@dataclass(frozen=True)
class StreamField:
    """Definition of a single field in a stream schema."""
    name: str
    dtype: DataType              # Enum of type codes
    unit: str = ""

@dataclass(frozen=True)
class StreamSchema:
    """Schema defining the structure of a data stream."""
    source_id: SourceId
    fields: tuple[StreamField, ...]
    schema_id: int = field(init=False)  # Computed CRC32

    def __post_init__(self) -> None:
        object.__setattr__(self, 'schema_id', self._compute_crc32())

    def _compute_crc32(self) -> int: ...
    def to_bytes(self) -> bytes: ...

    @classmethod
    def from_bytes(cls, data: bytes) -> "StreamSchema": ...

@dataclass(frozen=True)
class StreamData:
    """A batch of time-series samples."""
    schema_id: int
    timestamp_ns: int            # First sample timestamp
    period_ns: int               # Interval between samples
    samples: tuple[tuple[int | float, ...], ...]  # sample_count × field_count

    def get_timestamp(self, sample_index: int) -> int:
        """Get timestamp for sample at given index."""
        return self.timestamp_ns + (sample_index * self.period_ns)

    def to_bytes(self, schema: StreamSchema) -> bytes: ...

    @classmethod
    def from_bytes(cls, data: bytes, schema: StreamSchema) -> "StreamData": ...
```

### Interfaces

```python
class StreamPublisher(Protocol):
    """Interface for publishing streaming data."""

    @property
    def schema(self) -> StreamSchema:
        """The schema for this stream."""
        ...

    async def publish(self, data: StreamData) -> None:
        """Publish a data message."""
        ...

    async def start(self) -> None:
        """Start the publisher (connects and begins schema broadcast)."""
        ...

    async def stop(self) -> None:
        """Stop the publisher."""
        ...


class StreamSubscriber(Protocol):
    """Interface for subscribing to streaming data."""

    async def subscribe(self, source_id: SourceId) -> None:
        """Subscribe to a stream source."""
        ...

    async def get_schema(self) -> StreamSchema:
        """Get the schema (waits for schema message if needed)."""
        ...

    def data(self) -> AsyncIterator[StreamData]:
        """Async iterator over data messages."""
        ...

    async def unsubscribe(self) -> None:
        """Unsubscribe from the stream."""
        ...
```
