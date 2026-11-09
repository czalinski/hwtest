//! Bound checks for YAML/JSON-configured field monitoring.
//!
//! Each check serializes as a single-key map whose key names the check type:
//!
//! ```yaml
//! voltage:     {within_range: [3.3, 0.1]}        # center, delta
//! ripple:      {within_tolerance: [5.0, 0.02]}   # center, fraction
//! temperature: {within_baseline: [25, 5, 1]}     # nominal, init_delta, tight_delta[, baseline]
//! current:     {less_than: 2.0}
//! fan_rpm:     {greater_than: 1000}
//! supply:      {good_interval: [4.75, 5.25]}
//! resonance:   {bad_interval: [120, 130]}
//! mode:        {good_values: [0, 1, 2]}
//! status:      {bad_values: [255]}
//! debug:       {special: any}
//! ```
//!
//! Non-finite measurements (NaN, ±inf) fail every check except
//! `special: any`, so a faulted sensor never reads as a pass. (The original
//! Python passed e.g. `-inf` for `less_than` and raised on NaN for value sets.)

use std::collections::BTreeSet;

use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::{json, Value};

use crate::error::{Error, Result};

fn threshold_err(msg: impl Into<String>) -> Error {
    Error::Threshold(msg.into())
}

fn non_negative(type_name: &str, field: &str, v: f64) -> Result<()> {
    if v >= 0.0 {
        Ok(())
    } else {
        Err(threshold_err(format!("{type_name} {field} must be >= 0, got {v}")))
    }
}

fn ordered(type_name: &str, low: f64, high: f64) -> Result<()> {
    if low <= high {
        Ok(())
    } else {
        Err(threshold_err(format!("{type_name} requires low <= high, got low={low}, high={high}")))
    }
}

fn number(key: &str, v: &Value) -> Result<f64> {
    v.as_f64().ok_or_else(|| threshold_err(format!("{key}: expected a number, got {v}")))
}

fn numbers<const N: usize>(key: &str, v: &Value, names: &str) -> Result<[f64; N]> {
    let arr = v
        .as_array()
        .filter(|a| a.len() == N)
        .ok_or_else(|| threshold_err(format!("{key}: expected [{names}], got {v}")))?;
    let mut out = [0.0; N];
    for (o, item) in out.iter_mut().zip(arr) {
        *o = number(key, item)?;
    }
    Ok(out)
}

fn int_set(key: &str, v: &Value) -> Result<BTreeSet<i64>> {
    let arr = v
        .as_array()
        .ok_or_else(|| threshold_err(format!("{key}: expected a list of integers, got {v}")))?;
    arr.iter()
        .map(|item| {
            item.as_i64()
                .ok_or_else(|| threshold_err(format!("{key}: expected an integer, got {item}")))
        })
        .collect()
}

/// Rounds half-to-even like Python's `round()`; `None` if the result is not
/// representable as `i64` (or the input is not finite).
#[allow(clippy::cast_possible_truncation, clippy::cast_precision_loss)]
fn round_to_i64(value: f64) -> Option<i64> {
    let r = value.round_ties_even();
    // i64::MAX as f64 rounds up to 2^63, so use a strict upper bound.
    (r.is_finite() && r >= i64::MIN as f64 && r < i64::MAX as f64).then_some(r as i64)
}

/// Value within a fractional tolerance of a center:
/// `center*(1-fraction) <= value <= center*(1+fraction)` (works for negative centers).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct WithinTolerance {
    center: f64,
    fraction: f64,
}

impl WithinTolerance {
    /// YAML/JSON key.
    pub const KEY: &'static str = "within_tolerance";

