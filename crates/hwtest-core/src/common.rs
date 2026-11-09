//! Foundational types used throughout hwtest.

use std::time::{SystemTime, UNIX_EPOCH};

/// Identifies a data source (an instrument or sensor).
pub type SourceId = String;
/// Identifies a measurement channel within a source.
pub type ChannelId = String;
/// Identifies an environmental state.
pub type StateId = String;
/// Identifies a telemetry monitor.
pub type MonitorId = String;

/// Default timestamp source label.
pub const LOCAL_SOURCE: &str = "local";

/// High-resolution timestamp: nanoseconds since the Unix epoch plus a label
/// describing where the time came from (`"local"`, `"ntp"`, `"ptp"`, ...).
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Timestamp {
    /// Nanoseconds since 1970-01-01T00:00:00Z.
    pub unix_ns: i64,
    /// Origin of the timestamp.
    pub source: String,
}

impl Timestamp {
    /// Creates a timestamp.
    pub fn new(unix_ns: i64, source: impl Into<String>) -> Self {
        Self { unix_ns, source: source.into() }
    }

    /// Creates a timestamp with the default `"local"` source.
    pub fn local(unix_ns: i64) -> Self {
        Self::new(unix_ns, LOCAL_SOURCE)
    }

    /// Returns the current wall-clock time.
    pub fn now(source: impl Into<String>) -> Self {
        let unix_ns = match SystemTime::now().duration_since(UNIX_EPOCH) {
            Ok(d) => i64::try_from(d.as_nanos()).unwrap_or(i64::MAX),
            Err(e) => -i64::try_from(e.duration().as_nanos()).unwrap_or(i64::MAX),
        };
        Self::new(unix_ns, source)
    }

    /// Seconds since the epoch as a float.
    #[allow(clippy::cast_precision_loss)]
    pub fn unix_seconds(&self) -> f64 {
        self.unix_ns as f64 / 1e9
    }

    /// Milliseconds since the epoch (floored, matching Python `//`).
    pub fn unix_ms(&self) -> i64 {
        self.unix_ns.div_euclid(1_000_000)
    }

    /// Microseconds since the epoch (floored, matching Python `//`).
    pub fn unix_us(&self) -> i64 {
        self.unix_ns.div_euclid(1_000)
    }
}

/// Data type codes for the binary streaming protocol.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum DataType {
    /// 8-bit signed integer.
    I8 = 0x01,
    /// 16-bit signed integer.
    I16 = 0x02,
    /// 32-bit signed integer.
    I32 = 0x03,
    /// 64-bit signed integer.
    I64 = 0x04,
    /// 8-bit unsigned integer.
    U8 = 0x05,
    /// 16-bit unsigned integer.
    U16 = 0x06,
    /// 32-bit unsigned integer.
    U32 = 0x07,
    /// 64-bit unsigned integer.
    U64 = 0x08,
    /// 32-bit IEEE 754 float.
    F32 = 0x09,
    /// 64-bit IEEE 754 float.
    F64 = 0x0A,
}

impl DataType {
    /// All data types in code order.
    pub const ALL: [DataType; 10] = [
        DataType::I8,
        DataType::I16,
        DataType::I32,
        DataType::I64,
        DataType::U8,
        DataType::U16,
        DataType::U32,
        DataType::U64,
        DataType::F32,
        DataType::F64,
    ];

    /// The wire code for this type.
    pub fn code(self) -> u8 {
        self as u8
    }

    /// Looks up a type by wire code.
    pub fn from_code(code: u8) -> Option<Self> {
        Self::ALL.into_iter().find(|t| t.code() == code)
    }

    /// Size in bytes of one value.
    pub fn size(self) -> usize {
        match self {
            DataType::I8 | DataType::U8 => 1,
            DataType::I16 | DataType::U16 => 2,
            DataType::I32 | DataType::U32 | DataType::F32 => 4,
            DataType::I64 | DataType::U64 | DataType::F64 => 8,
        }
    }

    /// Python `struct` format character (big-endian when prefixed with `!`).
    pub fn struct_format(self) -> char {
        match self {
            DataType::I8 => 'b',
            DataType::I16 => 'h',
            DataType::I32 => 'i',
            DataType::I64 => 'q',
            DataType::U8 => 'B',
            DataType::U16 => 'H',
            DataType::U32 => 'I',
            DataType::U64 => 'Q',
            DataType::F32 => 'f',
            DataType::F64 => 'd',
        }
    }

    /// True for `I8`..`I64`.
    pub fn is_signed(self) -> bool {
        matches!(self, DataType::I8 | DataType::I16 | DataType::I32 | DataType::I64)
    }

    /// True for `U8`..`U64`.
    pub fn is_unsigned(self) -> bool {
        matches!(self, DataType::U8 | DataType::U16 | DataType::U32 | DataType::U64)
    }

    /// True for `F32` and `F64`.
    pub fn is_float(self) -> bool {
        matches!(self, DataType::F32 | DataType::F64)
    }
}

/// Instrument identification, modelled on the four SCPI `*IDN?` fields.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct InstrumentIdentity {
    /// Manufacturer name, e.g. `"BK Precision"`.
    pub manufacturer: String,
    /// Model, e.g. `"9115"`.
    pub model: String,
    /// Serial number.
    pub serial: String,
    /// Firmware / hardware version.
    pub firmware: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn data_type_codes_round_trip() {
        for t in DataType::ALL {
            assert_eq!(DataType::from_code(t.code()), Some(t));
        }
        assert_eq!(DataType::from_code(0), None);
        assert_eq!(DataType::from_code(0x0B), None);
        assert_eq!(DataType::F64.code(), 0x0A);
    }

    #[test]
    fn data_type_properties() {
        assert_eq!(DataType::I64.size(), 8);
        assert_eq!(DataType::U16.size(), 2);
        assert_eq!(DataType::F32.struct_format(), 'f');
        assert!(DataType::I8.is_signed() && !DataType::I8.is_unsigned());
        assert!(DataType::U64.is_unsigned() && !DataType::U64.is_float());
        assert!(DataType::F64.is_float() && !DataType::F64.is_signed());
    }

    #[test]
    fn timestamp_conversions_floor_like_python() {
        let ts = Timestamp::local(1_705_320_000_500_123_000);
        assert_eq!(ts.unix_ms(), 1_705_320_000_500);
        assert_eq!(ts.unix_us(), 1_705_320_000_500_123);
        assert!((ts.unix_seconds() - 1_705_320_000.500_123).abs() < 1e-6);
        // Python: -1 // 1000 == -1
        assert_eq!(Timestamp::local(-1).unix_us(), -1);
    }

    #[test]
    fn timestamp_now_is_recent() {
        let ts = Timestamp::now("ptp");
        assert!(ts.unix_ns > 1_700_000_000_000_000_000);
        assert_eq!(ts.source, "ptp");
    }
}
