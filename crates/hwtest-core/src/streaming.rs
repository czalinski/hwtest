//! Binary streaming protocol for high-rate telemetry.
//!
//! All multi-byte values are big-endian. Strings are a `u8` length followed by
//! UTF-8 bytes (max 255 bytes).
//!
//! Schema frame (`0x01`), retransmitted periodically:
//! ```text
//! u8 msg_type | u32 schema_id | str source_id | u16 field_count | field*
//! field := str name | u8 dtype | str unit
//! ```
//!
//! Data frame (`0x02`):
//! ```text
//! u8 msg_type | u32 schema_id | u64 timestamp_ns | u64 period_ns | u16 sample_count
//! | sample_count * (one packed value per schema field)
//! ```
//!
//! The schema ID is the CRC32 of the concatenated `name || dtype || unit`
//! bytes of every field (the source ID is deliberately excluded).

use crate::common::{DataType, SourceId};
use crate::error::{Error, Result};

/// Message type byte of a schema frame.
pub const MSG_TYPE_SCHEMA: u8 = 0x01;
/// Message type byte of a data frame.
pub const MSG_TYPE_DATA: u8 = 0x02;

/// Size of a data frame header in bytes.
pub const DATA_HEADER_SIZE: usize = 1 + 4 + 8 + 8 + 2;

fn protocol(msg: impl Into<String>) -> Error {
    Error::Protocol(msg.into())
}

/// Appends a length-prefixed UTF-8 string.
///
/// # Errors
/// Returns [`Error::Protocol`] if the string is longer than 255 bytes.
pub fn encode_string(out: &mut Vec<u8>, s: &str) -> Result<()> {
    let len = u8::try_from(s.len()).map_err(|_| {
        protocol(format!("String too long for encoding: {} bytes (max 255)", s.len()))
    })?;
    out.push(len);
    out.extend_from_slice(s.as_bytes());
    Ok(())
}

/// Bounds-checked reader over a byte slice.
struct Reader<'a> {
    data: &'a [u8],
    offset: usize,
}

impl<'a> Reader<'a> {
    fn new(data: &'a [u8], offset: usize) -> Self {
        Self { data, offset }
    }

    fn take(&mut self, n: usize) -> Result<&'a [u8]> {
        let end =
            self.offset.checked_add(n).filter(|&e| e <= self.data.len()).ok_or_else(|| {
                protocol(format!(
                    "Truncated message: need {n} bytes at offset {}, have {}",
                    self.offset,
                    self.data.len().saturating_sub(self.offset)
                ))
            })?;
        let bytes = &self.data[self.offset..end];
        self.offset = end;
        Ok(bytes)
    }

    fn array<const N: usize>(&mut self) -> Result<[u8; N]> {
        let mut buf = [0u8; N];
        buf.copy_from_slice(self.take(N)?);
        Ok(buf)
    }

    fn u8(&mut self) -> Result<u8> {
        Ok(self.take(1)?[0])
    }

    fn u16(&mut self) -> Result<u16> {
        Ok(u16::from_be_bytes(self.array()?))
    }

    fn u32(&mut self) -> Result<u32> {
        Ok(u32::from_be_bytes(self.array()?))
    }

    fn u64(&mut self) -> Result<u64> {
        Ok(u64::from_be_bytes(self.array()?))
    }

    fn string(&mut self) -> Result<String> {
        let len = usize::from(self.u8()?);
        let bytes = self.take(len)?;
        String::from_utf8(bytes.to_vec())
            .map_err(|e| protocol(format!("Invalid UTF-8 in string: {e}")))
    }
}

/// Decodes a length-prefixed string at `offset`, returning it and the offset
/// just past it.
///
/// # Errors
/// Returns [`Error::Protocol`] on truncation or invalid UTF-8.
pub fn decode_string(data: &[u8], offset: usize) -> Result<(String, usize)> {
    let mut r = Reader::new(data, offset);
    let s = r.string()?;
    Ok((s, r.offset))
}

