//! Analog loop calibration: drive a known voltage on an output channel,
//! measure it on an input channel, and compute the input's scale factor.
//!
//! On the integration bench this is MCC 152 AO0 -> MCC 118 CH0, stored as
//! `mcc118_scale_factor` in the rack instance file.

use std::time::Duration;

use serde::Serialize;

use crate::rack::{Rack, Result};

/// One reference point.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct CalibrationPoint {
    /// Voltage driven.
    pub reference_voltage: f64,
    /// Average voltage measured.
    pub measured_voltage: f64,
    /// `reference / measured` (1.0 if the measurement is ~0).
    pub scale_factor: f64,
}

/// Calibration outcome.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct CalibrationResult {
    /// Mean of the point scale factors.
    pub scale_factor: f64,
    /// Points measured.
    pub points: Vec<CalibrationPoint>,
}

impl CalibrationResult {
    /// Human-readable summary for the instance file.
    pub fn notes(&self) -> String {
        let v: Vec<String> =
            self.points.iter().map(|p| format!("{}V", p.reference_voltage)).collect();
        format!("Calibrated using {} reference points", v.join(", "))
    }
}

/// Settings.
#[derive(Debug, Clone)]
pub struct CalibrationPlan {
    /// Logical analog output that drives the reference.
    pub output: String,
    /// Logical analog input to calibrate.
    pub input: String,
    /// Reference voltages.
    pub voltages: Vec<f64>,
    /// Wait after each output change.
    pub settling: Duration,
    /// Readings averaged per point.
    pub samples: usize,
}

/// Runs the calibration. The output is returned to 0 V afterwards.
///
/// # Errors
/// Channel or instrument errors.
pub fn calibrate(rack: &Rack, plan: &CalibrationPlan) -> Result<CalibrationResult> {
    let mut points = Vec::new();
    let outcome = (|| {
        for &v in &plan.voltages {
            rack.write_analog(&plan.output, v)?;
            std::thread::sleep(plan.settling);
            let mut sum = 0.0;
            let n = plan.samples.max(1);
            for _ in 0..n {
                sum += rack.read_analog(&plan.input)?;
                std::thread::sleep(Duration::from_millis(10));
            }
            #[allow(clippy::cast_precision_loss)]
            let measured = sum / n as f64;
            let scale_factor = if measured.abs() > 0.001 { v / measured } else { 1.0 };
            tracing::info!(
                "reference {v:.3} V -> measured {measured:.4} V (scale {scale_factor:.5})"
            );
            points.push(CalibrationPoint {
                reference_voltage: v,
                measured_voltage: measured,
                scale_factor,
            });
        }
        Ok(())
    })();
    let _ = rack.write_analog(&plan.output, 0.0);
    outcome?;
    #[allow(clippy::cast_precision_loss)]
    let scale_factor = if points.is_empty() {
        1.0
    } else {
        points.iter().map(|p| p.scale_factor).sum::<f64>() / points.len() as f64
    };
    Ok(CalibrationResult { scale_factor, points })
}

#[cfg(test)]
mod tests {
    use super::*;
    use hwtest_instruments::daqhats::emulator::AnalogOut;
    use hwtest_instruments::DriverEnv;
    use hwtest_spec::RackConfig;

    #[test]
    fn computes_scale_and_restores_output() {
        let cfg = RackConfig::from_yaml_str(
            "rack: {id: r}\ninstruments:\n  dio: {driver: mcc152, identity: {manufacturer: Measurement Computing, model: MCC 152}, kwargs: {address: 0, analog_channels: [{id: 0, name: ref_out}]}}\n  daq: {driver: mcc118, identity: {manufacturer: Measurement Computing, model: MCC 118}, kwargs: {address: 4, channels: [{id: 0, name: meas}]}}\n",
            None,
        )
        .unwrap();
        let rack = Rack::open(cfg, None, DriverEnv::emulated());
        let hats = rack.env().emulated_hats().unwrap();
        hats.connect_analog_scaled(AnalogOut { address: 0, channel: 0 }, 4, 0, 0.98);
        let plan = CalibrationPlan {
            output: "ref_out".into(),
            input: "meas".into(),
            voltages: vec![1.0, 2.5, 4.0],
            settling: Duration::ZERO,
            samples: 3,
        };
        let r = calibrate(&rack, &plan).unwrap();
        assert_eq!(r.points.len(), 3);
        assert!((r.scale_factor - 1.0 / 0.98).abs() < 1e-9, "{}", r.scale_factor);
        assert!((r.points[1].measured_voltage - 2.45).abs() < 1e-12);
        assert_eq!(hats.analog_output(0, 0), Some(0.0), "output returned to 0 V");
        assert_eq!(r.notes(), "Calibrated using 1V, 2.5V, 4V reference points");
    }
}
