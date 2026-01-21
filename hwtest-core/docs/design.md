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