/// One field (column) of a stream schema.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct StreamField {
    /// Field name, e.g. `"voltage"`.
    pub name: String,
    /// Binary type of each value.
    pub dtype: DataType,
    /// Unit, e.g. `"V"`; may be empty.
    pub unit: String,
}

impl StreamField {
    /// Creates a field.
    pub fn new(name: impl Into<String>, dtype: DataType, unit: impl Into<String>) -> Self {
        Self { name: name.into(), dtype, unit: unit.into() }
    }

    /// Appends the wire encoding of this field.
    ///
    /// # Errors
    /// Returns [`Error::Protocol`] if the name or unit exceeds 255 bytes.
    pub fn encode_into(&self, out: &mut Vec<u8>) -> Result<()> {
        encode_string(out, &self.name)?;
        out.push(self.dtype.code());
        encode_string(out, &self.unit)
    }

    /// Returns the wire encoding of this field.
    ///
    /// # Errors
    /// Returns [`Error::Protocol`] if the name or unit exceeds 255 bytes.
    pub fn to_bytes(&self) -> Result<Vec<u8>> {
        let mut out = Vec::with_capacity(self.name.len() + self.unit.len() + 3);
        self.encode_into(&mut out)?;
        Ok(out)
    }

    /// Decodes a field at `offset`, returning it and the offset just past it.
    ///
    /// # Errors
    /// Returns [`Error::Protocol`] on truncation, bad UTF-8 or unknown dtype.
    pub fn from_bytes(data: &[u8], offset: usize) -> Result<(Self, usize)> {
        let mut r = Reader::new(data, offset);
        let field = Self::read(&mut r)?;
        Ok((field, r.offset))
    }

    fn read(r: &mut Reader<'_>) -> Result<Self> {
        let name = r.string()?;
        let code = r.u8()?;
        let dtype = DataType::from_code(code)
            .ok_or_else(|| protocol(format!("Unknown data type code: {code:#04x}")))?;
        let unit = r.string()?;
        Ok(Self { name, dtype, unit })
    }

    fn crc_update(&self, hasher: &mut crc32fast::Hasher) {
        hasher.update(self.name.as_bytes());
        hasher.update(&[self.dtype.code()]);
        hasher.update(self.unit.as_bytes());
    }
}

/// Describes the layout of samples in a stream. Immutable; the schema ID is
/// computed on construction.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct StreamSchema {
    source_id: SourceId,
    fields: Vec<StreamField>,
    schema_id: u32,
    sample_size: usize,
}

impl StreamSchema {
    /// Creates a schema and computes its CRC32 schema ID.
    pub fn new(source_id: impl Into<SourceId>, fields: Vec<StreamField>) -> Self {
        let mut hasher = crc32fast::Hasher::new();
        for f in &fields {
            f.crc_update(&mut hasher);
        }
        let sample_size = fields.iter().map(|f| f.dtype.size()).sum();
        Self { source_id: source_id.into(), fields, schema_id: hasher.finalize(), sample_size }
    }

    /// The source that publishes this stream.
    pub fn source_id(&self) -> &str {
        &self.source_id
    }

    /// Fields in sample order.
    pub fn fields(&self) -> &[StreamField] {
        &self.fields
    }

    /// CRC32 of the field definitions.
    pub fn schema_id(&self) -> u32 {
        self.schema_id
    }

    /// Bytes per packed sample.
    pub fn sample_size(&self) -> usize {
        self.sample_size
    }

    /// Looks up a field by name.
    pub fn field(&self, name: &str) -> Option<&StreamField> {
        self.fields.iter().find(|f| f.name == name)
    }

    /// Index of a field by name.
    pub fn field_index(&self, name: &str) -> Option<usize> {
        self.fields.iter().position(|f| f.name == name)
    }

