//! `hwtest-rack serve`: HTTP status API and dashboard, plus NATS streaming of
//! every instrument stream (`{prefix}.{source_id}.data`).
//!
//! | Route | Response |
//! |-------|----------|
//! | `GET /` | HTML dashboard |
//! | `GET /health` | `{"status": "ready"|"error", "rack_id": ...}` |
//! | `GET /status` | full rack status |
//! | `GET /instruments` | instrument statuses |
//! | `GET /instruments/{name}` | one instrument |
//! | `GET /channels` | logical channels |
//! | `GET /channels/{name}` | current value of an input channel |

use std::collections::HashMap;
use std::fmt::Write as _;
use std::sync::{Arc, Mutex};

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::{Html, IntoResponse, Json, Response};
use axum::routing::get;
use axum::Router;
use hwtest_core::StreamData;
use hwtest_instruments::ChannelKind;
use hwtest_nats::{NatsConnection, StreamPublisher};
use serde_json::json;

use crate::rack::Rack;

/// Shared server state.
#[derive(Clone)]
pub struct AppState {
    /// The rack.
    pub rack: Arc<Mutex<Rack>>,
    /// Latest streamed value per field (channel) name, used for channels
    /// whose instrument is streaming (single reads are not possible then).
    pub latest: Arc<Mutex<HashMap<String, f64>>>,
}

impl AppState {
    /// Wraps a rack.
    pub fn new(rack: Rack) -> Self {
        Self { rack: Arc::new(Mutex::new(rack)), latest: Arc::default() }
    }
}

fn rack(state: &AppState) -> std::sync::MutexGuard<'_, Rack> {
    state.rack.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

/// Builds the router.
pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/", get(dashboard))
        .route("/health", get(health))
        .route("/status", get(status))
        .route("/instruments", get(instruments))
        .route("/instruments/{name}", get(instrument))
        .route("/channels", get(channels))
        .route("/channels/{name}", get(channel_value))
        .with_state(state)
}

async fn health(State(s): State<AppState>) -> Json<serde_json::Value> {
    let r = rack(&s);
    Json(json!({"status": r.status().state, "rack_id": r.rack_id()}))
}

async fn status(State(s): State<AppState>) -> Json<crate::RackStatus> {
    Json(rack(&s).status())
}

async fn instruments(State(s): State<AppState>) -> Json<Vec<crate::InstrumentStatus>> {
    Json(rack(&s).status().instruments)
}

async fn instrument(State(s): State<AppState>, Path(name): Path<String>) -> Response {
    match rack(&s).instrument_status(&name) {
        Some(st) => Json(st).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": format!("Instrument {name:?} not found")})),
        )
            .into_response(),
    }
}

async fn channels(State(s): State<AppState>) -> Json<serde_json::Value> {
    let r = rack(&s);
    let list: Vec<_> = r
        .channels()
        .iter()
        .map(|c| json!({"name": c.logical_name, "instrument": c.instrument, "channel_id": c.channel_id, "type": c.channel_type.as_str()}))
        .collect();
    Json(json!(list))
}

async fn channel_value(State(s): State<AppState>, Path(name): Path<String>) -> Response {
    let cached = s.latest.lock().ok().and_then(|l| l.get(&name).copied());
    if let Some(v) = cached {
        return Json(json!({"name": name, "value": v, "source": "stream"})).into_response();
    }
    let state = s.clone();
    let result = tokio::task::spawn_blocking(move || {
        let r = rack(&state);
        let kind =
            r.config().channel(&name).and_then(|c| r.instrument(&c.instrument)).and_then(|i| {
                i.lock()
                    .ok()
                    .and_then(|i| i.channels().into_iter().find(|c| c.name == name).map(|c| c.kind))
            });
        let value = match kind {
            Some(ChannelKind::DigitalIn | ChannelKind::DigitalOut) => {
                r.read_digital(&name).map(|v| json!(v))
            }
            Some(ChannelKind::Psu) => {
                r.with_psu(&name, |p, ch| p.measure_voltage(ch)).map(|v| json!(v))
            }
            _ => r.read_analog(&name).map(|v| json!(v)),
        };
        value.map(|v| json!({"name": name, "value": v})).map_err(|e| e.to_string())
    })
    .await;
    match result {
        Ok(Ok(v)) => Json(v).into_response(),
        Ok(Err(e)) => (StatusCode::BAD_REQUEST, Json(json!({"detail": e}))).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"detail": e.to_string()})))
            .into_response(),
    }
}

fn esc(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
}

