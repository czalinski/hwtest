"""FastAPI REST API server for the UUT simulator.

This module provides a REST API for controlling the UUT simulator remotely.
The server exposes endpoints for CAN communication, DAC/ADC operations,
GPIO control, and failure injection.

The server can be run directly via the command line or imported and
configured programmatically.

Example:
    Command line usage::

        $ uut-simulator --port 8080 --can-interface can0

    Programmatic usage::

        >>> from hwtest_sim_pi4_waveshare.server import app
        >>> import uvicorn
        >>> uvicorn.run(app, host="0.0.0.0", port=8080)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from hwtest_sim_pi4_waveshare.can_interface import CanMessage
from hwtest_sim_pi4_waveshare.mcp23017 import PinDirection
from hwtest_sim_pi4_waveshare.models import (
    AdcChannelResponse,
    AdcStatusResponse,
    CanEchoConfig,
    CanHeartbeatStatus,
    CanMessageModel,
    CanSendRequest,
    DacChannelResponse,
    DacStatusResponse,
    DacWriteBothRequest,
    DacWriteRequest,
    ErrorResponse,
    FailureConfigRequest,
    FailureStatusResponse,
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
from hwtest_sim_pi4_waveshare.simulator import SimulatorConfig, UutSimulator

logger = logging.getLogger(__name__)

__version__ = "0.1.0"

# Global simulator instance
_simulator: UutSimulator | None = None
_run_task: asyncio.Task[None] | None = None


def get_simulator() -> UutSimulator:
    """Get the global simulator instance.

    Returns:
        The UUT simulator instance.

    Raises:
        RuntimeError: If simulator has not been initialized.
    """
    if _simulator is None:
        raise RuntimeError("Simulator not initialized")
    return _simulator


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan context manager.

    Handles simulator initialization on startup and cleanup on shutdown.
    Configures the simulator based on settings stored in app.state.

    Args:
        app: The FastAPI application instance.

    Yields:
        None during the application's lifetime.
    """
    global _simulator, _run_task

    # Get config from app state
    config: SimulatorConfig = getattr(app.state, "config", SimulatorConfig())
    use_waveshare_adda: bool = getattr(app.state, "waveshare_adda", False)

    # Initialize ADC/DAC if using Waveshare High-Precision AD/DA board
    dac = None
    adc = None
    if use_waveshare_adda:
        try:
            from hwtest_waveshare import Ads1256, Dac8532

            if config.dac_enabled:
                dac = Dac8532()
                logger.info("Waveshare DAC8532 initialized")
            if config.adc_enabled:
                adc = Ads1256()
                logger.info("Waveshare ADS1256 initialized")
        except ImportError:
            logger.warning("hwtest-waveshare not installed, AD/DA disabled")
        except Exception as exc:
            logger.warning("Failed to initialize Waveshare AD/DA: %s", exc)

    _simulator = UutSimulator(config=config, dac=dac, adc=adc)
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

    # Close ADC/DAC
    if adc is not None:
        try:
            adc.close()
        except Exception:
            pass
    if dac is not None:
        try:
            dac.close()
        except Exception:
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
    """Return HTML dashboard with simulator status.

    Returns:
        HTML page showing interface status and API links.
    """
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
    """Health check endpoint.

    Returns:
        Health status including version and uptime.
    """
    sim = get_simulator()
    return HealthResponse(
        status="healthy" if sim.is_running else "unhealthy",
        version=__version__,
        uptime_seconds=sim.uptime,
    )


@app.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """Get full simulator status.

    Returns:
        Status of all enabled interfaces and their configuration.
    """
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
    """Send a CAN message.

    Args:
        request: CAN message to send.

    Returns:
        Status confirmation.

    Raises:
        HTTPException: If CAN interface is not available (500).
    """
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
    """Get received CAN messages.

    Returns:
        List of CAN messages received since last clear.
    """
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
    """Clear received CAN message buffer.

    Returns:
        Status confirmation.
    """
    sim = get_simulator()
    sim.can_clear_received()
    return {"status": "cleared"}