    /// Byte offset of a field within a packed sample.
    pub fn field_offset(&self, name: &str) -> Option<usize> {
        let idx = self.field_index(name)?;
        Some(self.fields[..idx].iter().map(|f| f.dtype.size()).sum())
    }

    /// Encodes a schema frame.
    ///
    /// # Errors
    /// Returns [`Error::Protocol`] if a string exceeds 255 bytes or there are
    /// more than 65535 fields.
    pub fn to_bytes(&self) -> Result<Vec<u8>> {
        let count = u16::try_from(self.fields.len())
            .map_err(|_| protocol(format!("Too many fields: {} (max 65535)", self.fields.len())))?;
        let mut out = Vec::with_capacity(8 + self.source_id.len() + self.fields.len() * 16);
        out.push(MSG_TYPE_SCHEMA);
        out.extend_from_slice(&self.schema_id.to_be_bytes());
        encode_string(&mut out, &self.source_id)?;
        out.extend_from_slice(&count.to_be_bytes());
        for f in &self.fields {
            f.encode_into(&mut out)?;
        }
        Ok(out)
    }

    /// Decodes a schema frame and verifies its schema ID.
    ///
    /// # Errors
    /// Returns [`Error::Protocol`] for a wrong message type, truncation, bad
    /// field data, or a schema ID that does not match the fields.
    pub fn from_bytes(data: &[u8]) -> Result<Self> {
        let mut r = Reader::new(data, 0);
        let msg_type = r.u8()?;
        if msg_type != MSG_TYPE_SCHEMA {
            return Err(protocol(format!(
                "Invalid message type: expected {MSG_TYPE_SCHEMA}, got {msg_type}"
            )));
        }
        let expected = r.u32()?;
        let source_id = r.string()?;
        let count = r.u16()?;
        let fields = (0..count).map(|_| StreamField::read(&mut r)).collect::<Result<Vec<_>>>()?;
        let schema = Self::new(source_id, fields);
        if schema.schema_id != expected {
            return Err(protocol(format!(
                "Schema ID mismatch: computed {:#x}, expected {expected:#x}",
                schema.schema_id
            )));
        }
        Ok(schema)
    }
}

/// One value in a sample, before it is packed into a schema [`DataType`].
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum SampleValue {
    /// Signed integer.
    Int(i64),
    /// Unsigned integer too large for `i64`.
    UInt(u64),
    /// Floating point.
    Float(f64),
}

impl From<f64> for SampleValue {
    fn from(v: f64) -> Self {
        SampleValue::Float(v)
    }
}

impl From<i64> for SampleValue {
    fn from(v: i64) -> Self {
        SampleValue::Int(v)
    }
}

impl From<u64> for SampleValue {
    fn from(v: u64) -> Self {
        i64::try_from(v).map_or(SampleValue::UInt(v), SampleValue::Int)
    }
}

impl SampleValue {
    /// The value as `f64` (lossy for very large integers).
    #[allow(clippy::cast_precision_loss)]
    pub fn as_f64(self) -> f64 {
        match self {
            SampleValue::Int(v) => v as f64,
            SampleValue::UInt(v) => v as f64,
            SampleValue::Float(v) => v,
        }
    }

    fn as_i128(self, field: &StreamField) -> Result<i128> {
        match self {
            SampleValue::Int(v) => Ok(i128::from(v)),
            SampleValue::UInt(v) => Ok(i128::from(v)),
            SampleValue::Float(v) => Err(protocol(format!(
                "Field '{}' ({:?}) requires an integer, got {v}",
                field.name, field.dtype
            ))),
        }
    }

