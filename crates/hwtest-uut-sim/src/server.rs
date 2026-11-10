//! HTTP API (same routes and JSON as the Python `pi4-waveshare-sim`).
//!
//! `GET /`, `/health`, `/status`; CAN: `POST /can/send`, `GET|DELETE
//! /can/received`, `GET|PUT /can/echo`, `GET /can/heartbeat`; DAC: `POST
//! /dac/write`, `POST /dac/write-both`, `GET /dac/status`, `GET
//! /dac/{channel}`; ADC: `GET /adc/status`, `GET /adc/{channel}`; GPIO: `GET
//! /gpio/status`, `POST /gpio/configure|write|write-port|write-all`, `GET
//! /gpio/{pin}`; faults: `GET /failure/status`, `PUT /failure/config`, `POST
//! /failure/reset`; emulation only: `PUT /emulation/adc`, `PUT /emulation/gpio`,
//! `GET /emulation/dac/{channel}` (driven output including faults).
//! Errors are `{"detail": "..."}` with 400 (bad value) or 500 (unavailable).

use std::sync::Arc;

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::{Html, IntoResponse, Json, Response};
use axum::routing::{get, post, put};
use axum::Router;
use hwtest_instruments::can::CanMessage;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::simulator::{EchoConfig, GpioBackend, SimError, Simulator};

type AppState = Arc<Simulator>;

fn error(e: &SimError) -> Response {
    let code = if matches!(e, SimError::Invalid(_)) {
        StatusCode::BAD_REQUEST
    } else {
        StatusCode::INTERNAL_SERVER_ERROR
    };
    (code, Json(json!({"detail": e.to_string()}))).into_response()
}

async fn blocking(
    sim: &AppState,
    f: impl FnOnce(&Simulator) -> Result<Value, SimError> + Send + 'static,
) -> Response {
    let s = sim.clone();
    match tokio::task::spawn_blocking(move || f(&s)).await {
        Ok(Ok(v)) => Json(v).into_response(),
        Ok(Err(e)) => error(&e),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"detail": e.to_string()})))
            .into_response(),
    }
}

/// Builds the router.
pub fn router(sim: Arc<Simulator>) -> Router {
    Router::new()
        .route("/", get(dashboard))
        .route("/health", get(health))
        .route("/status", get(status))
        .route("/can/send", post(can_send))
        .route("/can/received", get(can_received).delete(can_clear))
        .route("/can/echo", get(can_echo).put(can_set_echo))
        .route("/can/heartbeat", get(can_heartbeat))
        .route("/dac/write", post(dac_write))
        .route("/dac/write-both", post(dac_write_both))
        .route("/dac/status", get(dac_status))
        .route("/dac/{channel}", get(dac_read))
        .route("/adc/status", get(adc_status))
        .route("/adc/{channel}", get(adc_read))
        .route("/gpio/status", get(gpio_status))
        .route("/gpio/configure", post(gpio_configure))
        .route("/gpio/write", post(gpio_write))
        .route("/gpio/write-port", post(gpio_write_port))
        .route("/gpio/write-all", post(gpio_write_all))
        .route("/gpio/{pin}", get(gpio_read))
        .route("/failure/status", get(failure_status))
        .route("/failure/config", put(failure_config))
        .route("/failure/reset", post(failure_reset))
        .route("/emulation/adc", put(emulation_adc))
        .route("/emulation/gpio", put(emulation_gpio))
        .route("/emulation/dac/{channel}", get(emulation_dac))
        .with_state(sim)
}

async fn health(State(s): State<AppState>) -> Json<Value> {
    let c = s.config();
    let (can, dac, adc, gpio) = s.available();
    let degraded = (c.can_interface.is_some() && !can)
        || (!matches!(c.adda, crate::AdDaBackend::None) && (!dac || !adc))
        || (!matches!(c.gpio, GpioBackend::None) && !gpio);
    Json(
        json!({"status": if degraded { "degraded" } else { "healthy" }, "version": env!("CARGO_PKG_VERSION"), "uptime_seconds": s.uptime()}),
    )
}

async fn status(State(s): State<AppState>) -> Json<Value> {
    let c = s.config();
    let (can, dac, adc, gpio) = s.available();
    let address = match c.gpio {
        GpioBackend::Mcp23017 { address, .. } => address,
        _ => 0x20,
    };
    Json(
        json!({"can_enabled": can, "can_interface": c.can_interface.clone().unwrap_or_default(), "dac_enabled": dac, "gpio_enabled": gpio, "gpio_address": address, "adc_enabled": adc}),
    )
}

#[derive(Deserialize)]
struct CanSend {
    message: CanMessage,
}

fn can_json(m: &CanMessage) -> Value {
    json!({"arbitration_id": m.arbitration_id, "data": m.data, "is_extended_id": m.is_extended_id, "is_fd": m.is_fd})
}

async fn can_send(State(s): State<AppState>, Json(r): Json<CanSend>) -> Response {
    blocking(&s, move |s| s.can_send(&r.message).map(|()| json!({"status": "sent"}))).await
}