@app.get("/can/echo", response_model=CanEchoConfig)
async def can_get_echo() -> CanEchoConfig:
    """Get CAN echo configuration.

    Returns:
        Current echo mode settings.
    """
    sim = get_simulator()
    state = sim.can_get_echo_config()
    return CanEchoConfig(
        enabled=state.enabled,
        id_offset=state.id_offset,
        filter_ids=state.filter_ids if state.filter_ids else None,
    )


@app.put("/can/echo")
async def can_set_echo(config: CanEchoConfig) -> dict[str, str]:
    """Configure CAN echo mode.

    Args:
        config: Echo mode configuration.

    Returns:
        Status confirmation.
    """
    sim = get_simulator()
    sim.can_set_echo(
        enabled=config.enabled,
        id_offset=config.id_offset,
        filter_ids=config.filter_ids,
    )
    return {"status": "configured"}


@app.get("/can/heartbeat", response_model=CanHeartbeatStatus)
async def can_get_heartbeat() -> CanHeartbeatStatus:
    """Get CAN heartbeat status.

    Returns:
        Current heartbeat state and statistics.
    """
    sim = get_simulator()
    state = sim.can_get_heartbeat_state()
    return CanHeartbeatStatus(
        running=state.running,
        message_count=state.message_count,
        arbitration_id=state.arbitration_id,
        interval_ms=state.interval_ms,
    )


# -----------------------------------------------------------------------------
# DAC Endpoints
# -----------------------------------------------------------------------------