    #[allow(clippy::cast_possible_truncation)]
    fn pack(self, field: &StreamField, out: &mut Vec<u8>) -> Result<()> {
        macro_rules! int {
            ($t:ty) => {{
                let v = self.as_i128(field)?;
                let v = <$t>::try_from(v).map_err(|_| {
                    protocol(format!(
                        "Value {v} out of range for field '{}' ({:?})",
                        field.name, field.dtype
                    ))
                })?;
                out.extend_from_slice(&v.to_be_bytes());
            }};
        }
        match field.dtype {
            DataType::I8 => int!(i8),
            DataType::I16 => int!(i16),
            DataType::I32 => int!(i32),
            DataType::I64 => int!(i64),
            DataType::U8 => int!(u8),
            DataType::U16 => int!(u16),
            DataType::U32 => int!(u32),
            DataType::U64 => int!(u64),
            DataType::F32 => out.extend_from_slice(&(self.as_f64() as f32).to_be_bytes()),
            DataType::F64 => out.extend_from_slice(&self.as_f64().to_be_bytes()),
        }
        Ok(())
    }

    fn unpack(dtype: DataType, b: &[u8]) -> SampleValue {
        // `b` is exactly `dtype.size()` bytes (guaranteed by the caller).
        macro_rules! be {
            ($t:ty) => {
                <$t>::from_be_bytes(b.try_into().expect("slice length matches dtype size"))
            };
        }
        match dtype {
            DataType::I8 => SampleValue::Int(i64::from(be!(i8))),
            DataType::I16 => SampleValue::Int(i64::from(be!(i16))),
            DataType::I32 => SampleValue::Int(i64::from(be!(i32))),
            DataType::I64 => SampleValue::Int(be!(i64)),
            DataType::U8 => SampleValue::Int(i64::from(be!(u8))),
            DataType::U16 => SampleValue::Int(i64::from(be!(u16))),
            DataType::U32 => SampleValue::Int(i64::from(be!(u32))),
            DataType::U64 => SampleValue::from(be!(u64)),
            DataType::F32 => SampleValue::Float(f64::from(be!(f32))),
            DataType::F64 => SampleValue::Float(be!(f64)),
        }
    }
}

/// A batch of evenly spaced samples. Sample `i` was taken at
/// `timestamp_ns + i * period_ns`.
#[derive(Debug, Clone, PartialEq)]
pub struct StreamData {
    /// Schema this batch conforms to.
    pub schema_id: u32,
    /// Timestamp of the first sample, ns since the epoch.
    pub timestamp_ns: u64,
    /// Sample period in ns.
    pub period_ns: u64,
    /// Samples; each has one value per schema field.
    pub samples: Vec<Vec<SampleValue>>,
}

impl StreamData {
    /// Number of samples in the batch.
    pub fn sample_count(&self) -> usize {
        self.samples.len()
    }

    /// Timestamp of sample `index` (wrapping, as on the wire).
    pub fn timestamp(&self, index: u64) -> u64 {
        self.timestamp_ns.wrapping_add(index.wrapping_mul(self.period_ns))
    }

