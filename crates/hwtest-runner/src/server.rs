//! Runner HTTP API and operator dashboard.
//!
//! | Route | |
//! |-------|-|
//! | `GET /` | dashboard: pick test + mode, start/stop, live status and log |
//! | `GET /health` | `{"status": "ok", "station_id"}` |
//! | `GET /status` | station, rack state, run status, test cases |
//! | `GET /test-cases` | test cases |
//! | `POST /run` | `{"test_case_id", "mode"}` starts a test (409 if busy) |
//! | `POST /stop` | stops the running test |
//! | `GET /run/status` | run status |
//! | `GET /run/log?since=N` | `{"lines": [...], "next": M}` |
//! | `GET /runs` | recent runs from the results database (if configured) |

use std::time::Duration;

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{Html, IntoResponse, Json, Response};
use axum::routing::{get, post};
use axum::Router;
use serde::Deserialize;
use serde_json::json;

use crate::executor::{Executor, StartError};

/// Builds the router.
pub fn router(executor: Executor) -> Router {
    Router::new()
        .route("/", get(dashboard))
        .route("/health", get(health))
        .route("/status", get(status))
        .route("/test-cases", get(test_cases))
        .route("/run", post(run))
        .route("/stop", post(stop))
        .route("/run/status", get(run_status))
        .route("/run/log", get(run_log))
        .route("/runs", get(runs))
        .with_state(executor)
}

fn test_case_list(e: &Executor) -> serde_json::Value {
    let list: Vec<_> = e
        .config()
        .station
        .test_cases
        .iter()
        .map(|t| json!({"id": t.id, "name": t.name, "modes": t.modes}))
        .collect();
    json!(list)
}

async fn health(State(e): State<Executor>) -> Json<serde_json::Value> {
    Json(json!({"status": "ok", "station_id": e.config().station.id}))
}

async fn rack_state(e: &Executor) -> String {
    let Some(url) = e.config().station.rack.url.clone() else {
        return "managed by test script".into();
    };
    tokio::task::spawn_blocking(move || {
        let agent: ureq::Agent = ureq::Agent::config_builder()
            .timeout_global(Some(Duration::from_secs(2)))
            .build()
            .into();
        agent
            .get(&format!("{}/health", url.trim_end_matches('/')))
            .call()
            .ok()
            .and_then(|mut r| r.body_mut().read_to_string().ok())
            .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
            .and_then(|v| v.get("status").and_then(|s| s.as_str()).map(String::from))
            .unwrap_or_else(|| "unreachable".into())
    })
    .await
    .unwrap_or_else(|_| "unknown".into())
}

async fn status(State(e): State<Executor>) -> Json<serde_json::Value> {
    let st = &e.config().station;
    Json(json!({
        "station_id": st.id,
        "description": st.description,
        "rack_state": rack_state(&e).await,
        "emulated": e.config().emulate,
        "run": e.status(),
        "test_cases": test_case_list(&e),
    }))
}

async fn test_cases(State(e): State<Executor>) -> Json<serde_json::Value> {
    Json(test_case_list(&e))
}

#[derive(Deserialize)]
struct RunRequest {
    test_case_id: String,
    #[serde(default = "functional")]
    mode: String,
}

fn functional() -> String {
    "functional".into()
}

async fn run(State(e): State<Executor>, Json(req): Json<RunRequest>) -> Response {
    match e.start(&req.test_case_id, &req.mode) {
        Ok(s) => Json(s).into_response(),
        Err(err @ StartError::Busy(_)) => {
            (StatusCode::CONFLICT, Json(json!({"detail": err.to_string()}))).into_response()
        }
        Err(err @ StartError::Spawn(_)) => {
            (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"detail": err.to_string()})))
                .into_response()
        }
        Err(err) => {
            (StatusCode::BAD_REQUEST, Json(json!({"detail": err.to_string()}))).into_response()
        }
    }
}

async fn stop(State(e): State<Executor>) -> Json<crate::RunStatus> {
    Json(e.stop())
}

async fn run_status(State(e): State<Executor>) -> Json<crate::RunStatus> {
    Json(e.status())
}

#[derive(Deserialize)]
struct LogQuery {
    #[serde(default)]
    since: u64,
}

async fn run_log(State(e): State<Executor>, Query(q): Query<LogQuery>) -> Json<serde_json::Value> {
    let (lines, next) = e.log_since(q.since);
    Json(json!({"lines": lines, "next": next}))
}