async fn can_received(State(s): State<AppState>) -> Json<Value> {
    Json(Value::Array(s.can_received().iter().map(can_json).collect()))
}

async fn can_clear(State(s): State<AppState>) -> Json<Value> {
    s.can_clear_received();
    Json(json!({"status": "cleared"}))
}

async fn can_echo(State(s): State<AppState>) -> Json<EchoConfig> {
    Json(s.can_echo())
}

async fn can_set_echo(State(s): State<AppState>, Json(e): Json<EchoConfig>) -> Response {
    match s.can_set_echo(e) {
        Ok(()) => Json(json!({"status": "ok"})).into_response(),
        Err(e) => error(&e),
    }
}

async fn can_heartbeat(State(s): State<AppState>) -> Json<Value> {
    Json(json!(s.can_heartbeat()))
}

#[derive(Deserialize)]
struct DacWrite {
    channel: u8,
    voltage: f64,
}

#[derive(Deserialize)]
struct DacBoth {
    voltage_a: f64,
    voltage_b: f64,
}

async fn dac_write(State(s): State<AppState>, Json(r): Json<DacWrite>) -> Response {
    blocking(&s, move |s| s.dac_write(r.channel, r.voltage).map(|()| json!({"status": "ok"}))).await
}

async fn dac_write_both(State(s): State<AppState>, Json(r): Json<DacBoth>) -> Response {
    blocking(&s, move |s| {
        s.dac_write(0, r.voltage_a)?;
        s.dac_write(1, r.voltage_b)?;
        Ok(json!({"status": "ok"}))
    })
    .await
}

async fn dac_status(State(s): State<AppState>) -> Response {
    blocking(&s, |s| {
        let channels: Vec<Value> = [0u8, 1]
            .iter()
            .map(|c| json!({"channel": c, "voltage": s.dac_read(*c).unwrap_or(0.0)}))
            .collect();
        Ok(json!({"channels": channels}))
    })
    .await
}

async fn dac_read(State(s): State<AppState>, Path(channel): Path<u8>) -> Response {
    blocking(&s, move |s| s.dac_read(channel).map(|v| json!({"channel": channel, "voltage": v})))
        .await
}

async fn adc_status(State(s): State<AppState>) -> Response {
    blocking(&s, |s| {
        let ch: Result<Vec<_>, _> = (0..8u8)
            .map(|c| s.adc_read(c).map(|v| json!({"channel": c, "voltage": v, "raw": 0})))
            .collect();
        Ok(json!({"channels": ch?}))
    })
    .await
}

async fn adc_read(State(s): State<AppState>, Path(channel): Path<u8>) -> Response {
    blocking(&s, move |s| {
        s.adc_read(channel).map(|v| json!({"channel": channel, "voltage": v, "raw": 0}))
    })
    .await
}

async fn gpio_status(State(s): State<AppState>) -> Response {
    blocking(&s, |s| {
        let port = |p: &str| {
            s.gpio_port(p).map(|(v, d)| json!({"port": p, "value": v, "direction_mask": d}))
        };
        Ok(json!({"port_a": port("A")?, "port_b": port("B")?}))
    })
    .await
}

#[derive(Deserialize)]
struct GpioConfigure {
    pin: u8,
    direction: String,
    #[serde(default)]
    pullup: bool,
}

async fn gpio_configure(State(s): State<AppState>, Json(r): Json<GpioConfigure>) -> Response {
    blocking(&s, move |s| {
        let output = match r.direction.as_str() {
            "output" => true,
            "input" => false,
            other => {
                return Err(SimError::Invalid(format!(
                    "direction must be input or output, got {other:?}"
                )))
            }
        };
        s.gpio_configure(r.pin, output, r.pullup).map(|()| json!({"status": "ok"}))
    })
    .await
}

#[derive(Deserialize)]
struct GpioWrite {
    pin: u8,
    value: bool,
}

async fn gpio_write(State(s): State<AppState>, Json(r): Json<GpioWrite>) -> Response {
    blocking(&s, move |s| s.gpio_write(r.pin, r.value).map(|()| json!({"status": "ok"}))).await
}

#[derive(Deserialize)]
struct GpioPort {
    port: String,
    value: u8,
}

async fn gpio_write_port(State(s): State<AppState>, Json(r): Json<GpioPort>) -> Response {
    blocking(&s, move |s| s.gpio_write_port(&r.port, r.value).map(|()| json!({"status": "ok"})))
        .await
}

#[derive(Deserialize)]
struct GpioAll {
    value: u16,
}

async fn gpio_write_all(State(s): State<AppState>, Json(r): Json<GpioAll>) -> Response {
    blocking(&s, move |s| s.gpio_write_all(r.value).map(|()| json!({"status": "ok"}))).await
}

async fn gpio_read(State(s): State<AppState>, Path(pin): Path<u8>) -> Response {
    blocking(&s, move |s| s.gpio_read(pin).map(|(v, out)| json!({"pin": pin, "value": v, "direction": if out { "output" } else { "input" }}))).await
}

async fn failure_status(State(s): State<AppState>) -> Json<Value> {
    Json(json!(s.failure_status()))
}