    /// Timestamps of all samples.
    pub fn timestamps(&self) -> impl Iterator<Item = u64> + '_ {
        (0..self.samples.len() as u64).map(|i| self.timestamp(i))
    }

    /// Encodes a data frame.
    ///
    /// # Errors
    /// Returns [`Error::Protocol`] if the schema ID differs, the schema has no
    /// fields, a sample has the wrong number of values, a value does not fit
    /// its field type, or there are more than 65535 samples.
    pub fn to_bytes(&self, schema: &StreamSchema) -> Result<Vec<u8>> {
        if schema.schema_id() != self.schema_id {
            return Err(protocol(format!(
                "Schema ID mismatch: data has {:#x}, schema has {:#x}",
                self.schema_id,
                schema.schema_id()
            )));
        }
        if schema.fields().is_empty() {
            return Err(protocol("Schema has no fields"));
        }
        let count = u16::try_from(self.samples.len()).map_err(|_| {
            protocol(format!("Too many samples: {} (max 65535)", self.samples.len()))
        })?;
        let mut out =
            Vec::with_capacity(DATA_HEADER_SIZE + self.samples.len() * schema.sample_size());
        out.push(MSG_TYPE_DATA);
        out.extend_from_slice(&self.schema_id.to_be_bytes());
        out.extend_from_slice(&self.timestamp_ns.to_be_bytes());
        out.extend_from_slice(&self.period_ns.to_be_bytes());
        out.extend_from_slice(&count.to_be_bytes());
        let n_fields = schema.fields().len();
        for sample in &self.samples {
            if sample.len() != n_fields {
                return Err(protocol(format!(
                    "Sample has {} values, schema has {n_fields} fields",
                    sample.len()
                )));
            }
            for (value, field) in sample.iter().zip(schema.fields()) {
                value.pack(field, &mut out)?;
            }
        }
        Ok(out)
    }

    /// Decodes a data frame using `schema`.
    ///
    /// # Errors
    /// Returns [`Error::Protocol`] for a wrong message type, a schema ID that
    /// does not match `schema`, or a truncated frame.
    pub fn from_bytes(data: &[u8], schema: &StreamSchema) -> Result<Self> {
        let mut r = Reader::new(data, 0);
        let msg_type = r.u8()?;
        if msg_type != MSG_TYPE_DATA {
            return Err(protocol(format!(
                "Invalid message type: expected {MSG_TYPE_DATA}, got {msg_type}"
            )));
        }
        let schema_id = r.u32()?;
        if schema_id != schema.schema_id() {
            return Err(protocol(format!(
                "Schema ID mismatch: data has {schema_id:#x}, schema has {:#x}",
                schema.schema_id()
            )));
        }
        let timestamp_ns = r.u64()?;
        let period_ns = r.u64()?;
        let count = usize::from(r.u16()?);
        let body = r.take(count * schema.sample_size())?;
        let mut samples = Vec::with_capacity(count);
        let mut pos = 0;
        for _ in 0..count {
            let mut sample = Vec::with_capacity(schema.fields().len());
            for f in schema.fields() {
                let size = f.dtype.size();
                sample.push(SampleValue::unpack(f.dtype, &body[pos..pos + size]));
                pos += size;
            }
            samples.push(sample);
        }
        Ok(Self { schema_id, timestamp_ns, period_ns, samples })
    }
}

