"""FastAPI server for test rack REST API."""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from hwtest_rack.config import load_config
from hwtest_rack.models import HealthResponse, InstrumentStatus, RackStatus
from hwtest_rack.rack import Rack

logger = logging.getLogger(__name__)

# Global rack instance (set during lifespan)
_rack: Rack | None = None


def _get_rack() -> Rack:
    """Get the global rack instance."""
    if _rack is None:
        raise RuntimeError("Rack not initialized")
    return _rack


def create_app(config_path: str | Path | None = None) -> FastAPI:
    """Create a FastAPI application.

    Args:
        config_path: Path to rack configuration YAML.
            If None, config must be set via environment or startup.

    Returns:
        Configured FastAPI application.
    """
    # Store config path in app state for lifespan access
    app_state: dict[str, Any] = {"config_path": config_path}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Application lifespan handler."""
        global _rack  # pylint: disable=global-statement

        cfg_path = app_state.get("config_path")
        if cfg_path:
            logger.info("Loading rack configuration from %s", cfg_path)
            config = load_config(cfg_path)
            _rack = Rack(config)
            _rack.initialize()
            logger.info(
                "Rack '%s' initialized with %d instruments", _rack.rack_id, len(config.instruments)
            )

        yield

        if _rack is not None:
            logger.info("Shutting down rack '%s'", _rack.rack_id)
            _rack.close()
            _rack = None

    app = FastAPI(
        title="hwtest Rack API",
        description="REST API for hardware test rack status and instrument discovery",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Register routes
    app.add_api_route("/", _dashboard, methods=["GET"], response_class=HTMLResponse)
    app.add_api_route("/health", _health, methods=["GET"], response_model=HealthResponse)
    app.add_api_route("/status", _status, methods=["GET"], response_model=RackStatus)
    app.add_api_route(
        "/instruments", _list_instruments, methods=["GET"], response_model=list[InstrumentStatus]
    )
    app.add_api_route(
        "/instruments/{name}", _get_instrument, methods=["GET"], response_model=InstrumentStatus
    )

    return app


async def _dashboard() -> HTMLResponse:
    """HTML dashboard showing all instruments."""
    rack = _get_rack()
    status = rack.get_status()

    # Build instrument rows
    rows = []
    for inst in status.instruments:
        state_color = {
            "ready": "#28a745",
            "error": "#dc3545",
            "pending": "#6c757d",
            "initializing": "#ffc107",
            "closed": "#6c757d",
        }.get(inst.state.value, "#6c757d")

        identity_str = ""
        if inst.identity:
            identity_str = f"{inst.identity.manufacturer} {inst.identity.model}"
            if inst.identity.serial:
                identity_str += f" (S/N: {inst.identity.serial})"
        else:
            identity_str = f"Expected: {inst.expected_manufacturer} {inst.expected_model}"

        error_row = ""
        if inst.error:
            error_row = f'<tr><td colspan="4" style="color: #dc3545; padding-left: 2em;">Error: {inst.error}</td></tr>'

        rows.append(f"""
        <tr>
            <td><strong>{inst.name}</strong></td>
            <td><code>{inst.driver}</code></td>
            <td><span style="color: {state_color}; font-weight: bold;">{inst.state.value.upper()}</span></td>
            <td>{identity_str}</td>
        </tr>
        {error_row}
        """)

    rack_state_color = {
        "ready": "#28a745",
        "error": "#dc3545",
        "initializing": "#ffc107",
        "closed": "#6c757d",
    }.get(status.state, "#6c757d")

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{status.rack_id} - hwtest Rack</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                margin: 0;
                padding: 20px;
                background: #f5f5f5;
            }}
            .container {{
                max-width: 1200px;
                margin: 0 auto;
                background: white;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                padding: 20px;
            }}
            h1 {{
                margin-top: 0;
                color: #333;
            }}
            .rack-info {{
                margin-bottom: 20px;
                padding: 15px;
                background: #f8f9fa;
                border-radius: 4px;
            }}
            .rack-state {{
                font-size: 1.2em;
                font-weight: bold;
                color: {rack_state_color};
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
            }}
            th, td {{
                text-align: left;
                padding: 12px;
                border-bottom: 1px solid #dee2e6;
            }}
            th {{
                background: #f8f9fa;
                font-weight: 600;
            }}
            code {{
                background: #f1f1f1;
                padding: 2px 6px;
                border-radius: 3px;
                font-size: 0.9em;
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
            <h1>hwtest Rack Dashboard</h1>
            <div class="rack-info">
                <p><strong>Rack ID:</strong> {status.rack_id}</p>
                <p><strong>Description:</strong> {status.description or "(none)"}</p>
                <p><strong>State:</strong> <span class="rack-state">{status.state.upper()}</span></p>
            </div>

            <h2>Instruments ({len(status.instruments)})</h2>
            <table>
                <thead>
                    <tr>
                        <th>Name</th>
                        <th>Driver</th>
                        <th>State</th>
                        <th>Identity</th>
                    </tr>
                </thead>
                <tbody>
                    {"".join(rows) if rows else "<tr><td colspan='4'>No instruments configured</td></tr>"}
                </tbody>
            </table>

            <div class="api-links">
                <strong>API Endpoints:</strong>
                <a href="/health">/health</a>
                <a href="/status">/status</a>
                <a href="/instruments">/instruments</a>
                <a href="/docs">/docs (OpenAPI)</a>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


async def _health() -> HealthResponse:
    """Health check endpoint."""
    rack = _get_rack()
    return HealthResponse(
        status="ok" if rack.state == "ready" else rack.state,
        rack_id=rack.rack_id,
    )


async def _status() -> RackStatus:
    """Get full rack status."""
    rack = _get_rack()
    return rack.get_status()


async def _list_instruments() -> list[InstrumentStatus]:
    """List all instruments."""
    rack = _get_rack()
    return rack.list_instruments()


async def _get_instrument(name: str) -> InstrumentStatus:
    """Get a specific instrument's status."""
    rack = _get_rack()
    status = rack.get_instrument_status(name)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Instrument '{name}' not found")
    return status


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Start the hwtest rack server")
    parser.add_argument(
        "config",
        type=Path,
        help="Path to rack configuration YAML file",
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
