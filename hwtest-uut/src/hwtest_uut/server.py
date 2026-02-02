"""FastAPI REST API server for the UUT simulator."""

from __future__ import annotations

import argparse
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from hwtest_uut.can_interface import CanMessage
from hwtest_uut.mcp23017 import PinDirection
from hwtest_uut.models import (
    AdcChannelResponse,
    AdcStatusResponse,
    CanEchoConfig,
    CanMessageModel,
    CanSendRequest,
    DacChannelResponse,
    DacStatusResponse,
    DacWriteBothRequest,
    DacWriteRequest,
    ErrorResponse,
    GpioPinConfig,
    GpioPinResponse,
    GpioPinWriteRequest,
    GpioPortResponse,
    GpioPortWriteRequest,
    GpioStatusResponse,
    GpioWriteAllRequest,
    HealthResponse,
    StatusResponse,
)
from hwtest_uut.simulator import SimulatorConfig, UutSimulator

logger = logging.getLogger(__name__)

__version__ = "0.1.0"

# Global simulator instance
_simulator: UutSimulator | None = None
_run_task: asyncio.Task[None] | None = None


def get_simulator() -> UutSimulator:
    """Get the simulator instance.

    Raises:
        RuntimeError: If simulator not initialized.
    """
    if _simulator is None:
        raise RuntimeError("Simulator not initialized")
    return _simulator


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler."""
    global _simulator, _run_task

    # Get config from app state
    config: SimulatorConfig = getattr(app.state, "config", SimulatorConfig())

    _simulator = UutSimulator(config=config)
    _simulator.start()

    # Start async receive loop
    _run_task = asyncio.create_task(_simulator.run())

    logger.info("UUT simulator server started")
    yield

    # Shutdown
    if _simulator is not None:
        _simulator.stop()
    if _run_task is not None:
        _run_task.cancel()
        try:
            await _run_task
        except asyncio.CancelledError:
            pass

    logger.info("UUT simulator server stopped")


app = FastAPI(
    title="UUT Simulator",
    description="Unit Under Test simulator for hardware integration testing",
    version=__version__,
    lifespan=lifespan,
)


# -----------------------------------------------------------------------------
# Health and Status
# -----------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def get_dashboard() -> str:
    """Return HTML dashboard."""
    sim = get_simulator()
    cfg = sim.config

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>UUT Simulator</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; margin: 40px; }}
        h1 {{ color: #333; }}
        .status {{ padding: 10px; margin: 10px 0; border-radius: 5px; }}
        .enabled {{ background: #d4edda; }}
        .disabled {{ background: #f8d7da; }}
        table {{ border-collapse: collapse; margin: 20px 0; }}
        td, th {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background: #f4f4f4; }}
    </style>
</head>
<body>
    <h1>UUT Simulator</h1>
    <p>Version: {__version__} | Uptime: {sim.uptime:.1f}s</p>

    <h2>Interfaces</h2>
    <table>
        <tr><th>Interface</th><th>Status</th><th>Details</th></tr>
        <tr>
            <td>CAN Bus</td>
            <td class="{'enabled' if cfg.can_enabled else 'disabled'}">
                {'Enabled' if cfg.can_enabled else 'Disabled'}
            </td>
            <td>{cfg.can_interface} @ {cfg.can_bitrate} bps</td>
        </tr>
        <tr>
            <td>DAC</td>
            <td class="{'enabled' if cfg.dac_enabled else 'disabled'}">
                {'Enabled' if cfg.dac_enabled else 'Disabled'}
            </td>
            <td>Vref: {cfg.dac_vref}V</td>
        </tr>
        <tr>
            <td>ADC</td>
            <td class="{'enabled' if cfg.adc_enabled else 'disabled'}">
                {'Enabled' if cfg.adc_enabled else 'Disabled'}
            </td>
            <td>8 channels</td>
        </tr>
        <tr>
            <td>GPIO</td>
            <td class="{'enabled' if cfg.gpio_enabled else 'disabled'}">
                {'Enabled' if cfg.gpio_enabled else 'Disabled'}
            </td>
            <td>MCP23017 @ 0x{cfg.gpio_address:02X}</td>
        </tr>
    </table>

    <h2>API Endpoints</h2>
    <ul>
        <li><a href="/docs">/docs</a> - OpenAPI documentation</li>
        <li><a href="/health">/health</a> - Health check</li>
        <li><a href="/status">/status</a> - Full status</li>
    </ul>
</body>
</html>"""
    return html