/// Reads the message type and schema ID from any frame without decoding it.
///
/// # Errors
/// Returns [`Error::Protocol`] if the frame is shorter than 5 bytes.
pub fn peek_header(data: &[u8]) -> Result<(u8, u32)> {
    let mut r = Reader::new(data, 0);
    Ok((r.u8()?, r.u32()?))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn adc_schema() -> StreamSchema {
        StreamSchema::new(
            "adc",
            vec![
                StreamField::new("ch0", DataType::F32, "V"),
                StreamField::new("ch1", DataType::I16, "mA"),
                StreamField::new("ch2", DataType::U8, ""),
            ],
        )
    }

    #[test]
    fn string_encoding_limits() {
        let mut out = Vec::new();
        encode_string(&mut out, &"a".repeat(255)).unwrap();
        assert_eq!(out.len(), 256);
        let err = encode_string(&mut Vec::new(), &"a".repeat(256)).unwrap_err();
        assert!(err.to_string().contains("String too long"));
        assert_eq!(decode_string(b"xxx\x05hello", 3).unwrap(), ("hello".into(), 9));
        assert!(decode_string(b"\x05hel", 0).unwrap_err().to_string().contains("Truncated"));
    }

    #[test]
    fn schema_layout() {
        let s = adc_schema();
        assert_eq!(s.sample_size(), 7);
        assert_eq!(s.field_offset("ch0"), Some(0));
        assert_eq!(s.field_offset("ch1"), Some(4));
        assert_eq!(s.field_offset("ch2"), Some(6));
        assert_eq!(s.field_offset("nope"), None);
        assert_eq!(s.field("ch1").unwrap().unit, "mA");
    }

    #[test]
    fn schema_id_ignores_source() {
        let f = vec![StreamField::new("x", DataType::I32, "")];
        assert_eq!(
            StreamSchema::new("a", f.clone()).schema_id(),
            StreamSchema::new("b", f).schema_id()
        );
    }

    #[test]
    fn schema_round_trip_and_corruption() {
        let s = adc_schema();
        let bytes = s.to_bytes().unwrap();
        assert_eq!(StreamSchema::from_bytes(&bytes).unwrap(), s);

        let mut bad = bytes.clone();
        bad[1] ^= 0xFF;
        assert!(StreamSchema::from_bytes(&bad).unwrap_err().to_string().contains("mismatch"));
        assert!(StreamSchema::from_bytes(&bytes[..bytes.len() - 1]).is_err());
        assert!(StreamSchema::from_bytes(&[]).is_err());
        let mut wrong_type = bytes;
        wrong_type[0] = MSG_TYPE_DATA;
        assert!(StreamSchema::from_bytes(&wrong_type)
            .unwrap_err()
            .to_string()
            .contains("Invalid message type"));
    }

    #[test]
    fn data_round_trip() {
        let s = adc_schema();
        let d = StreamData {
            schema_id: s.schema_id(),
            timestamp_ns: 1_000,
            period_ns: 10,
            samples: vec![
                vec![1.5.into(), (-3i64).into(), 255i64.into()],
                vec![(-2.25).into(), 32767i64.into(), 0i64.into()],
            ],
        };
        let bytes = d.to_bytes(&s).unwrap();
        assert_eq!(bytes.len(), DATA_HEADER_SIZE + 2 * 7);
        assert_eq!(StreamData::from_bytes(&bytes, &s).unwrap(), d);
        assert_eq!(d.timestamps().collect::<Vec<_>>(), vec![1_000, 1_010]);
        assert_eq!(peek_header(&bytes).unwrap(), (MSG_TYPE_DATA, s.schema_id()));
    }

    #[test]
    fn data_rejects_bad_values() {
        let s = adc_schema();
        let mk = |sample: Vec<SampleValue>| StreamData {
            schema_id: s.schema_id(),
            timestamp_ns: 0,
            period_ns: 0,
            samples: vec![sample],
        };
        let err = |d: StreamData| d.to_bytes(&s).unwrap_err().to_string();
        assert!(err(mk(vec![1.0.into(), 1i64.into()])).contains("Sample has 2 values"));
        assert!(err(mk(vec![1.0.into(), 1.5.into(), 1i64.into()])).contains("requires an integer"));
        assert!(err(mk(vec![1.0.into(), 40_000i64.into(), 1i64.into()])).contains("out of range"));
        assert!(err(mk(vec![1.0.into(), 1i64.into(), (-1i64).into()])).contains("out of range"));
        let mut d = mk(vec![1.0.into(), 1i64.into(), 1i64.into()]);
        d.schema_id ^= 1;
        assert!(err(d).contains("Schema ID mismatch"));
    }

    #[test]
    fn data_rejects_truncated_body() {
        let s = adc_schema();
        let d = StreamData {
            schema_id: s.schema_id(),
            timestamp_ns: 0,
            period_ns: 0,
            samples: vec![vec![0.0.into(), 0i64.into(), 0i64.into()]],
        };
        let bytes = d.to_bytes(&s).unwrap();
        let err = StreamData::from_bytes(&bytes[..bytes.len() - 1], &s).unwrap_err();
        assert!(err.to_string().contains("Truncated"));
    }

    #[test]
    fn u64_extremes() {
        let s = StreamSchema::new("u", vec![StreamField::new("x", DataType::U64, "")]);
        let d = StreamData {
            schema_id: s.schema_id(),
            timestamp_ns: 0,
            period_ns: 0,
            samples: vec![vec![u64::MAX.into()], vec![0u64.into()]],
        };
        let back = StreamData::from_bytes(&d.to_bytes(&s).unwrap(), &s).unwrap();
        assert_eq!(back.samples[0][0], SampleValue::UInt(u64::MAX));
        assert_eq!(back.samples[1][0], SampleValue::Int(0));
    }
}
