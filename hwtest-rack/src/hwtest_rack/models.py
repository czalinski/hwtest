"""Pydantic models for REST API responses."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class InstrumentState(str, Enum):
    """State of an instrument in the rack."""

    PENDING = "pending"
    INITIALIZING = "initializing"
    READY = "ready"
    ERROR = "error"
    CLOSED = "closed"


class IdentityModel(BaseModel):
    """Instrument identity information."""

    manufacturer: str
    model: str
    serial: str
    firmware: str


class InstrumentStatus(BaseModel):
    """Status of a single instrument."""

    name: str
    driver: str
    state: InstrumentState
    expected_manufacturer: str
    expected_model: str
    identity: IdentityModel | None = None
    error: str | None = None


class RackStatus(BaseModel):
    """Status of the entire rack."""

    rack_id: str
    description: str
    state: str
    instruments: list[InstrumentStatus]


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    rack_id: str
