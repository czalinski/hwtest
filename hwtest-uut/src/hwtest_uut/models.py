"""Pydantic models for the UUT simulator REST API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Health check response."""

    status: Literal["healthy", "degraded", "unhealthy"]
    version: str
    uptime_seconds: float


class StatusResponse(BaseModel):
    """Full status response."""

    can_enabled: bool
    can_interface: str
    dac_enabled: bool
    gpio_enabled: bool
    gpio_address: int
    adc_enabled: bool


class CanMessageModel(BaseModel):
    """CAN message request/response model."""

    arbitration_id: int = Field(..., ge=0, le=0x1FFFFFFF)
    data: list[int] = Field(default_factory=list, max_length=64)
    is_extended_id: bool = False
    is_fd: bool = False


class CanSendRequest(BaseModel):
    """Request to send a CAN message."""

    message: CanMessageModel


class CanEchoConfig(BaseModel):
    """Configuration for CAN echo mode."""

    enabled: bool
    id_offset: int = Field(default=0, ge=-0x7FF, le=0x7FF)
    filter_ids: list[int] | None = None


class DacWriteRequest(BaseModel):
    """Request to write DAC voltage."""

    channel: int = Field(..., ge=0, le=1)
    voltage: float = Field(..., ge=0.0, le=5.0)


class DacWriteBothRequest(BaseModel):
    """Request to write both DAC channels."""

    voltage_a: float = Field(..., ge=0.0, le=5.0)
    voltage_b: float = Field(..., ge=0.0, le=5.0)


class DacChannelResponse(BaseModel):
    """Response for DAC channel state."""

    channel: int
    voltage: float


class DacStatusResponse(BaseModel):
    """Response for all DAC channels."""

    channels: list[DacChannelResponse]


class GpioPinConfig(BaseModel):
    """Configuration for a single GPIO pin."""

    pin: int = Field(..., ge=0, le=15)
    direction: Literal["input", "output"]
    pullup: bool = False


class GpioPinWriteRequest(BaseModel):
    """Request to write a GPIO pin value."""

    pin: int = Field(..., ge=0, le=15)
    value: bool


class GpioPortWriteRequest(BaseModel):
    """Request to write a GPIO port value."""

    port: Literal["A", "B"]
    value: int = Field(..., ge=0, le=255)


class GpioWriteAllRequest(BaseModel):
    """Request to write all GPIO pins."""

    value: int = Field(..., ge=0, le=65535)


class GpioPinResponse(BaseModel):
    """Response for a single GPIO pin."""

    pin: int
    value: bool
    direction: Literal["input", "output"]


class GpioPortResponse(BaseModel):
    """Response for a GPIO port."""

    port: Literal["A", "B"]
    value: int
    direction_mask: int


class GpioStatusResponse(BaseModel):
    """Response for all GPIO pins."""

    port_a: GpioPortResponse
    port_b: GpioPortResponse


class AdcChannelResponse(BaseModel):
    """Response for an ADC channel reading."""

    channel: int
    voltage: float
    raw: int


class AdcStatusResponse(BaseModel):
    """Response for all ADC channels."""

    channels: list[AdcChannelResponse]


class ErrorResponse(BaseModel):
    """Error response model."""

    error: str
    detail: str | None = None
