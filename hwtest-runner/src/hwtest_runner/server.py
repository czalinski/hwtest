"""FastAPI server for the hwtest-runner web service.

Provides an HTML dashboard for operators to select and run test cases,
plus REST API endpoints for programmatic access.

Endpoints:
    GET  /           : HTML dashboard (test picker, live status)
    GET  /health     : Health check
    GET  /status     : Full station status as JSON
    GET  /test-cases : List available test cases
    POST /run        : Start a test run
    POST /stop       : Stop current test
    GET  /run/status : Current run status

Example:
    hwtest-runner configs/pi5_bench_a_station.yaml --port 8000
"""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from hwtest_rack import Rack
from hwtest_rack.config import load_config as load_rack_config
from hwtest_rack.instance import RackInstanceConfig, load_instance_config

from hwtest_runner.config import StationConfig, load_station_config
from hwtest_runner.executor import TestExecutor
from hwtest_runner.models import (
    RunRequest,
    RunState,
    RunStatus,
    StationStatus,
    TestCaseModel,
)

logger = logging.getLogger(__name__)

# Global state (set during lifespan)
_station: StationConfig | None = None
_rack: Rack | None = None
_executor: TestExecutor | None = None


def _get_station() -> StationConfig:
    if _station is None:
        raise RuntimeError("Station not initialized")
    return _station


def _get_rack() -> Rack:
    if _rack is None:
        raise RuntimeError("Rack not initialized")
    return _rack


def _get_executor() -> TestExecutor:
    if _executor is None:
        raise RuntimeError("Executor not initialized")
    return _executor


# Search paths for rack configuration YAML files
RACK_CONFIG_SEARCH_PATHS = [
    Path(__file__).parent.parent.parent.parent / "hwtest-intg" / "src" / "hwtest_intg" / "configs",
    Path(__file__).parent.parent.parent.parent / "hwtest-intg" / "configs",
]


def _find_rack_config_path(rack_config_name: str) -> Path:
    """Find a rack configuration YAML by name."""
    filenames = [f"{rack_config_name}.yaml", f"{rack_config_name}.yml"]
    for search_dir in RACK_CONFIG_SEARCH_PATHS:
        if not search_dir.is_dir():
            continue
        for filename in filenames:
            candidate = search_dir / filename
            if candidate.is_file():
                return candidate
    raise FileNotFoundError(
        f"Rack config not found: '{rack_config_name}'. Searched: {RACK_CONFIG_SEARCH_PATHS}"
    )