    /// # Errors
    /// [`Error::Threshold`] if `fraction` is negative or NaN.
    pub fn new(center: f64, fraction: f64) -> Result<Self> {
        non_negative("WithinTolerance", "fraction", fraction)?;
        Ok(Self { center, fraction })
    }
    /// Center value.
    pub fn center(&self) -> f64 {
        self.center
    }
    /// Fractional tolerance (0.1 = 10%).
    pub fn fraction(&self) -> f64 {
        self.fraction
    }
    /// Inclusive `(low, high)` acceptance interval.
    pub fn interval(&self) -> (f64, f64) {
        let a = self.center * (1.0 - self.fraction);
        let b = self.center * (1.0 + self.fraction);
        (a.min(b), a.max(b))
    }
    /// Evaluates the check.
    pub fn check(&self, value: f64) -> bool {
        let (lo, hi) = self.interval();
        value.is_finite() && lo <= value && value <= hi
    }
    fn args(&self) -> Value {
        json!([self.center, self.fraction])
    }
    fn from_args(v: &Value) -> Result<Self> {
        let [c, f] = numbers(Self::KEY, v, "center, fraction")?;
        Self::new(c, f)
    }
}

/// Value within an absolute delta of a center: `|value - center| <= delta`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct WithinRange {
    center: f64,
    delta: f64,
}

impl WithinRange {
    /// YAML/JSON key.
    pub const KEY: &'static str = "within_range";

    /// # Errors
    /// [`Error::Threshold`] if `delta` is negative or NaN.
    pub fn new(center: f64, delta: f64) -> Result<Self> {
        non_negative("WithinRange", "delta", delta)?;
        Ok(Self { center, delta })
    }
    /// Center value.
    pub fn center(&self) -> f64 {
        self.center
    }
    /// Absolute tolerance.
    pub fn delta(&self) -> f64 {
        self.delta
    }
    /// Inclusive `(low, high)` acceptance interval.
    pub fn interval(&self) -> (f64, f64) {
        (self.center - self.delta, self.center + self.delta)
    }
    /// Evaluates the check.
    pub fn check(&self, value: f64) -> bool {
        let (lo, hi) = self.interval();
        value.is_finite() && lo <= value && value <= hi
    }
    fn args(&self) -> Value {
        json!([self.center, self.delta])
    }
    fn from_args(v: &Value) -> Result<Self> {
        let [c, d] = numbers(Self::KEY, v, "center, delta")?;
        Self::new(c, d)
    }
}

/// Two-phase check. Until locked, the value must be within
/// `nominal ± init_delta`; the first passing value becomes the baseline, after
/// which values must be within `baseline ± tight_delta`.
///
/// This is the only stateful check. Equality ignores the locked baseline.
#[derive(Debug, Clone, Copy)]
pub struct WithinBaseline {
    nominal: f64,
    init_delta: f64,
    tight_delta: f64,
    baseline: Option<f64>,
}

impl PartialEq for WithinBaseline {
    fn eq(&self, other: &Self) -> bool {
        self.nominal == other.nominal
            && self.init_delta == other.init_delta
            && self.tight_delta == other.tight_delta
    }
}

impl WithinBaseline {
    /// YAML/JSON key.
    pub const KEY: &'static str = "within_baseline";

    /// # Errors
    /// [`Error::Threshold`] if either delta is negative or NaN.
    pub fn new(nominal: f64, init_delta: f64, tight_delta: f64) -> Result<Self> {
        non_negative("WithinBaseline", "init_delta", init_delta)?;
        non_negative("WithinBaseline", "tight_delta", tight_delta)?;
        Ok(Self { nominal, init_delta, tight_delta, baseline: None })
    }
    /// Expected nominal value.
    pub fn nominal(&self) -> f64 {
        self.nominal
    }
    /// Acquisition tolerance (phase 1).
    pub fn init_delta(&self) -> f64 {
        self.init_delta
    }
    /// Tracking tolerance (phase 2).
    pub fn tight_delta(&self) -> f64 {
        self.tight_delta
    }
    /// The locked baseline, if any.
    pub fn baseline(&self) -> Option<f64> {
        self.baseline
    }
    /// True once a baseline has been captured.
    pub fn is_locked(&self) -> bool {
        self.baseline.is_some()
    }
    /// Restores a previously captured baseline.
    pub fn lock(&mut self, baseline: f64) {
        self.baseline = Some(baseline);
    }
    /// Clears the baseline, returning to phase 1.
    pub fn reset(&mut self) {
        self.baseline = None;
    }
    /// Current inclusive `(low, high)` acceptance interval.
    pub fn interval(&self) -> (f64, f64) {
        match self.baseline {
            Some(b) => (b - self.tight_delta, b + self.tight_delta),
            None => (self.nominal - self.init_delta, self.nominal + self.init_delta),
        }
    }
    /// Evaluates the check, locking the baseline on the first pass.
    pub fn check(&mut self, value: f64) -> bool {
        let (lo, hi) = self.interval();
        let ok = value.is_finite() && lo <= value && value <= hi;
        if ok && self.baseline.is_none() {
            self.baseline = Some(value);
        }
        ok
    }
    fn args(&self) -> Value {
        let mut a = vec![json!(self.nominal), json!(self.init_delta), json!(self.tight_delta)];
        if let Some(b) = self.baseline {
            a.push(json!(b));
        }
        Value::Array(a)
    }
    fn from_args(v: &Value) -> Result<Self> {
        let names = "nominal, init_delta, tight_delta[, baseline]";
        let arr = v
            .as_array()
            .filter(|a| a.len() == 3 || a.len() == 4)
            .ok_or_else(|| threshold_err(format!("{}: expected [{names}], got {v}", Self::KEY)))?;
        let n = |i: usize| number(Self::KEY, &arr[i]);
        let mut b = Self::new(n(0)?, n(1)?, n(2)?)?;
        if arr.len() == 4 {
            b.lock(n(3)?);
        }
        Ok(b)
    }
}