#[derive(Deserialize)]
struct FailureConfig {
    delay_seconds: Option<f64>,
    duration_seconds: Option<f64>,
    voltage_offset: Option<f64>,
}

async fn failure_config(State(s): State<AppState>, Json(r): Json<FailureConfig>) -> Response {
    match s.failure_configure(r.delay_seconds, r.duration_seconds, r.voltage_offset) {
        Ok(()) => Json(json!({"status": "ok"})).into_response(),
        Err(e) => error(&e),
    }
}

async fn failure_reset(State(s): State<AppState>) -> Json<Value> {
    s.failure_reset();
    Json(json!({"status": "reset"}))
}

#[derive(Deserialize)]
struct EmuAdc {
    channel: u8,
    voltage: f64,
}

async fn emulation_adc(State(s): State<AppState>, Json(r): Json<EmuAdc>) -> Response {
    match s.emulate_adc_input(r.channel, r.voltage) {
        Ok(()) => Json(json!({"status": "ok"})).into_response(),
        Err(e) => error(&e),
    }
}

#[derive(Deserialize)]
struct EmuGpio {
    levels: u16,
}

async fn emulation_gpio(State(s): State<AppState>, Json(r): Json<EmuGpio>) -> Response {
    match s.emulate_gpio_inputs(r.levels) {
        Ok(()) => Json(json!({"status": "ok"})).into_response(),
        Err(e) => error(&e),
    }
}

async fn emulation_dac(State(s): State<AppState>, Path(channel): Path<u8>) -> Response {
    match s.emulated_dac_output(channel) {
        Some(v) if channel <= 1 => Json(json!({"channel": channel, "voltage": v})).into_response(),
        Some(_) => error(&SimError::Invalid(format!("channel must be 0 or 1, got {channel}"))),
        None => error(&SimError::Unavailable("emulated DAC")),
    }
}

async fn dashboard(State(s): State<AppState>) -> impl IntoResponse {
    let (can, dac, adc, gpio) = s.available();
    let hb = s.can_heartbeat();
    let f = s.failure_status();
    let yes = |b: bool| if b { "<b class=ok>available</b>" } else { "<b class=off>off</b>" };
    Html(format!(
        r#"<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="2"><title>UUT Simulator</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem}}td{{padding:.3rem 1rem;border-bottom:1px solid #ddd}}.ok{{color:#1a7f37}}.off{{color:#777}}.bad{{color:#cf222e}}</style></head>
<body><h1>UUT Simulator</h1><p>Uptime {:.0} s</p><table>
<tr><td>CAN</td><td>{}</td><td>heartbeat id {:#x}, {} sent</td></tr>
<tr><td>DAC</td><td>{}</td><td>A {:.3} V, B {:.3} V</td></tr>
<tr><td>ADC</td><td>{}</td><td></td></tr>
<tr><td>GPIO</td><td>{}</td><td></td></tr>
<tr><td>Fault injection</td><td>{}</td><td>delay {} s, duration {} s, offset {} V</td></tr>
</table></body></html>"#,
        s.uptime(),
        yes(can),
        hb.arbitration_id,
        hb.message_count,
        yes(dac),
        s.dac_read(0).unwrap_or(0.0),
        s.dac_read(1).unwrap_or(0.0),
        yes(adc),
        yes(gpio),
        if f.active {
            "<b class=bad>ACTIVE</b>"
        } else if f.enabled {
            "armed"
        } else {
            "off"
        },
        f.delay_seconds,
        f.duration_seconds,
        f.voltage_offset
    ))
}

/// A server running on its own thread (for embedding, e.g. from Python).
/// Stops when dropped.
pub struct BackgroundServer {
    addr: std::net::SocketAddr,
    sim: Arc<Simulator>,
    shutdown: Option<tokio::sync::oneshot::Sender<()>>,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl BackgroundServer {
    /// Starts serving `sim` on `host:port` (port 0 picks a free port).
    ///
    /// # Errors
    /// The address cannot be bound.
    pub fn start(sim: Simulator, host: &str, port: u16) -> std::io::Result<Self> {
        let sim = Arc::new(sim);
        let rt =
            tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build()?;
        let listener = rt.block_on(tokio::net::TcpListener::bind((host, port)))?;
        let addr = listener.local_addr()?;
        let (tx, rx) = tokio::sync::oneshot::channel::<()>();
        let app = router(sim.clone());
        let thread = std::thread::spawn(move || {
            rt.block_on(async move {
                let _ = axum::serve(listener, app)
                    .with_graceful_shutdown(async {
                        let _ = rx.await;
                    })
                    .await;
            });
        });
        Ok(Self { addr, sim, shutdown: Some(tx), thread: Some(thread) })
    }

    /// Bound address.
    pub fn addr(&self) -> std::net::SocketAddr {
        self.addr
    }

    /// The simulator.
    pub fn simulator(&self) -> &Simulator {
        &self.sim
    }
}

impl Drop for BackgroundServer {
    fn drop(&mut self) {
        if let Some(tx) = self.shutdown.take() {
            let _ = tx.send(());
        }
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}
