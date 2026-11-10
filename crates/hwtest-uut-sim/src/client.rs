//! Blocking client for the simulator's HTTP API (exposed to test scripts as
//! `hwtest.UutClient`).

use std::time::Duration;

use hwtest_instruments::can::CanMessage;
use serde_json::{json, Value};

/// Client error.
#[derive(Debug, thiserror::Error)]
pub enum ClientError {
    /// Network error.
    #[error("UUT {url}: {message}")]
    Http {
        /// Request URL.
        url: String,
        /// Cause.
        message: String,
    },
    /// The UUT rejected the request.
    #[error("UUT {url}: {status}: {detail}")]
    Rejected {
        /// Request URL.
        url: String,
        /// HTTP status.
        status: u16,
        /// Server message.
        detail: String,
    },
}

/// Convenience alias.
pub type Result<T> = std::result::Result<T, ClientError>;

/// UUT simulator client.
#[derive(Clone)]
pub struct UutClient {
    base: String,
    agent: ureq::Agent,
}

impl UutClient {
    /// Creates a client for `base_url` (e.g. `http://192.168.68.4:8080`).
    pub fn new(base_url: &str, timeout: Duration) -> Self {
        let agent = ureq::Agent::config_builder()
            .timeout_global(Some(timeout))
            .http_status_as_error(false)
            .build()
            .into();
        Self { base: base_url.trim_end_matches('/').to_string(), agent }
    }

    /// Base URL.
    pub fn base_url(&self) -> &str {
        &self.base
    }

    fn call(&self, method: &str, path: &str, body: Option<Value>) -> Result<Value> {
        let url = format!("{}{path}", self.base);
        let http = |e: ureq::Error| ClientError::Http { url: url.clone(), message: e.to_string() };
        let resp = match (method, body) {
            ("GET", _) => self.agent.get(&url).call(),
            ("DELETE", _) => self.agent.delete(&url).call(),
            ("POST", b) => self
                .agent
                .post(&url)
                .header("content-type", "application/json")
                .send(b.unwrap_or(json!({})).to_string()),
            (_, b) => self
                .agent
                .put(&url)
                .header("content-type", "application/json")
                .send(b.unwrap_or(json!({})).to_string()),
        };
        let mut resp = resp.map_err(http)?;
        let status = resp.status().as_u16();
        let text = resp.body_mut().read_to_string().map_err(http)?;
        let value: Value = serde_json::from_str(&text).unwrap_or(Value::String(text));
        if (200..300).contains(&status) {
            Ok(value)
        } else {
            let detail = value.get("detail").map_or_else(
                || value.to_string(),
                |d| d.as_str().map_or_else(|| d.to_string(), String::from),
            );
            Err(ClientError::Rejected { url, status, detail })
        }
    }