def create_app(config_path: str | Path | None = None) -> FastAPI:
    """Create a FastAPI application.

    Args:
        config_path: Path to station configuration YAML.

    Returns:
        Configured FastAPI application.
    """
    app_state: dict[str, Any] = {"config_path": config_path}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        global _station, _rack, _executor  # pylint: disable=global-statement

        cfg_path = app_state.get("config_path")
        if cfg_path:
            logger.info("Loading station configuration from %s", cfg_path)
            _station = load_station_config(cfg_path)

            # Load and initialize rack
            rack_config_path = _find_rack_config_path(_station.rack.config)
            logger.info("Loading rack config from %s", rack_config_path)
            rack_config = load_rack_config(rack_config_path)

            _rack = Rack(rack_config)
            _rack.initialize()
            logger.info(
                "Rack '%s' initialized (%s)",
                _rack.rack_id,
                _rack.state,
            )

            # Load rack instance calibration
            rack_class = _station.rack.config.replace("_rack", "")
            try:
                rack_instance = load_instance_config(rack_class, _station.rack.serial)
                logger.info(
                    "Loaded rack instance: %s #%s",
                    rack_instance.instance.rack_class,
                    rack_instance.instance.serial_number,
                )
            except FileNotFoundError:
                logger.warning(
                    "Rack instance config not found for '%s'. Using defaults.", rack_class
                )
                from hwtest_rack.instance import RackInstanceInfo, CalibrationMetadata

                rack_instance = RackInstanceConfig(
                    instance=RackInstanceInfo(
                        serial_number="default",
                        rack_class=rack_class,
                        description="Default instance (no calibration file found)",
                    ),
                    calibration={
                        "uut_adc_scale_factor": 2.0,
                        "mcc118_scale_factor": 1.0,
                    },
                    metadata=CalibrationMetadata(
                        notes="Using default values",
                    ),
                )

            _executor = TestExecutor(
                station=_station,
                rack=_rack,
                rack_instance=rack_instance,
            )

            logger.info(
                "Station '%s' ready with %d test cases",
                _station.id,
                len(_station.test_cases),
            )

        yield

        if _executor is not None:
            await _executor.stop()
            await _executor.wait()

        if _rack is not None:
            logger.info("Shutting down rack")
            _rack.close()
            _rack = None

        _executor = None
        _station = None

    app = FastAPI(
        title="hwtest Runner",
        description="Web-based test execution service",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_api_route("/", _dashboard, methods=["GET"], response_class=HTMLResponse)
    app.add_api_route("/health", _health, methods=["GET"])
    app.add_api_route("/status", _status, methods=["GET"], response_model=StationStatus)
    app.add_api_route(
        "/test-cases", _test_cases, methods=["GET"], response_model=list[TestCaseModel]
    )
    app.add_api_route("/run", _start_run, methods=["POST"], response_model=RunStatus)
    app.add_api_route("/stop", _stop_run, methods=["POST"], response_model=RunStatus)
    app.add_api_route("/run/status", _run_status, methods=["GET"], response_model=RunStatus)

    return app


# =============================================================================
# Endpoints
# =============================================================================


async def _health() -> dict[str, str]:
    rack = _get_rack()
    return {"status": "ok" if rack.state == "ready" else rack.state, "station_id": _get_station().id}


async def _status() -> StationStatus:
    station = _get_station()
    rack = _get_rack()
    executor = _get_executor()
    return StationStatus(
        station_id=station.id,
        description=station.description,
        rack_state=rack.state,
        run=executor.get_status(),
        test_cases=[
            TestCaseModel(id=tc.id, name=tc.name, modes=tc.modes) for tc in station.test_cases
        ],
    )


async def _test_cases() -> list[TestCaseModel]:
    station = _get_station()
    return [TestCaseModel(id=tc.id, name=tc.name, modes=tc.modes) for tc in station.test_cases]


async def _start_run(request: RunRequest) -> RunStatus:
    executor = _get_executor()
    try:
        await executor.start(request.test_case_id, request.mode)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return executor.get_status()


async def _stop_run() -> RunStatus:
    executor = _get_executor()
    await executor.stop()
    return executor.get_status()


async def _run_status() -> RunStatus:
    executor = _get_executor()
    return executor.get_status()


# =============================================================================
# HTML Dashboard
# =============================================================================


async def _dashboard() -> HTMLResponse:
    station = _get_station()
    rack = _get_rack()
    executor = _get_executor()
    run = executor.get_status()

    # Build test case options
    tc_options = ""
    for tc in station.test_cases:
        tc_options += f'<option value="{tc.id}" data-modes="{",".join(tc.modes)}">{tc.name}</option>\n'

    # Build mode radio buttons (populated by JS based on selected test case)
    mode_radios = ""

    rack_state_color = {
        "ready": "#28a745",
        "error": "#dc3545",
        "initializing": "#ffc107",
        "closed": "#6c757d",
    }.get(rack.state, "#6c757d")

    run_state_color = {
        "idle": "#6c757d",
        "running": "#28a745",
        "stopping": "#ffc107",
    }.get(run.state.value, "#6c757d")

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{station.id} - hwtest Runner</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                margin: 0;
                padding: 20px;
                background: #f5f5f5;
            }}
            .container {{
                max-width: 900px;
                margin: 0 auto;
                background: white;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                padding: 20px;
            }}
            h1 {{ margin-top: 0; color: #333; }}
            h2 {{ color: #555; margin-top: 24px; }}
            .station-info {{
                margin-bottom: 20px;
                padding: 15px;
                background: #f8f9fa;
                border-radius: 4px;
            }}
            .controls {{
                padding: 15px;
                background: #f8f9fa;
                border-radius: 4px;
                margin-bottom: 20px;
            }}
            .controls label {{
                font-weight: 600;
                margin-right: 8px;
            }}
            .controls select, .controls button {{
                padding: 8px 16px;
                font-size: 1em;
                border-radius: 4px;
                border: 1px solid #ccc;
                margin-right: 8px;
            }}
            .controls button {{
                cursor: pointer;
                color: white;
                border: none;
            }}
            .btn-start {{
                background: #28a745;
            }}
            .btn-start:hover {{
                background: #218838;
            }}
            .btn-start:disabled {{
                background: #6c757d;
                cursor: not-allowed;
            }}
            .btn-stop {{
                background: #dc3545;
            }}
            .btn-stop:hover {{
                background: #c82333;
            }}
            .btn-stop:disabled {{
                background: #6c757d;
                cursor: not-allowed;
            }}
            .mode-group {{
                display: inline-flex;
                gap: 12px;
                margin: 0 12px;
            }}
            .mode-group label {{
                font-weight: normal;
                cursor: pointer;
            }}
            .status-panel {{
                padding: 15px;
                border-radius: 4px;
                border: 2px solid #dee2e6;
                margin-bottom: 20px;
            }}
            .status-panel.running {{
                border-color: #28a745;
            }}
            .status-panel.stopping {{
                border-color: #ffc107;
            }}
            .status-grid {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 8px;
            }}
            .status-grid .label {{
                font-weight: 600;
                color: #555;
            }}
            .message {{
                margin-top: 12px;
                padding: 10px;
                background: #f8f9fa;
                border-radius: 4px;
                font-family: monospace;
                font-size: 0.95em;
                word-break: break-word;
            }}
            .api-links {{
                margin-top: 20px;
                padding-top: 20px;
                border-top: 1px solid #dee2e6;
            }}
            .api-links a {{
                margin-right: 15px;
                color: #007bff;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>hwtest Runner</h1>

            <div class="station-info">
                <p><strong>Station:</strong> {station.id}</p>
                <p><strong>Description:</strong> {station.description or "(none)"}</p>
                <p><strong>Rack:</strong>
                    <span style="color: {rack_state_color}; font-weight: bold;">
                        {rack.state.upper()}
                    </span>
                </p>
                <p><strong>UUT:</strong> {station.uut.url}</p>
            </div>

            <h2>Run Test</h2>
            <div class="controls">
                <label for="test-case">Test Case:</label>
                <select id="test-case" onchange="updateModes()">
                    {tc_options}
                </select>

                <span class="mode-group" id="mode-group"></span>

                <button class="btn-start" id="btn-start" onclick="startTest()">Start</button>
                <button class="btn-stop" id="btn-stop" onclick="stopTest()" disabled>Stop</button>
            </div>

            <h2>Status</h2>
            <div class="status-panel" id="status-panel">
                <div class="status-grid">
                    <span class="label">State:</span>
                    <span id="st-state" style="color: {run_state_color}; font-weight: bold;">
                        {run.state.value.upper()}
                    </span>

                    <span class="label">Test Case:</span>
                    <span id="st-test-case">{run.test_case_id or "-"}</span>

                    <span class="label">Mode:</span>
                    <span id="st-mode">{run.mode or "-"}</span>

                    <span class="label">Current State:</span>
                    <span id="st-current-state">{run.current_state or "-"}</span>

                    <span class="label">Cycle:</span>
                    <span id="st-cycle">{run.cycle}</span>

                    <span class="label">Passes / Failures:</span>
                    <span id="st-stats">
                        {run.stats.get("passes", 0)} / {run.stats.get("failures", 0)}
                    </span>

                    <span class="label">Started:</span>
                    <span id="st-started">{run.started_at or "-"}</span>
                </div>
                <div class="message" id="st-message">{run.message}</div>
            </div>

            <div class="api-links">
                <strong>API:</strong>
                <a href="/health">/health</a>
                <a href="/status">/status</a>
                <a href="/test-cases">/test-cases</a>
                <a href="/run/status">/run/status</a>
                <a href="/docs">/docs</a>
            </div>
        </div>

        <script>
            function updateModes() {{
                const sel = document.getElementById('test-case');
                const opt = sel.options[sel.selectedIndex];
                const modes = (opt.dataset.modes || 'functional').split(',');
                const group = document.getElementById('mode-group');
                group.innerHTML = '';
                modes.forEach(function(m, i) {{
                    const id = 'mode-' + m;
                    const radio = document.createElement('input');
                    radio.type = 'radio';
                    radio.name = 'mode';
                    radio.value = m;
                    radio.id = id;
                    if (i === 0) radio.checked = true;
                    const label = document.createElement('label');
                    label.htmlFor = id;
                    label.textContent = m.toUpperCase();
                    label.prepend(radio);
                    group.appendChild(label);
                }});
            }}

            function getSelectedMode() {{
                const checked = document.querySelector('input[name="mode"]:checked');
                return checked ? checked.value : 'functional';
            }}

            async function startTest() {{
                const testCaseId = document.getElementById('test-case').value;
                const mode = getSelectedMode();
                try {{
                    const resp = await fetch('/run', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{test_case_id: testCaseId, mode: mode}})
                    }});
                    if (!resp.ok) {{
                        const err = await resp.json();
                        alert('Error: ' + (err.detail || resp.statusText));
                    }}
                }} catch(e) {{
                    alert('Request failed: ' + e);
                }}
            }}

            async function stopTest() {{
                try {{
                    await fetch('/stop', {{method: 'POST'}});
                }} catch(e) {{
                    alert('Request failed: ' + e);
                }}
            }}

            function updateStatus(data) {{
                const stateColors = {{idle: '#6c757d', running: '#28a745', stopping: '#ffc107'}};
                const el = function(id) {{ return document.getElementById(id); }};

                el('st-state').textContent = data.state.toUpperCase();
                el('st-state').style.color = stateColors[data.state] || '#6c757d';
                el('st-test-case').textContent = data.test_case_id || '-';
                el('st-mode').textContent = data.mode ? data.mode.toUpperCase() : '-';
                el('st-current-state').textContent = data.current_state || '-';
                el('st-cycle').textContent = data.cycle;
                el('st-stats').textContent = (data.stats.passes || 0) + ' / ' + (data.stats.failures || 0);
                el('st-started').textContent = data.started_at || '-';
                el('st-message').textContent = data.message;

                const panel = el('status-panel');
                panel.className = 'status-panel ' + data.state;

                const isRunning = data.state !== 'idle';
                el('btn-start').disabled = isRunning;
                el('btn-stop').disabled = !isRunning;
                el('test-case').disabled = isRunning;
                document.querySelectorAll('input[name="mode"]').forEach(function(r) {{
                    r.disabled = isRunning;
                }});
            }}

            // Poll for status updates
            setInterval(async function() {{
                try {{
                    const resp = await fetch('/run/status');
                    if (resp.ok) {{
                        const data = await resp.json();
                        updateStatus(data);
                    }}
                }} catch(e) {{
                    // ignore fetch errors
                }}
            }}, 1000);

            // Initialize modes on page load
            updateModes();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


# =============================================================================
# CLI Entry Point
# =============================================================================


def main() -> None:
    """Command-line entry point for the runner server."""
    parser = argparse.ArgumentParser(description="Start the hwtest runner server")
    parser.add_argument(
        "config",
        type=Path,
        help="Path to station configuration YAML file",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on (default: 8000)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.config.exists():
        logger.error("Config file not found: %s", args.config)
        sys.exit(1)

    import uvicorn  # pylint: disable=import-outside-toplevel

    app = create_app(args.config)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