@app.post("/dac/write", responses={400: {"model": ErrorResponse}})
async def dac_write(request: DacWriteRequest) -> dict[str, str]:
    """Write voltage to a DAC channel.

    Args:
        request: Channel and voltage to write.

    Returns:
        Status confirmation.

    Raises:
        HTTPException: If channel or voltage is invalid (400).
    """
    sim = get_simulator()
    try:
        sim.dac_write(request.channel, request.voltage)
        return {"status": "written"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/dac/write-both", responses={400: {"model": ErrorResponse}})
async def dac_write_both(request: DacWriteBothRequest) -> dict[str, str]:
    """Write voltage to both DAC channels.

    Args:
        request: Voltages for both channels.

    Returns:
        Status confirmation.

    Raises:
        HTTPException: If voltage is invalid (400).
    """
    sim = get_simulator()
    try:
        sim.dac_write_both(request.voltage_a, request.voltage_b)
        return {"status": "written"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/dac/status", response_model=DacStatusResponse)
async def dac_get_status() -> DacStatusResponse:
    """Get current DAC channel voltages.

    Returns:
        Status of all DAC channels.
    """
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
    """Get a DAC channel voltage.

    Args:
        channel: Channel number (0 or 1).

    Returns:
        Channel voltage state.

    Raises:
        HTTPException: If channel is invalid (400).
    """
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
    """Read all ADC channels.

    Returns:
        Voltage readings from all ADC channels.

    Raises:
        HTTPException: If ADC is not available (500).
    """
    sim = get_simulator()
    try:
        voltages = sim.adc_read_all()
        return AdcStatusResponse(
            channels=[
                AdcChannelResponse(channel=i, voltage=v, raw=0) for i, v in enumerate(voltages)
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
    """Read an ADC channel.

    Args:
        channel: Channel number (0-7).

    Returns:
        Voltage reading from the specified channel.

    Raises:
        HTTPException: If channel is invalid (400) or ADC not available (500).
    """
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
    """Get all GPIO pin states.

    Returns:
        Status of both GPIO ports (A and B).

    Raises:
        HTTPException: If GPIO is not available (500).
    """
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
    """Configure a GPIO pin direction and pull-up.

    Args:
        config: Pin configuration (pin number, direction, pull-up).

    Returns:
        Status confirmation.

    Raises:
        HTTPException: If pin is invalid (400) or GPIO not available (500).
    """
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
    """Write a GPIO pin value.

    Args:
        request: Pin number and value to write.

    Returns:
        Status confirmation.

    Raises:
        HTTPException: If pin is invalid (400) or GPIO not available (500).
    """
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
    """Write all pins on a GPIO port.

    Args:
        request: Port name and 8-bit value to write.

    Returns:
        Status confirmation.

    Raises:
        HTTPException: If port is invalid (400) or GPIO not available (500).
    """
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
    """Write all GPIO pins.

    Args:
        request: 16-bit value to write (bits 0-7 = port A, 8-15 = port B).

    Returns:
        Status confirmation.

    Raises:
        HTTPException: If GPIO is not available (500).
    """
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
    """Read a GPIO pin value.

    Args:
        pin: Pin number (0-15).

    Returns:
        Pin value and direction.

    Raises:
        HTTPException: If pin is invalid (400) or GPIO not available (500).
    """
    sim = get_simulator()
    try:
        value = sim.gpio_read(pin)
        return GpioPinResponse(pin=pin, value=value, direction="input")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# -----------------------------------------------------------------------------
# Failure Injection Endpoints
# -----------------------------------------------------------------------------


@app.get("/failure/status", response_model=FailureStatusResponse)
async def failure_get_status() -> FailureStatusResponse:
    """Get failure injection status.

    Returns:
        Current failure injection state and configuration.
    """
    sim = get_simulator()
    state = sim.failure_get_state()
    return FailureStatusResponse(
        enabled=state.enabled,
        delay_seconds=state.delay_seconds,
        duration_seconds=state.duration_seconds,
        voltage_offset=state.voltage_offset,
        active=state.active,
        cycle_count=state.cycle_count,
        time_until_active=sim.failure_time_until_active(),
    )


@app.put("/failure/config")
async def failure_configure(request: FailureConfigRequest) -> dict[str, str]:
    """Configure failure injection parameters.

    Args:
        request: New configuration values (None values keep current setting).

    Returns:
        Status confirmation.
    """
    sim = get_simulator()
    sim.failure_configure(
        delay_seconds=request.delay_seconds,
        duration_seconds=request.duration_seconds,
        voltage_offset=request.voltage_offset,
    )
    return {"status": "configured"}


@app.post("/failure/reset")
async def failure_reset() -> dict[str, str]:
    """Reset failure injection state (timer and active flag).

    Clears the start time and active flag, allowing the failure sequence
    to begin again on the next DAC write.

    Returns:
        Status confirmation.
    """
    sim = get_simulator()
    sim.failure_reset()
    return {"status": "reset"}


# -----------------------------------------------------------------------------
# CLI Entry Point
# -----------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse command line arguments.

    Returns:
        Parsed arguments namespace.
    """
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
    parser.add_argument(
        "--waveshare-adda",
        action="store_true",
        help="Use Waveshare High-Precision AD/DA board (ADS1256 + DAC8532)",
    )
    parser.add_argument(
        "--failure-delay",
        type=float,
        default=0.0,
        help="Failure injection delay in seconds (0 to disable)",
    )
    parser.add_argument(
        "--failure-duration",
        type=float,
        default=10.0,
        help="Failure duration in seconds before recovery (0 for permanent, default: 10)",
    )
    parser.add_argument(
        "--failure-offset",
        type=float,
        default=1.0,
        help="Failure injection voltage offset in volts (default: 1.0)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> None:
    """Main entry point for the UUT simulator server.

    Parses command line arguments, configures the simulator, and starts
    the uvicorn server.
    """
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
        failure_delay_seconds=args.failure_delay,
        failure_duration_seconds=args.failure_duration,
        failure_voltage_offset=args.failure_offset,
    )

    if args.failure_delay > 0:
        mode = "cyclic" if args.failure_duration > 0 else "permanent"
        logger.info(
            "Failure injection enabled: delay=%.1fs, duration=%.1fs (%s), offset=+%.2fV",
            args.failure_delay,
            args.failure_duration,
            mode,
            args.failure_offset,
        )

    app.state.config = config
    app.state.waveshare_adda = args.waveshare_adda

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