    /// `GET /health`.
    ///
    /// # Errors
    /// Network failure or rejection.
    pub fn health(&self) -> Result<Value> {
        self.call("GET", "/health", None)
    }
    /// `GET /status`.
    ///
    /// # Errors
    /// Network failure or rejection.
    pub fn status(&self) -> Result<Value> {
        self.call("GET", "/status", None)
    }
    /// Sends a CAN frame from the UUT.
    ///
    /// # Errors
    /// Network failure or rejection.
    pub fn can_send(&self, msg: &CanMessage) -> Result<()> {
        self.call("POST", "/can/send", Some(json!({"message": {"arbitration_id": msg.arbitration_id, "data": msg.data, "is_extended_id": msg.is_extended_id, "is_fd": msg.is_fd}}))).map(|_| ())
    }
    /// Frames the UUT received.
    ///
    /// # Errors
    /// Network failure or rejection.
    pub fn can_received(&self) -> Result<Value> {
        self.call("GET", "/can/received", None)
    }
    /// Clears the UUT's receive buffer.
    ///
    /// # Errors
    /// Network failure or rejection.
    pub fn can_clear_received(&self) -> Result<()> {
        self.call("DELETE", "/can/received", None).map(|_| ())
    }
    /// Echo settings.
    ///
    /// # Errors
    /// Network failure or rejection.
    pub fn can_echo(&self) -> Result<Value> {
        self.call("GET", "/can/echo", None)
    }
    /// Sets echo.
    ///
    /// # Errors
    /// Network failure or rejection.
    pub fn can_set_echo(
        &self,
        enabled: bool,
        id_offset: i64,
        filter_ids: Option<&[u32]>,
    ) -> Result<()> {
        self.call(
            "PUT",
            "/can/echo",
            Some(json!({"enabled": enabled, "id_offset": id_offset, "filter_ids": filter_ids})),
        )
        .map(|_| ())
    }
    /// Heartbeat status.
    ///
    /// # Errors
    /// Network failure or rejection.
    pub fn can_heartbeat(&self) -> Result<Value> {
        self.call("GET", "/can/heartbeat", None)
    }
    /// Writes a UUT DAC channel.
    ///
    /// # Errors
    /// Network failure or rejection.
    pub fn dac_write(&self, channel: u8, volts: f64) -> Result<()> {
        self.call("POST", "/dac/write", Some(json!({"channel": channel, "voltage": volts})))
            .map(|_| ())
    }
    /// Reads back a UUT DAC channel.
    ///
    /// # Errors
    /// Network failure or rejection.
    pub fn dac_read(&self, channel: u8) -> Result<f64> {
        Ok(self.call("GET", &format!("/dac/{channel}"), None)?["voltage"]
            .as_f64()
            .unwrap_or(f64::NAN))
    }
    /// Reads a UUT ADC channel.
    ///
    /// # Errors
    /// Network failure or rejection.
    pub fn adc_read(&self, channel: u8) -> Result<f64> {
        Ok(self.call("GET", &format!("/adc/{channel}"), None)?["voltage"]
            .as_f64()
            .unwrap_or(f64::NAN))
    }
    /// Configures a UUT GPIO pin.
    ///
    /// # Errors
    /// Network failure or rejection.
    pub fn gpio_configure(&self, pin: u8, output: bool, pullup: bool) -> Result<()> {
        self.call("POST", "/gpio/configure", Some(json!({"pin": pin, "direction": if output { "output" } else { "input" }, "pullup": pullup}))).map(|_| ())
    }
    /// Drives a UUT GPIO pin.
    ///
    /// # Errors
    /// Network failure or rejection.
    pub fn gpio_write(&self, pin: u8, value: bool) -> Result<()> {
        self.call("POST", "/gpio/write", Some(json!({"pin": pin, "value": value}))).map(|_| ())
    }
    /// Reads a UUT GPIO pin.
    ///
    /// # Errors
    /// Network failure or rejection.
    pub fn gpio_read(&self, pin: u8) -> Result<bool> {
        Ok(self.call("GET", &format!("/gpio/{pin}"), None)?["value"].as_bool().unwrap_or(false))
    }
    /// Fault injection status.
    ///
    /// # Errors
    /// Network failure or rejection.
    pub fn failure_status(&self) -> Result<Value> {
        self.call("GET", "/failure/status", None)
    }
    /// Configures fault injection.
    ///
    /// # Errors
    /// Network failure or rejection.
    pub fn failure_configure(
        &self,
        delay: Option<f64>,
        duration: Option<f64>,
        offset: Option<f64>,
    ) -> Result<()> {
        self.call("PUT", "/failure/config", Some(json!({"delay_seconds": delay, "duration_seconds": duration, "voltage_offset": offset}))).map(|_| ())
    }
    /// Resets the fault timer.
    ///
    /// # Errors
    /// Network failure or rejection.
    pub fn failure_reset(&self) -> Result<()> {
        self.call("POST", "/failure/reset", None).map(|_| ())
    }
    /// Emulation: the DAC output actually driven (including fault offset).
    ///
    /// # Errors
    /// Network failure or rejection (not emulated).
    pub fn emulated_dac_output(&self, channel: u8) -> Result<f64> {
        Ok(self.call("GET", &format!("/emulation/dac/{channel}"), None)?["voltage"]
            .as_f64()
            .unwrap_or(f64::NAN))
    }

    /// Emulation: sets an emulated ADC input.
    ///
    /// # Errors
    /// Network failure or rejection (not emulated).
    pub fn emulate_adc_input(&self, channel: u8, volts: f64) -> Result<()> {
        self.call("PUT", "/emulation/adc", Some(json!({"channel": channel, "voltage": volts})))
            .map(|_| ())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::simulator::{AdDaBackend, GpioBackend, SimConfig, Simulator};
    use std::sync::Arc;

    #[test]
    fn client_against_server() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        let listener = rt.block_on(tokio::net::TcpListener::bind("127.0.0.1:0")).unwrap();
        let addr = listener.local_addr().unwrap();
        let sim = Arc::new(Simulator::start(SimConfig {
            can_interface: Some("virtual:client_test".into()),
            adda: AdDaBackend::Emulated { loopback: true },
            gpio: GpioBackend::Emulated,
            ..SimConfig::default()
        }));
        rt.spawn(async move { axum::serve(listener, crate::server::router(sim)).await.unwrap() });
        let c = UutClient::new(&format!("http://{addr}"), Duration::from_secs(5));
        assert_eq!(c.health().unwrap()["status"], "healthy");
        assert_eq!(c.status().unwrap()["can_interface"], "virtual:client_test");
        c.dac_write(0, 1.5).unwrap();
        assert_eq!(c.dac_read(0).unwrap(), 1.5);
        assert!((c.adc_read(0).unwrap() - 1.5).abs() < 1e-3);
        assert!((c.emulated_dac_output(0).unwrap() - 1.5).abs() < 1e-3);
        c.emulate_adc_input(4, 0.75).unwrap();
        assert!((c.adc_read(4).unwrap() - 0.75).abs() < 1e-6);
        let e = c.dac_write(0, 9.0).unwrap_err().to_string();
        assert!(e.contains("400") && e.contains("voltage must be 0-5V"), "{e}");
        c.gpio_configure(2, true, false).unwrap();
        c.gpio_write(2, true).unwrap();
        assert!(c.gpio_read(2).unwrap());
        c.can_set_echo(true, 0x10, None).unwrap();
        assert_eq!(c.can_echo().unwrap()["id_offset"], 0x10);
        c.can_send(&CanMessage::new(0x123, &[1])).unwrap();
        assert!(c.can_heartbeat().unwrap()["running"].as_bool().unwrap());
        c.failure_configure(Some(5.0), Some(1.0), Some(0.5)).unwrap();
        assert_eq!(c.failure_status().unwrap()["enabled"], true);
        c.failure_reset().unwrap();
        c.can_clear_received().unwrap();
        assert!(UutClient::new("http://127.0.0.1:1", Duration::from_millis(500)).health().is_err());
    }
}
