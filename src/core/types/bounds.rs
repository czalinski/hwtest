use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize, Debug)]
#[serde(tag = "type")]
pub enum BoundCheck {
    WithinTolerance(WithinTolerance),
    WithinRange(WithinRange),
    WithinBaseline(WithinBaseline),
    LessThan(LessThan),
    GreaterThan(GreaterThan),
    GoodInterval(GoodInterval),
    BadInterval(BadInterval),
    GoodValues(GoodValues),
    BadValues(BadValues),
    Special(Special),
}

#[derive(Serialize, Deserialize, Debug)]
pub struct WithinTolerance {
    center: f64,
    fraction: f64,
}

impl WithinTolerance {
    pub fn check(&self, value: f64) -> bool {
        let lower = self.center * (1.0 - self.fraction);
        let upper = self.center * (1.0 + self.fraction);
        lower <= value && value <= upper
    }
}

#[derive(Serialize, Deserialize, Debug)]
pub struct WithinRange {
    center: f64,
    delta: f64,
}

impl WithinRange {
    pub fn check(&self, value: f64) -> bool {
        self.center - self.delta <= value && value <= self.center + self.delta
    }
}

#[derive(Serialize, Deserialize, Debug)]
pub struct WithinBaseline {
    nominal: f64,
    init_delta: f64,
    tight_delta: f64,
    baseline: Option<f64>,
}

impl WithinBaseline {
    pub fn check(&self, value: f64) -> bool {
        match self.baseline {
            Some(baseline) => baseline - self.tight_delta <= value && value <= baseline + self.tight_delta,
            None => {
                let lower = self.nominal - self.init_delta;
                let upper = self.nominal + self.init_delta;
                lower <= value && value <= upper
            }
        }
    }
}

#[derive(Serialize, Deserialize, Debug)]
pub struct LessThan {
    limit: f64,
}

impl LessThan {
    pub fn check(&self, value: f64) -> bool {
        value < self.limit
    }
}

#[derive(Serialize, Deserialize, Debug)]
pub struct GreaterThan {
    limit: f64,
}

impl GreaterThan {
    pub fn check(&self, value: f64) -> bool {
        value > self.limit
    }
}

#[derive(Serialize, Deserialize, Debug)]
pub struct GoodInterval {
    low: f64,
    high: f64,
}

impl GoodInterval {
    pub fn check(&self, value: f64) -> bool {
        self.low <= value && value <= self.high
    }
}

#[derive(Serialize, Deserialize, Debug)]
pub struct BadInterval {
    low: f64,
    high: f64,
}

impl BadInterval {
    pub fn check(&self, value: f64) -> bool {
        value < self.low || value > self.high
    }
}

#[derive(Serialize, Deserialize, Debug)]
pub struct GoodValues {
    values: Vec<i64>,
}

impl GoodValues {
    pub fn check(&self, value: f64) -> bool {
        let rounded = value.round() as i64;
        self.values.contains(&rounded)
    }
}

#[derive(Serialize, Deserialize, Debug)]
pub struct BadValues {
    values: Vec<i64>,
}

impl BadValues {
    pub fn check(&self, value: f64) -> bool {
        let rounded = value.round() as i64;
        !self.values.contains(&rounded)
    }
}

#[derive(Serialize, Deserialize, Debug)]
pub struct Special {
    kind: String,
}

impl Special {
    pub fn check(&self, _value: f64) -> bool {
        self.kind == "any"
    }
}

impl BoundCheck {
    pub fn check(&self, value: f64) -> bool {
        match self {
            BoundCheck::WithinTolerance(v) => v.check(value),
            BoundCheck::WithinRange(v) => v.check(value),
            BoundCheck::WithinBaseline(v) => v.check(value),
            BoundCheck::LessThan(v) => v.check(value),
            BoundCheck::GreaterThan(v) => v.check(value),
            BoundCheck::GoodInterval(v) => v.check(value),
            BoundCheck::BadInterval(v) => v.check(value),
            BoundCheck::GoodValues(v) => v.check(value),
            BoundCheck::BadValues(v) => v.check(value),
            BoundCheck::Special(v) => v.check(value),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_within_tolerance() {
        let check = BoundCheck::WithinTolerance(WithinTolerance {
            center: 100.0,
            fraction: 0.05,
        });
        assert!(check.check(102.0));
        assert!(!check.check(110.0));
    }

    #[test]
    fn test_within_range() {
        let check = BoundCheck::WithinRange(WithinRange {
            center: 3.3,
            delta: 0.1,
        });
        assert!(check.check(3.35));
        assert!(!check.check(3.5));
    }

    #[test]
    fn test_within_baseline() {
        let mut check = BoundCheck::WithinBaseline(WithinBaseline {
            nominal: 5.0,
            init_delta: 0.5,
            tight_delta: 0.1,
            baseline: None,
        });
        assert!(check.check(5.2));
        assert!(check.check(5.25));
        assert!(!check.check(5.5));
    }

    #[test]
    fn test_less_than() {
        let check = BoundCheck::LessThan(LessThan { limit: 100.0 });
        assert!(check.check(99.9));
        assert!(!check.check(100.0));
    }

    #[test]
    fn test_greater_than() {
        let check = BoundCheck::GreaterThan(GreaterThan { limit: 0.0 });
        assert!(check.check(0.1));
        assert!(!check.check(0.0));
    }

    #[test]
    fn test_good_interval() {
        let check = BoundCheck::GoodInterval(GoodInterval {
            low: 0.0,
            high: 5.0,
        });
        assert!(check.check(2.5));
        assert!(!check.check(5.1));
    }

    #[test]
    fn test_bad_interval() {
        let check = BoundCheck::BadInterval(BadInterval {
            low: 1.0,
            high: 2.0,
        });
        assert!(check.check(0.5));
        assert!(!check.check(1.5));
    }

    #[test]
    fn test_good_values() {
        let check = BoundCheck::GoodValues(GoodValues {
            values: vec![0, 1, 2],
        });
        assert!(check.check(1.4));
        assert!(!check.check(2.6));
    }

    #[test]
    fn test_bad_values() {
        let check = BoundCheck::BadValues(BadValues {
            values: vec![-1, 255],
        });
        assert!(check.check(0.0));
        assert!(!check.check(-0.6));
    }

    #[test]
    fn test_special_any() {
        let check = BoundCheck::Special(Special {
            kind: "any".to_string(),
        });
        assert!(check.check(123.45));
    }
}