/// Value strictly less than a limit.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LessThan {
    limit: f64,
}

impl LessThan {
    /// YAML/JSON key.
    pub const KEY: &'static str = "less_than";
    /// Creates the check.
    pub fn new(limit: f64) -> Self {
        Self { limit }
    }
    /// Exclusive upper limit.
    pub fn limit(&self) -> f64 {
        self.limit
    }
    /// Evaluates the check.
    pub fn check(&self, value: f64) -> bool {
        value.is_finite() && value < self.limit
    }
}

/// Value strictly greater than a limit.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct GreaterThan {
    limit: f64,
}

impl GreaterThan {
    /// YAML/JSON key.
    pub const KEY: &'static str = "greater_than";
    /// Creates the check.
    pub fn new(limit: f64) -> Self {
        Self { limit }
    }
    /// Exclusive lower limit.
    pub fn limit(&self) -> f64 {
        self.limit
    }
    /// Evaluates the check.
    pub fn check(&self, value: f64) -> bool {
        value.is_finite() && value > self.limit
    }
}

/// Value inside the inclusive interval `[low, high]`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct GoodInterval {
    low: f64,
    high: f64,
}

impl GoodInterval {
    /// YAML/JSON key.
    pub const KEY: &'static str = "good_interval";
    /// # Errors
    /// [`Error::Threshold`] unless `low <= high`.
    pub fn new(low: f64, high: f64) -> Result<Self> {
        ordered("GoodInterval", low, high)?;
        Ok(Self { low, high })
    }
    /// Inclusive lower bound.
    pub fn low(&self) -> f64 {
        self.low
    }
    /// Inclusive upper bound.
    pub fn high(&self) -> f64 {
        self.high
    }
    /// Evaluates the check.
    pub fn check(&self, value: f64) -> bool {
        value.is_finite() && self.low <= value && value <= self.high
    }
    fn args(&self) -> Value {
        json!([self.low, self.high])
    }
    fn from_args(v: &Value) -> Result<Self> {
        let [l, h] = numbers(Self::KEY, v, "low, high")?;
        Self::new(l, h)
    }
}

/// Value outside the inclusive interval `[low, high]`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BadInterval {
    low: f64,
    high: f64,
}

impl BadInterval {
    /// YAML/JSON key.
    pub const KEY: &'static str = "bad_interval";
    /// # Errors
    /// [`Error::Threshold`] unless `low <= high`.
    pub fn new(low: f64, high: f64) -> Result<Self> {
        ordered("BadInterval", low, high)?;
        Ok(Self { low, high })
    }
    /// Lower bound of the forbidden interval.
    pub fn low(&self) -> f64 {
        self.low
    }
    /// Upper bound of the forbidden interval.
    pub fn high(&self) -> f64 {
        self.high
    }
    /// Evaluates the check.
    pub fn check(&self, value: f64) -> bool {
        value.is_finite() && (value < self.low || value > self.high)
    }
    fn args(&self) -> Value {
        json!([self.low, self.high])
    }
    fn from_args(v: &Value) -> Result<Self> {
        let [l, h] = numbers(Self::KEY, v, "low, high")?;
        Self::new(l, h)
    }
}

