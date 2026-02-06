"""Pydantic models for REST API responses.

This module defines the data models used by the test rack REST API for
serializing responses. All models use Pydantic for validation and
automatic JSON serialization.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class InstrumentState(str, Enum):
    """State of an instrument in the rack lifecycle.

    Instruments progress through states as follows:
        PENDING -> INITIALIZING -> READY (success)
                                -> ERROR (failure)
        READY/ERROR -> CLOSED (shutdown)

    Attributes:
        PENDING: Instrument is configured but not yet initialized.
        INITIALIZING: Driver is being loaded and instrument is being opened.
        READY: Instrument is initialized and identity verified.
        ERROR: Initialization failed or identity mismatch detected.
        CLOSED: Instrument has been shut down.
    """

    PENDING = "pending"
    INITIALIZING = "initializing"
    READY = "ready"
    ERROR = "error"
    CLOSED = "closed"


class IdentityModel(BaseModel):
    """Instrument identity information returned from the device.

    This model represents the response from an instrument's identity query
    (e.g., SCPI *IDN? command).

    Attributes:
        manufacturer: Instrument manufacturer name.
        model: Instrument model name or number.
        serial: Instrument serial number.
        firmware: Firmware version string.
    """

    manufacturer: str
    model: str
    serial: str
    firmware: str


class InstrumentStatus(BaseModel):
    """Status of a single instrument in the rack.

    Attributes:
        name: Unique instrument name within the rack.
        driver: Driver path in "module:function" format.
        state: Current lifecycle state of the instrument.
        expected_manufacturer: Manufacturer expected from configuration.
        expected_model: Model expected from configuration.
        identity: Actual identity from device (if available).
        error: Error message if instrument is in error state.
    """

    name: str
    driver: str
    state: InstrumentState
    expected_manufacturer: str
    expected_model: str
    identity: IdentityModel | None = None
    error: str | None = None


class RackStatus(BaseModel):
    """Status of the entire test rack.

    Attributes:
        rack_id: Unique identifier for this rack.
        description: Human-readable description of the rack.
        state: Overall rack state ("initializing", "ready", "error", "closed").
        instruments: Status of all instruments in the rack.
    """

    rack_id: str
    description: str
    state: str
    instruments: list[InstrumentStatus]


class HealthResponse(BaseModel):
    """Health check response for monitoring and load balancers.

    Attributes:
        status: Health status ("ok" if ready, otherwise the rack state).
        rack_id: Unique identifier for this rack.
    """

    status: str
    rack_id: str