async fn dashboard(State(s): State<AppState>) -> impl IntoResponse {
    let st = rack(&s).status();
    let mut rows = String::new();
    for i in &st.instruments {
        let id = i.identity.as_ref().map_or_else(
            || "—".into(),
            |id| esc(&format!("{} {} (S/N {})", id.manufacturer, id.model, id.serial)),
        );
        let state = format!("{:?}", i.state).to_lowercase();
        let _ = write!(
            rows,
            "<tr><td>{}</td><td><code>{}</code></td><td class=\"{state}\">{state}</td><td>{}</td><td>{}</td></tr>",
            esc(&i.name),
            esc(&i.driver),
            id,
            esc(i.error.as_deref().unwrap_or(""))
        );
    }
    Html(format!(
        r#"<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="5">
<title>Rack {id}</title><style>
body{{font-family:system-ui,sans-serif;margin:2rem;background:#fafafa;color:#222}}
table{{border-collapse:collapse;width:100%;background:#fff}}td,th{{border:1px solid #ddd;padding:.4rem .6rem;text-align:left}}
th{{background:#f0f0f0}}.ready{{color:#1a7f37;font-weight:600}}.error{{color:#cf222e;font-weight:600}}.closed,.pending{{color:#777}}
</style></head><body><h1>Rack {id}</h1><p>{desc}</p>
<p>State: <b class="{state}">{state}</b>{serial}{emu}</p>
<table><tr><th>Instrument</th><th>Driver</th><th>State</th><th>Identity</th><th>Error</th></tr>{rows}</table>
<p><a href="/status">/status</a> · <a href="/channels">/channels</a></p></body></html>"#,
        id = esc(&st.rack_id),
        desc = esc(&st.description),
        state = esc(&st.state),
        serial = st.serial.map(|s| format!(" · serial {}", esc(&s))).unwrap_or_default(),
        emu = if st.emulated { " · <b>EMULATED</b>" } else { "" },
    ))
}

/// Starts streaming every ready instrument that supports it to NATS.
/// Returns the number of streams started.
///
/// # Errors
/// NATS failures creating publishers (instrument start errors are logged).
pub async fn start_streaming(
    state: &AppState,
    conn: &NatsConnection,
) -> hwtest_nats::Result<usize> {
    let instruments: Vec<_> = {
        let r = rack(state);
        r.instrument_names().into_iter().filter_map(|n| r.instrument(&n).map(|i| (n, i))).collect()
    };
    let mut started = 0;
    for (name, inst) in instruments {
        let schema = {
            let mut i = inst.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
            match i.stream() {
                Some(s) => s.schema(),
                None => continue,
            }
        };
        let publisher = StreamPublisher::start(conn.clone(), schema.clone()).await?;
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<StreamData>();
        let latest = state.latest.clone();
        let fields: Vec<String> = schema.fields().iter().map(|f| f.name.clone()).collect();
        tokio::spawn(async move {
            while let Some(d) = rx.recv().await {
                if let (Some(last), Ok(mut cache)) = (d.samples.last(), latest.lock()) {
                    for (f, v) in fields.iter().zip(last) {
                        cache.insert(f.clone(), v.as_f64());
                    }
                }
                if let Err(e) = publisher.publish(&d).await {
                    tracing::warn!("publish failed: {e}");
                }
            }
        });
        let mut i = inst.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        if let Some(s) = i.stream() {
            match s.start_stream(Box::new(move |d| {
                let _ = tx.send(d);
            })) {
                Ok(rate) => {
                    tracing::info!("streaming {name} at {rate} S/s");
                    started += 1;
                }
                Err(e) => tracing::error!("cannot stream {name}: {e}"),
            }
        }
    }
    Ok(started)
}

#[cfg(test)]
mod tests {
    use super::*;
    use hwtest_instruments::DriverEnv;
    use hwtest_spec::RackConfig;

    async fn get_json(app: &Router, path: &str) -> (StatusCode, serde_json::Value) {
        use tower_service::Service as _;
        let req = axum::http::Request::get(path).body(axum::body::Body::empty()).unwrap();
        let resp = app.clone().call(req).await.unwrap();
        let status = resp.status();
        let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20).await.unwrap();
        (status, serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null))
    }

    #[tokio::test]
    async fn routes() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let cfg = RackConfig::from_file(root.join("configs/pi5_mcc_intg_a_rack.yaml")).unwrap();
        let state = AppState::new(Rack::open(cfg, None, DriverEnv::emulated()));
        state.rack.lock().unwrap().env().emulated_hats().unwrap().set_analog_input(4, 2, 1.25);
        state.latest.lock().unwrap().insert("tc_1".into(), 23.5);
        let app = router(state);
        let (code, health) = get_json(&app, "/health").await;
        assert_eq!(
            (code, health["status"].as_str(), health["rack_id"].as_str()),
            (StatusCode::OK, Some("ready"), Some("pi5-mcc-intg-a"))
        );
        let (_, st) = get_json(&app, "/status").await;
        assert_eq!(st["instruments"].as_array().unwrap().len(), 3);
        assert_eq!(st["emulated"], true);
        let (code, _) = get_json(&app, "/instruments/nope").await;
        assert_eq!(code, StatusCode::NOT_FOUND);
        let (_, v) = get_json(&app, "/channels/voltage_2").await;
        assert_eq!(v["value"], 1.25);
        let (_, v) = get_json(&app, "/channels/tc_1").await;
        assert_eq!((v["value"].as_f64(), v["source"].as_str()), (Some(23.5), Some("stream")));
        let (_, v) = get_json(&app, "/channels/sensor_door").await;
        assert_eq!(v["value"], false);
        let (code, v) = get_json(&app, "/channels/nope").await;
        assert_eq!(code, StatusCode::BAD_REQUEST);
        assert!(v["detail"].as_str().unwrap().contains("unknown channel"));
        let (_, chans) = get_json(&app, "/channels").await;
        assert!(chans
            .as_array()
            .unwrap()
            .iter()
            .any(|c| c["name"] == "tc_0" && c["type"] == "daq_analog"));
    }
}