/// Rounded (half-to-even) value is one of a set of allowed integers.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct GoodValues {
    values: BTreeSet<i64>,
}

impl GoodValues {
    /// YAML/JSON key.
    pub const KEY: &'static str = "good_values";
    /// Creates the check.
    pub fn new(values: impl IntoIterator<Item = i64>) -> Self {
        Self { values: values.into_iter().collect() }
    }
    /// Allowed values.
    pub fn values(&self) -> &BTreeSet<i64> {
        &self.values
    }
    /// Evaluates the check.
    pub fn check(&self, value: f64) -> bool {
        round_to_i64(value).is_some_and(|r| self.values.contains(&r))
    }
}

/// Rounded (half-to-even) value is none of a set of forbidden integers.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct BadValues {
    values: BTreeSet<i64>,
}

impl BadValues {
    /// YAML/JSON key.
    pub const KEY: &'static str = "bad_values";
    /// Creates the check.
    pub fn new(values: impl IntoIterator<Item = i64>) -> Self {
        Self { values: values.into_iter().collect() }
    }
    /// Forbidden values.
    pub fn values(&self) -> &BTreeSet<i64> {
        &self.values
    }
    /// Evaluates the check. Non-finite values fail.
    pub fn check(&self, value: f64) -> bool {
        if !value.is_finite() {
            return false;
        }
        round_to_i64(value).is_none_or(|r| !self.values.contains(&r))
    }
}

/// Special checks. Only `kind = "any"` (always passes) exists; unknown kinds
/// are rejected so a typo cannot silently disable monitoring.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct Special {
    kind: String,
}

impl Special {
    /// YAML/JSON key.
    pub const KEY: &'static str = "special";
    /// Supported kinds.
    pub const KINDS: &'static [&'static str] = &["any"];

    /// # Errors
    /// [`Error::Threshold`] for an unsupported kind.
    pub fn new(kind: impl Into<String>) -> Result<Self> {
        let kind = kind.into();
        if Self::KINDS.contains(&kind.as_str()) {
            Ok(Self { kind })
        } else {
            Err(threshold_err(format!(
                "Unknown special bound kind: {kind:?} (supported: {:?})",
                Self::KINDS
            )))
        }
    }
    /// The special kind.
    pub fn kind(&self) -> &str {
        &self.kind
    }
    /// True for `special: any`.
    pub fn is_any(&self) -> bool {
        self.kind == "any"
    }
    /// Evaluates the check (always true for `any`).
    pub fn check(&self, _value: f64) -> bool {
        true
    }
}

/// Any bound check. Serializes as `{"<key>": <args>}`.
#[derive(Debug, Clone, PartialEq)]
pub enum BoundCheck {
    /// See [`WithinTolerance`].
    WithinTolerance(WithinTolerance),
    /// See [`WithinRange`].
    WithinRange(WithinRange),
    /// See [`WithinBaseline`].
    WithinBaseline(WithinBaseline),
    /// See [`LessThan`].
    LessThan(LessThan),
    /// See [`GreaterThan`].
    GreaterThan(GreaterThan),
    /// See [`GoodInterval`].
    GoodInterval(GoodInterval),
    /// See [`BadInterval`].
    BadInterval(BadInterval),
    /// See [`GoodValues`].
    GoodValues(GoodValues),
    /// See [`BadValues`].
    BadValues(BadValues),
    /// See [`Special`].
    Special(Special),
}

impl BoundCheck {
    /// All YAML/JSON keys.
    pub const KEYS: [&'static str; 10] = [
        WithinTolerance::KEY,
        WithinRange::KEY,
        WithinBaseline::KEY,
        LessThan::KEY,
        GreaterThan::KEY,
        GoodInterval::KEY,
        BadInterval::KEY,
        GoodValues::KEY,
        BadValues::KEY,
        Special::KEY,
    ];