async fn runs(State(e): State<Executor>) -> Response {
    let Some(path) = e.config().db_path.clone() else {
        return Json(json!([])).into_response();
    };
    let result = tokio::task::spawn_blocking(move || -> Result<serde_json::Value, String> {
        let db = hwtest_db::Database::open(&path).map_err(|e| e.to_string())?;
        let runs = db.list_test_runs(None, 50).map_err(|e| e.to_string())?;
        let rows: Vec<_> = runs
            .iter()
            .map(|r| {
                let name = db.connection().query_row("SELECT name FROM test_case WHERE id = ?1", [r.test_case_id], |row| row.get::<_, String>(0)).unwrap_or_default();
                json!({"id": r.id, "test_case": name, "run_type": r.run_type, "status": r.status, "started_at": r.started_at, "finished_at": r.finished_at})
            })
            .collect();
        Ok(json!(rows))
    })
    .await;
    match result {
        Ok(Ok(v)) => Json(v).into_response(),
        Ok(Err(e)) => {
            (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"detail": e}))).into_response()
        }
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"detail": e.to_string()})))
            .into_response(),
    }
}

async fn dashboard(State(e): State<Executor>) -> impl IntoResponse {
    let st = &e.config().station;
    let esc = |s: &str| s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;");
    Html(
        DASHBOARD
            .replace("{{STATION}}", &esc(&st.id))
            .replace("{{DESCRIPTION}}", &esc(&st.description)),
    )
}

