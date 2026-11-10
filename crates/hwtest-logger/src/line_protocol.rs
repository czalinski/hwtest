//! `InfluxDB` line protocol encoding.

use std::fmt::Write as _;

use hwtest_core::streaming::SampleValue;

fn escape(s: &str, chars: &[char]) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        if chars.contains(&c) || c == '\\' {
            out.push('\\');
        }
        out.push(c);
    }
    out
}

/// Escapes a measurement name.
pub fn measurement(s: &str) -> String {
    escape(s, &[',', ' '])
}

/// Escapes a tag key, tag value or field key.
pub fn key(s: &str) -> String {
    escape(s, &[',', '=', ' '])
}

/// A field value.
#[derive(Debug, Clone, PartialEq)]
pub enum FieldValue {
    /// Float (written as-is).
    Float(f64),
    /// Signed integer (`i` suffix).
    Int(i64),
    /// Unsigned integer (`u` suffix).
    UInt(u64),
    /// String (quoted).
    Str(String),
}

impl From<SampleValue> for FieldValue {
    fn from(v: SampleValue) -> Self {
        match v {
            SampleValue::Int(i) => FieldValue::Int(i),
            SampleValue::UInt(u) => FieldValue::UInt(u),
            SampleValue::Float(f) => FieldValue::Float(f),
        }
    }
}

impl FieldValue {
    /// Line-protocol text, or `None` for values `InfluxDB` cannot store (NaN, inf).
    pub fn encode(&self) -> Option<String> {
        match self {
            FieldValue::Float(f) if f.is_finite() => {
                let s = f.to_string();
                Some(if s.contains(['.', 'e', 'E']) { s } else { format!("{s}.0") })
            }
            FieldValue::Float(_) => None,
            FieldValue::Int(i) => Some(format!("{i}i")),
            FieldValue::UInt(u) => Some(format!("{u}u")),
            FieldValue::Str(s) => {
                Some(format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\"")))
            }
        }
    }
}

/// Encodes one point. Tags are sorted by key (as `InfluxDB` recommends).
/// Returns `None` if no field has a storable value.
pub fn point<'a>(
    measurement_name: &str,
    tags: impl IntoIterator<Item = (&'a str, &'a str)>,
    fields: impl IntoIterator<Item = (&'a str, FieldValue)>,
    timestamp_ns: i128,
) -> Option<String> {
    let mut tags: Vec<(&str, &str)> = tags.into_iter().filter(|(_, v)| !v.is_empty()).collect();
    tags.sort_by(|a, b| a.0.cmp(b.0));
    let fields: Vec<String> = fields
        .into_iter()
        .filter_map(|(k, v)| v.encode().map(|e| format!("{}={e}", key(k))))
        .collect();
    if fields.is_empty() {
        return None;
    }
    let mut line = measurement(measurement_name);
    for (k, v) in tags {
        let _ = write!(line, ",{}={}", key(k), key(v));
    }
    let _ = write!(line, " {} {timestamp_ns}", fields.join(","));
    Some(line)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encodes_points() {
        let line = point(
            "volt age",
            [("topic", "echo"), ("a b", "x,y=z"), ("empty", "")],
            [
                ("v", FieldValue::Float(3.0)),
                ("n", FieldValue::Int(-2)),
                ("s", FieldValue::Str("a\"b".into())),
                ("bad", FieldValue::Float(f64::NAN)),
            ],
            1_000,
        )
        .unwrap();
        assert_eq!(line, r#"volt\ age,a\ b=x\,y\=z,topic=echo v=3.0,n=-2i,s="a\"b" 1000"#);
        assert_eq!(point("m", [], [("v", FieldValue::Float(f64::INFINITY))], 0), None);
        assert_eq!(FieldValue::Float(1e-7).encode().unwrap(), "0.0000001");
        assert_eq!(FieldValue::UInt(u64::MAX).encode().unwrap(), "18446744073709551615u");
    }
}