    /// Evaluates the check. Takes `&mut self` because [`WithinBaseline`]
    /// locks its baseline on the first pass.
    pub fn check(&mut self, value: f64) -> bool {
        match self {
            BoundCheck::WithinTolerance(b) => b.check(value),
            BoundCheck::WithinRange(b) => b.check(value),
            BoundCheck::WithinBaseline(b) => b.check(value),
            BoundCheck::LessThan(b) => b.check(value),
            BoundCheck::GreaterThan(b) => b.check(value),
            BoundCheck::GoodInterval(b) => b.check(value),
            BoundCheck::BadInterval(b) => b.check(value),
            BoundCheck::GoodValues(b) => b.check(value),
            BoundCheck::BadValues(b) => b.check(value),
            BoundCheck::Special(b) => b.check(value),
        }
    }

    /// The YAML/JSON key of this check.
    pub fn key(&self) -> &'static str {
        match self {
            BoundCheck::WithinTolerance(_) => WithinTolerance::KEY,
            BoundCheck::WithinRange(_) => WithinRange::KEY,
            BoundCheck::WithinBaseline(_) => WithinBaseline::KEY,
            BoundCheck::LessThan(_) => LessThan::KEY,
            BoundCheck::GreaterThan(_) => GreaterThan::KEY,
            BoundCheck::GoodInterval(_) => GoodInterval::KEY,
            BoundCheck::BadInterval(_) => BadInterval::KEY,
            BoundCheck::GoodValues(_) => GoodValues::KEY,
            BoundCheck::BadValues(_) => BadValues::KEY,
            BoundCheck::Special(_) => Special::KEY,
        }
    }

    /// True for `special: any`, i.e. the field is intentionally unchecked.
    pub fn is_any(&self) -> bool {
        matches!(self, BoundCheck::Special(s) if s.is_any())
    }

    /// The current inclusive acceptance interval, for checks that have one.
    /// `less_than`/`greater_than` report an open infinite side.
    pub fn interval(&self) -> Option<(f64, f64)> {
        match self {
            BoundCheck::WithinTolerance(b) => Some(b.interval()),
            BoundCheck::WithinRange(b) => Some(b.interval()),
            BoundCheck::WithinBaseline(b) => Some(b.interval()),
            BoundCheck::GoodInterval(b) => Some((b.low, b.high)),
            BoundCheck::LessThan(b) => Some((f64::NEG_INFINITY, b.limit)),
            BoundCheck::GreaterThan(b) => Some((b.limit, f64::INFINITY)),
            BoundCheck::BadInterval(_)
            | BoundCheck::GoodValues(_)
            | BoundCheck::BadValues(_)
            | BoundCheck::Special(_) => None,
        }
    }

    /// The arguments (the value under the key).
    pub fn args(&self) -> Value {
        match self {
            BoundCheck::WithinTolerance(b) => b.args(),
            BoundCheck::WithinRange(b) => b.args(),
            BoundCheck::WithinBaseline(b) => b.args(),
            BoundCheck::LessThan(b) => json!(b.limit),
            BoundCheck::GreaterThan(b) => json!(b.limit),
            BoundCheck::GoodInterval(b) => b.args(),
            BoundCheck::BadInterval(b) => b.args(),
            BoundCheck::GoodValues(b) => json!(b.values),
            BoundCheck::BadValues(b) => json!(b.values),
            BoundCheck::Special(b) => json!(b.kind),
        }
    }

    /// Serializes to `{"<key>": <args>}`.
    pub fn to_json(&self) -> Value {
        let mut m = serde_json::Map::with_capacity(1);
        m.insert(self.key().to_owned(), self.args());
        Value::Object(m)
    }

    /// Builds a check from its key and arguments.
    ///
    /// # Errors
    /// [`Error::Threshold`] for an unknown key or invalid arguments.
    pub fn from_key_args(key: &str, args: &Value) -> Result<Self> {
        Ok(match key {
            WithinTolerance::KEY => BoundCheck::WithinTolerance(WithinTolerance::from_args(args)?),
            WithinRange::KEY => BoundCheck::WithinRange(WithinRange::from_args(args)?),
            WithinBaseline::KEY => BoundCheck::WithinBaseline(WithinBaseline::from_args(args)?),
            LessThan::KEY => BoundCheck::LessThan(LessThan::new(number(key, args)?)),
            GreaterThan::KEY => BoundCheck::GreaterThan(GreaterThan::new(number(key, args)?)),
            GoodInterval::KEY => BoundCheck::GoodInterval(GoodInterval::from_args(args)?),
            BadInterval::KEY => BoundCheck::BadInterval(BadInterval::from_args(args)?),
            GoodValues::KEY => BoundCheck::GoodValues(GoodValues { values: int_set(key, args)? }),
            BadValues::KEY => BoundCheck::BadValues(BadValues { values: int_set(key, args)? }),
            Special::KEY => {
                let kind = args.as_str().ok_or_else(|| {
                    threshold_err(format!("{key}: expected a string, got {args}"))
                })?;
                BoundCheck::Special(Special::new(kind)?)
            }
            other => return Err(threshold_err(format!("Unknown bound check type: {other:?}"))),
        })
    }

    /// Parses `{"<key>": <args>}`.
    ///
    /// # Errors
    /// [`Error::Threshold`] unless `value` is a single-key map with a known key
    /// and valid arguments.
    pub fn from_json(value: &Value) -> Result<Self> {
        let map = value
            .as_object()
            .ok_or_else(|| threshold_err(format!("Bound check must be a mapping, got {value}")))?;
        if map.len() != 1 {
            let keys: Vec<&String> = map.keys().collect();
            return Err(threshold_err(format!(
                "Bound check dict must have exactly one key, got {}: {keys:?}",
                map.len()
            )));
        }
        match map.iter().next() {
            Some((key, args)) => Self::from_key_args(key, args),
            None => unreachable!("map has exactly one entry"),
        }
    }
}