const DASHBOARD: &str = r#"<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Station {{STATION}}</title><style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#1f2328;--muted:#656d76;--ok:#1a7f37;--bad:#cf222e;--warn:#9a6700;--accent:#0969da}
body{font-family:system-ui,sans-serif;margin:0;background:var(--bg);color:var(--fg)}
header{padding:1rem 1.5rem;background:var(--card);border-bottom:1px solid #d0d7de}h1{margin:0;font-size:1.3rem}
main{padding:1rem 1.5rem;display:grid;gap:1rem;max-width:1100px}
.card{background:var(--card);border:1px solid #d0d7de;border-radius:8px;padding:1rem}
.row{display:flex;gap:.75rem;flex-wrap:wrap;align-items:center}
select,button{font-size:1rem;padding:.45rem .8rem;border-radius:6px;border:1px solid #d0d7de;background:#fff}
button.go{background:var(--ok);color:#fff;border-color:var(--ok)}button.stop{background:var(--bad);color:#fff;border-color:var(--bad)}
button:disabled{opacity:.5}
.big{font-size:1.6rem;font-weight:700}.muted{color:var(--muted)}
.passed{color:var(--ok)}.failed,.error{color:var(--bad)}.stopped{color:var(--warn)}
dl{display:grid;grid-template-columns:max-content 1fr;gap:.25rem 1rem;margin:0}dt{color:var(--muted)}
pre{background:#0d1117;color:#e6edf3;padding:.75rem;border-radius:6px;height:22rem;overflow:auto;margin:0;font-size:.85rem}
</style></head><body>
<header><h1>Station {{STATION}}</h1><div class="muted">{{DESCRIPTION}} · rack: <span id="rack">…</span></div></header>
<main>
<div class="card row">
 <label>Test <select id="tc"></select></label>
 <label>Mode <select id="mode"></select></label>
 <button class="go" id="start">Start</button><button class="stop" id="stopb">Stop</button>
 <span id="err" class="failed"></span>
</div>
<div class="card"><div class="big" id="state">Idle</div><div id="msg" class="muted"></div>
<dl style="margin-top:.75rem"><dt>Test</dt><dd id="test">—</dd><dt>Mode</dt><dd id="m">—</dd><dt>State</dt><dd id="cur">—</dd>
<dt>Cycles</dt><dd id="cyc">0</dd><dt>Pass / Fail</dt><dd id="pf">0 / 0</dd><dt>Run</dt><dd id="run">—</dd></dl></div>
<div class="card"><pre id="log"></pre></div>
</main>
<script>
let tcs=[],next=0,lastRun=null;
const $=id=>document.getElementById(id);
function fillModes(){const t=tcs.find(x=>x.id===$('tc').value);$('mode').innerHTML=(t?t.modes:[]).map(m=>`<option>${m}</option>`).join('')}
async function poll(){
 try{const s=await (await fetch('/status')).json();
  if(!tcs.length){tcs=s.test_cases;$('tc').innerHTML=tcs.map(t=>`<option value="${t.id}">${t.name}</option>`).join('');fillModes()}
  $('rack').textContent=s.rack_state+(s.emulated?' (emulated)':'');
  const r=s.run;const running=r.state!=='idle';
  $('state').textContent=running?(r.state==='stopping'?'Stopping…':'Running'):(r.outcome?r.outcome.toUpperCase():'Idle');
  $('state').className='big '+(running?'':(r.outcome||''));
  $('msg').textContent=r.message;$('test').textContent=r.test_case_id||'—';$('m').textContent=r.mode||'—';
  $('cur').textContent=r.current_state||'—';$('cyc').textContent=r.cycle;
  $('pf').textContent=`${r.stats.passes||0} / ${r.stats.failures||0}`;$('run').textContent=r.run_id||'—';
  $('start').disabled=running;$('stopb').disabled=r.state!=='running';
  if(r.run_id!==lastRun){lastRun=r.run_id;next=0;$('log').textContent=''}
  const l=await (await fetch('/run/log?since='+next)).json();
  if(l.lines.length){const p=$('log');const atEnd=p.scrollTop+p.clientHeight>=p.scrollHeight-5;p.textContent+=l.lines.join('\n')+'\n';if(atEnd)p.scrollTop=p.scrollHeight}
  next=l.next;
 }catch(e){}
}
$('tc').onchange=fillModes;
$('start').onclick=async()=>{$('err').textContent='';const r=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({test_case_id:$('tc').value,mode:$('mode').value})});if(!r.ok)$('err').textContent=(await r.json()).detail;poll()};
$('stopb').onclick=async()=>{await fetch('/stop',{method:'POST'});poll()};
poll();setInterval(poll,1000);
</script></body></html>"#;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::executor::RunnerConfig;
    use hwtest_spec::StationConfig;

    async fn call(
        app: &Router,
        method: &str,
        path: &str,
        body: Option<serde_json::Value>,
    ) -> (StatusCode, serde_json::Value) {
        use tower_service::Service as _;
        let mut req = axum::http::Request::builder().method(method).uri(path);
        let body = match body {
            Some(b) => {
                req = req.header("content-type", "application/json");
                axum::body::Body::from(b.to_string())
            }
            None => axum::body::Body::empty(),
        };
        let resp = app.clone().call(req.body(body).unwrap()).await.unwrap();
        let code = resp.status();
        let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20).await.unwrap();
        (code, serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null))
    }

    #[tokio::test]
    async fn api() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("t.py"), "print('hello')\nprint('@@hwtest {\"event\":\"run_finished\",\"outcome\":\"passed\",\"message\":\"ok\",\"cycles_completed\":1,\"passes\":0,\"failures\":0}')\n").unwrap();
        let p = dir.path().join("s.yaml");
        std::fs::write(&p, "station: {id: bench, description: Bench}\nrack: {config: r}\nuut: {url: u}\ntest_cases: [{id: t, name: Test T}]\n").unwrap();
        let ex = Executor::new(RunnerConfig {
            station: StationConfig::from_file(&p).unwrap(),
            python: "python3".into(),
            nats_url: None,
            db_path: None,
            emulate: false,
            stop_timeout: Duration::from_secs(5),
        });
        let app = router(ex.clone());
        let (_, st) = call(&app, "GET", "/status", None).await;
        assert_eq!(
            (st["station_id"].as_str(), st["rack_state"].as_str()),
            (Some("bench"), Some("managed by test script"))
        );
        assert_eq!(st["test_cases"][0]["modes"], json!(["functional"]));
        let (code, v) =
            call(&app, "POST", "/run", Some(json!({"test_case_id": "t", "mode": "hass"}))).await;
        assert_eq!(code, StatusCode::BAD_REQUEST);
        assert!(v["detail"].as_str().unwrap().contains("not enabled"));
        let (code, v) = call(&app, "POST", "/run", Some(json!({"test_case_id": "t"}))).await;
        assert_eq!((code, v["state"].as_str()), (StatusCode::OK, Some("running")));
        for _ in 0..200 {
            if ex.status().state == crate::RunState::Idle {
                break;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        let (_, v) = call(&app, "GET", "/run/status", None).await;
        assert_eq!(v["outcome"], "passed");
        let (_, v) = call(&app, "GET", "/run/log?since=0", None).await;
        assert_eq!(v["lines"], json!(["hello"]));
        let (_, v) = call(&app, "GET", "/runs", None).await;
        assert_eq!(v, json!([]));
    }
}