@app.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    """Health check endpoint."""
    sim = get_simulator()
    return HealthResponse(
        status="healthy" if sim.is_running else "unhealthy",
        version=__version__,
        uptime_seconds=sim.uptime,
    )


@app.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """Get full simulator status."""
    sim = get_simulator()
    cfg = sim.config
    return StatusResponse(
        can_enabled=cfg.can_enabled,
        can_interface=cfg.can_interface,
        dac_enabled=cfg.dac_enabled,
        gpio_enabled=cfg.gpio_enabled,
        gpio_address=cfg.gpio_address,
        adc_enabled=cfg.adc_enabled,
    )


# -----------------------------------------------------------------------------
# CAN Endpoints
# -----------------------------------------------------------------------------


@app.post("/can/send", responses={500: {"model": ErrorResponse}})
async def can_send(request: CanSendRequest) -> dict[str, str]:
    """Send a CAN message."""
    sim = get_simulator()
    try:
        msg = CanMessage(
            arbitration_id=request.message.arbitration_id,
            data=bytes(request.message.data),
            is_extended_id=request.message.is_extended_id,
            is_fd=request.message.is_fd,
        )
        sim.can_send(msg)
        return {"status": "sent"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/can/received", response_model=list[CanMessageModel])
async def can_get_received() -> list[CanMessageModel]:
    """Get received CAN messages."""
    sim = get_simulator()
    messages = sim.can_get_received()
    return [
        CanMessageModel(
            arbitration_id=m.arbitration_id,
            data=list(m.data),
            is_extended_id=m.is_extended_id,
            is_fd=m.is_fd,
        )
        for m in messages
    ]


@app.delete("/can/received")
async def can_clear_received() -> dict[str, str]:
    """Clear received CAN message buffer."""
    sim = get_simulator()
    sim.can_clear_received()
    return {"status": "cleared"}


@app.get("/can/echo", response_model=CanEchoConfig)
async def can_get_echo() -> CanEchoConfig:
    """Get CAN echo configuration."""
    sim = get_simulator()
    state = sim.can_get_echo_config()
    return CanEchoConfig(
        enabled=state.enabled,
        id_offset=state.id_offset,
        filter_ids=state.filter_ids if state.filter_ids else None,
    )


@app.put("/can/echo")
async def can_set_echo(config: CanEchoConfig) -> dict[str, str]:
    """Configure CAN echo mode."""
    sim = get_simulator()
    sim.can_set_echo(
        enabled=config.enabled,
        id_offset=config.id_offset,
        filter_ids=config.filter_ids,
    )
    return {"status": "configured"}


# -----------------------------------------------------------------------------
# DAC Endpoints
# -----------------------------------------------------------------------------


@app.post("/dac/write", responses={400: {"model": ErrorResponse}})
async def dac_write(request: DacWriteRequest) -> dict[str, str]:
    """Write voltage to a DAC channel."""
    sim = get_simulator()
    try:
        sim.dac_write(request.channel, request.voltage)
        return {"status": "written"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/dac/write-both", responses={400: {"model": ErrorResponse}})
async def dac_write_both(request: DacWriteBothRequest) -> dict[str, str]:
    """Write voltage to both DAC channels."""
    sim = get_simulator()
    try:
        sim.dac_write_both(request.voltage_a, request.voltage_b)
        return {"status": "written"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/dac/status", response_model=DacStatusResponse)
async def dac_get_status() -> DacStatusResponse:
    """Get current DAC channel voltages."""
    sim = get_simulator()
    voltages = sim.dac_read_all()
    return DacStatusResponse(
        channels=[
            DacChannelResponse(channel=0, voltage=voltages[0]),
            DacChannelResponse(channel=1, voltage=voltages[1]),
        ]
    )


@app.get(
    "/dac/{channel}",
    response_model=DacChannelResponse,
    responses={400: {"model": ErrorResponse}},
)
async def dac_get_channel(channel: int) -> DacChannelResponse:
    """Get a DAC channel voltage."""
    sim = get_simulator()
    try:
        voltage = sim.dac_read(channel)
        return DacChannelResponse(channel=channel, voltage=voltage)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# -----------------------------------------------------------------------------
# ADC Endpoints
# -----------------------------------------------------------------------------


@app.get(
    "/adc/status",
    response_model=AdcStatusResponse,
    responses={500: {"model": ErrorResponse}},
)
async def adc_get_status() -> AdcStatusResponse:
    """Read all ADC channels."""
    sim = get_simulator()
    try:
        voltages = sim.adc_read_all()
        return AdcStatusResponse(
            channels=[
                AdcChannelResponse(channel=i, voltage=v, raw=0)
                for i, v in enumerate(voltages)
            ]
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get(
    "/adc/{channel}",
    response_model=AdcChannelResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def adc_get_channel(channel: int) -> AdcChannelResponse:
    """Read an ADC channel."""
    sim = get_simulator()
    try:
        voltage = sim.adc_read(channel)
        return AdcChannelResponse(channel=channel, voltage=voltage, raw=0)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# -----------------------------------------------------------------------------
# GPIO Endpoints
# -----------------------------------------------------------------------------


@app.get(
    "/gpio/status",
    response_model=GpioStatusResponse,
    responses={500: {"model": ErrorResponse}},
)
async def gpio_get_status() -> GpioStatusResponse:
    """Get all GPIO pin states."""
    sim = get_simulator()
    try:
        port_a = sim.gpio_read_port("A")
        port_b = sim.gpio_read_port("B")
        return GpioStatusResponse(
            port_a=GpioPortResponse(port="A", value=port_a, direction_mask=0xFF),
            port_b=GpioPortResponse(port="B", value=port_b, direction_mask=0xFF),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post(
    "/gpio/configure",
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def gpio_configure_pin(config: GpioPinConfig) -> dict[str, str]:
    """Configure a GPIO pin direction and pull-up."""
    sim = get_simulator()
    try:
        direction = PinDirection.INPUT if config.direction == "input" else PinDirection.OUTPUT
        sim.gpio_set_direction(config.pin, direction)
        sim.gpio_set_pullup(config.pin, config.pullup)
        return {"status": "configured"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post(
    "/gpio/write",
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def gpio_write_pin(request: GpioPinWriteRequest) -> dict[str, str]:
    """Write a GPIO pin value."""
    sim = get_simulator()
    try:
        sim.gpio_write(request.pin, request.value)
        return {"status": "written"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post(
    "/gpio/write-port",
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def gpio_write_port(request: GpioPortWriteRequest) -> dict[str, str]:
    """Write all pins on a GPIO port."""
    sim = get_simulator()
    try:
        sim.gpio_write_port(request.port, request.value)
        return {"status": "written"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post(
    "/gpio/write-all",
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def gpio_write_all(request: GpioWriteAllRequest) -> dict[str, str]:
    """Write all GPIO pins."""
    sim = get_simulator()
    try:
        sim.gpio_write_all(request.value)
        return {"status": "written"}
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get(
    "/gpio/{pin}",
    response_model=GpioPinResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def gpio_read_pin(pin: int) -> GpioPinResponse:
    """Read a GPIO pin value."""
    sim = get_simulator()
    try:
        value = sim.gpio_read(pin)
        return GpioPinResponse(pin=pin, value=value, direction="input")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# -----------------------------------------------------------------------------
# CLI Entry Point
# -----------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="UUT Simulator Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on")
    parser.add_argument("--can-interface", default="can0", help="CAN interface name")
    parser.add_argument("--can-bitrate", type=int, default=500000, help="CAN bitrate")
    parser.add_argument("--no-can", action="store_true", help="Disable CAN interface")
    parser.add_argument("--no-dac", action="store_true", help="Disable DAC")
    parser.add_argument("--no-adc", action="store_true", help="Disable ADC")
    parser.add_argument("--no-gpio", action="store_true", help="Disable GPIO expander")
    parser.add_argument(
        "--gpio-address", type=lambda x: int(x, 0), default=0x20, help="GPIO I2C address"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = SimulatorConfig(
        can_enabled=not args.no_can,
        can_interface=args.can_interface,
        can_bitrate=args.can_bitrate,
        dac_enabled=not args.no_dac,
        adc_enabled=not args.no_adc,
        gpio_enabled=not args.no_gpio,
        gpio_address=args.gpio_address,
    )

    app.state.config = config

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