impl Serialize for BoundCheck {
    fn serialize<S: Serializer>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error> {
        self.to_json().serialize(serializer)
    }
}

impl<'de> Deserialize<'de> for BoundCheck {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        let value = Value::deserialize(deserializer)?;
        Self::from_json(&value).map_err(serde::de::Error::custom)
    }
}

macro_rules! impl_from {
    ($($t:ident),*) => {$(
        impl From<$t> for BoundCheck {
            fn from(b: $t) -> Self {
                BoundCheck::$t(b)
            }
        }
    )*};
}
impl_from!(
    WithinTolerance,
    WithinRange,
    WithinBaseline,
    LessThan,
    GreaterThan,
    GoodInterval,
    BadInterval,
    GoodValues,
    BadValues,
    Special
);

#[cfg(test)]
#[allow(clippy::needless_pass_by_value)]
mod tests {
    use super::*;

    fn parse(v: Value) -> BoundCheck {
        BoundCheck::from_json(&v).unwrap()
    }

    fn parse_err(v: Value) -> String {
        BoundCheck::from_json(&v).unwrap_err().to_string()
    }

    #[test]
    fn within_tolerance_handles_negative_center() {
        let b = WithinTolerance::new(-10.0, 0.1).unwrap();
        assert!(b.check(-11.0) && b.check(-9.0) && b.check(-10.0));
        assert!(!b.check(-8.9) && !b.check(-11.1));
    }

    #[test]
    fn negative_or_nan_tolerances_rejected() {
        assert!(WithinTolerance::new(1.0, -0.1).unwrap_err().to_string().contains("fraction"));
        assert!(WithinRange::new(1.0, f64::NAN).is_err());
        assert!(WithinBaseline::new(1.0, 1.0, -1.0).unwrap_err().to_string().contains("tight"));
        assert!(GoodInterval::new(2.0, 1.0).unwrap_err().to_string().contains("low <= high"));
    }

    #[test]
    fn within_baseline_locks_then_tracks() {
        let mut b = parse(json!({"within_baseline": [5.0, 0.5, 0.1]}));
        assert!(b.check(5.2)); // locks at 5.2
        assert!(b.check(5.25));
        assert!(!b.check(5.5)); // outside 5.2 ± 0.1 although inside 5.0 ± 0.5
        assert_eq!(b.to_json(), json!({"within_baseline": [5.0, 0.5, 0.1, 5.2]}));
        assert_eq!(b.interval().map(|(l, _)| (l * 100.0).round()), Some(510.0));
    }

