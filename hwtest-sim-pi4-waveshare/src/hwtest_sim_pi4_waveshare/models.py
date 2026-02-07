"""Pydantic models for the UUT simulator REST API.

This module defines the request and response models used by the FastAPI
REST API endpoints. All models use Pydantic for validation and serialization.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Health check response.

    Attributes:
        status: Health status ("healthy", "degraded", or "unhealthy").
        version: Server version string.
        uptime_seconds: Time since server start in seconds.
    """

    status: Literal["healthy", "degraded", "unhealthy"]
    version: str
    uptime_seconds: float


class StatusResponse(BaseModel):
    """Full simulator status response.

    Attributes:
        can_enabled: Whether CAN interface is enabled.
        can_interface: Name of the CAN interface (e.g., "can0").
        dac_enabled: Whether DAC output is enabled.
        gpio_enabled: Whether GPIO expander is enabled.
        gpio_address: I2C address of GPIO expander.
        adc_enabled: Whether ADC input is enabled.
    """

    can_enabled: bool
    can_interface: str
    dac_enabled: bool
    gpio_enabled: bool
    gpio_address: int
    adc_enabled: bool


class CanMessageModel(BaseModel):
    """CAN message request/response model.

    Attributes:
        arbitration_id: CAN arbitration ID (0 to 0x1FFFFFFF).
        data: Message data as list of bytes (0-64 bytes).
        is_extended_id: True for 29-bit extended ID.
        is_fd: True for CAN FD frame.
    """

    arbitration_id: int = Field(..., ge=0, le=0x1FFFFFFF)
    data: list[int] = Field(default_factory=list, max_length=64)
    is_extended_id: bool = False
    is_fd: bool = False


class CanSendRequest(BaseModel):
    """Request to send a CAN message.

    Attributes:
        message: The CAN message to send.
    """

    message: CanMessageModel


class CanEchoConfig(BaseModel):
    """Configuration for CAN echo mode.

    Attributes:
        enabled: Enable or disable echo mode.
        id_offset: Offset to add to echoed message IDs (-0x7FF to 0x7FF).
        filter_ids: Only echo messages with these IDs (None for all).
    """

    enabled: bool
    id_offset: int = Field(default=0, ge=-0x7FF, le=0x7FF)
    filter_ids: list[int] | None = None


class CanHeartbeatStatus(BaseModel):
    """Status of the CAN heartbeat.

    Attributes:
        running: True if heartbeat is being transmitted.
        message_count: Total heartbeat messages sent.
        arbitration_id: CAN ID used for heartbeat messages.
        interval_ms: Interval between heartbeats in milliseconds.
    """

    running: bool
    message_count: int
    arbitration_id: int
    interval_ms: int


class DacWriteRequest(BaseModel):
    """Request to write DAC voltage.

    Attributes:
        channel: DAC channel (0 or 1).
        voltage: Voltage to output (0.0 to 5.0V).
    """

    channel: int = Field(..., ge=0, le=1)
    voltage: float = Field(..., ge=0.0, le=5.0)


class DacWriteBothRequest(BaseModel):
    """Request to write both DAC channels.

    Attributes:
        voltage_a: Voltage for channel A (0.0 to 5.0V).
        voltage_b: Voltage for channel B (0.0 to 5.0V).
    """

    voltage_a: float = Field(..., ge=0.0, le=5.0)
    voltage_b: float = Field(..., ge=0.0, le=5.0)


class DacChannelResponse(BaseModel):
    """Response for DAC channel state.

    Attributes:
        channel: DAC channel number.
        voltage: Current voltage output.
    """

    channel: int
    voltage: float


class DacStatusResponse(BaseModel):
    """Response for all DAC channels.

    Attributes:
        channels: List of channel states.
    """

    channels: list[DacChannelResponse]


class GpioPinConfig(BaseModel):
    """Configuration for a single GPIO pin.

    Attributes:
        pin: Pin number (0-15).
        direction: Pin direction ("input" or "output").
        pullup: Enable internal pull-up resistor.
    """

    pin: int = Field(..., ge=0, le=15)
    direction: Literal["input", "output"]
    pullup: bool = False


class GpioPinWriteRequest(BaseModel):
    """Request to write a GPIO pin value.

    Attributes:
        pin: Pin number (0-15).
        value: True for high, False for low.
    """

    pin: int = Field(..., ge=0, le=15)
    value: bool


class GpioPortWriteRequest(BaseModel):
    """Request to write a GPIO port value.

    Attributes:
        port: Port name ("A" or "B").
        value: 8-bit value to write (0-255).
    """

    port: Literal["A", "B"]
    value: int = Field(..., ge=0, le=255)


class GpioWriteAllRequest(BaseModel):
    """Request to write all GPIO pins.

    Attributes:
        value: 16-bit value (bits 0-7 = port A, bits 8-15 = port B).
    """

    value: int = Field(..., ge=0, le=65535)


class GpioPinResponse(BaseModel):
    """Response for a single GPIO pin.

    Attributes:
        pin: Pin number.
        value: Pin state (True = high, False = low).
        direction: Pin direction.
    """

    pin: int
    value: bool
    direction: Literal["input", "output"]


class GpioPortResponse(BaseModel):
    """Response for a GPIO port.

    Attributes:
        port: Port name ("A" or "B").
        value: 8-bit port value.
        direction_mask: 8-bit direction mask (1 = input, 0 = output).
    """

    port: Literal["A", "B"]
    value: int
    direction_mask: int


class GpioStatusResponse(BaseModel):
    """Response for all GPIO pins.

    Attributes:
        port_a: Port A status.
        port_b: Port B status.
    """

    port_a: GpioPortResponse
    port_b: GpioPortResponse


class AdcChannelResponse(BaseModel):
    """Response for an ADC channel reading.

    Attributes:
        channel: ADC channel number.
        voltage: Measured voltage in volts.
        raw: Raw ADC reading.
    """

    channel: int
    voltage: float
    raw: int


class AdcStatusResponse(BaseModel):
    """Response for all ADC channels.

    Attributes:
        channels: List of channel readings.
    """

    channels: list[AdcChannelResponse]


class ErrorResponse(BaseModel):
    """Error response model.

    Attributes:
        error: Error message.
        detail: Additional error details (optional).
    """

    error: str
    detail: str | None = None


class FailureStatusResponse(BaseModel):
    """Response for failure injection status.

    Attributes:
        enabled: Whether failure injection is enabled.
        delay_seconds: Delay before failure activates.
        duration_seconds: How long failure stays active.
        voltage_offset: Voltage offset applied during failure.
        active: Whether failure is currently active.
        cycle_count: Number of complete failure cycles.
        time_until_active: Seconds until failure activates (None if N/A).
    """

    enabled: bool
    delay_seconds: float
    duration_seconds: float
    voltage_offset: float
    active: bool
    cycle_count: int
    time_until_active: float | None


class FailureConfigRequest(BaseModel):
    """Request to configure failure injection.

    Attributes:
        delay_seconds: New delay value (None to keep current).
        duration_seconds: New duration value (None to keep current).
        voltage_offset: New offset value (None to keep current).
    """

    delay_seconds: float | None = Field(default=None, ge=0.0)
    duration_seconds: float | None = Field(default=None, ge=0.0)
    voltage_offset: float | None = Field(default=None, ge=0.0, le=5.0)