    #[test]
    fn within_baseline_fail_does_not_lock() {
        let mut b = WithinBaseline::new(20.0, 4.0, 2.0).unwrap();
        assert!(!b.check(25.0));
        assert!(!b.is_locked());
        assert!(b.check(19.0));
        assert_eq!(b.baseline(), Some(19.0));
        b.reset();
        assert!(!b.is_locked());
    }

    #[test]
    fn value_sets_use_bankers_rounding() {
        let mut g = parse(json!({"good_values": [5]}));
        assert!(g.check(4.6) && g.check(5.4));
        assert!(!g.check(5.5)); // round(5.5) == 6
        assert!(!g.check(4.5)); // round(4.5) == 4
        let mut b = parse(json!({"bad_values": [-1, 255]}));
        assert!(!b.check(-0.6) && b.check(0.0));
        assert!(b.check(1e30)); // cannot be a forbidden i64
    }

    #[test]
    fn non_finite_values_fail_closed() {
        for v in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            for spec in [
                json!({"within_tolerance": [1.0, 0.1]}),
                json!({"within_range": [0.0, f64::MAX]}),
                json!({"within_baseline": [0.0, f64::MAX, 1.0]}),
                json!({"less_than": 0.0}),
                json!({"greater_than": 0.0}),
                json!({"good_interval": [f64::MIN, f64::MAX]}),
                json!({"bad_interval": [0, 1]}),
                json!({"good_values": [0]}),
                json!({"bad_values": []}),
            ] {
                assert!(!parse(spec.clone()).check(v), "{spec} accepted {v}");
            }
        }
        assert!(parse(json!({"special": "any"})).check(f64::NAN));
    }

    #[test]
    fn json_round_trip_all_types() {
        for spec in [
            json!({"within_tolerance": [10.0, 0.1]}),
            json!({"within_range": [3.3, 0.1]}),
            json!({"within_baseline": [20.0, 4.0, 2.0]}),
            json!({"within_baseline": [20.0, 4.0, 2.0, 19.5]}),
            json!({"less_than": 11.0}),
            json!({"greater_than": -1.0}),
            json!({"good_interval": [0.0, 5.0]}),
            json!({"bad_interval": [1.0, 2.0]}),
            json!({"good_values": [1, 2, 3]}),
            json!({"bad_values": [-1, 255]}),
            json!({"special": "any"}),
        ] {
            assert_eq!(parse(spec.clone()).to_json(), spec);
        }
        // Integers are accepted where floats are expected (YAML `[20, 4, 2]`).
        assert_eq!(
            parse(json!({"within_baseline": [20, 4, 2]})).to_json(),
            json!({"within_baseline": [20.0, 4.0, 2.0]})
        );
        // Sets are emitted sorted.
        assert_eq!(parse(json!({"good_values": [3, 1, 2]})).args(), json!([1, 2, 3]));
    }

    #[test]
    fn malformed_specs_report_clear_errors() {
        assert!(parse_err(json!({})).contains("exactly one key"));
        assert!(parse_err(json!({"less_than": 1, "greater_than": 0})).contains("exactly one key"));
        assert!(parse_err(json!({"unknown_type": 42})).contains("Unknown bound check type"));
        assert!(parse_err(json!({"within_range": [1.0]})).contains("[center, delta]"));
        assert!(parse_err(json!({"less_than": "ten"})).contains("expected a number"));
        assert!(parse_err(json!({"good_values": [1.5]})).contains("expected an integer"));
        assert!(parse_err(json!({"special": "anyy"})).contains("Unknown special bound kind"));
        assert!(parse_err(json!([1, 2])).contains("must be a mapping"));
    }

    #[test]
    fn serde_integration() {
        let b: BoundCheck = serde_json::from_str(r#"{"good_interval": [4.75, 5.25]}"#).unwrap();
        assert_eq!(b.interval(), Some((4.75, 5.25)));
        assert_eq!(serde_json::to_string(&b).unwrap(), r#"{"good_interval":[4.75,5.25]}"#);
        let e = serde_json::from_str::<BoundCheck>(r#"{"nope": 1}"#).unwrap_err();
        assert!(e.to_string().contains("Unknown bound check type"));
    }
}
