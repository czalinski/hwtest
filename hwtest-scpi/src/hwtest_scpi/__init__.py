"""SCPI protocol library for hwtest instrument automation.

This package provides SCPI (Standard Commands for Programmable Instruments)
communication infrastructure for hardware test automation. It includes:

- Transport abstraction for SCPI message passing
- PyVISA-backed transport for real instruments
- High-level connection with automatic error checking
- Number parsing and formatting utilities for SCPI responses
- Custom exception types for SCPI protocol errors

Typical usage::

    from hwtest_scpi import VisaResource, ScpiConnection

    transport = VisaResource("TCPIP::192.168.1.100::INSTR")
    transport.open()
    conn = ScpiConnection(transport)
    identity = conn.get_identity()
    print(f"Connected to {identity.manufacturer} {identity.model}")
    conn.close()
"""

from hwtest_scpi.connection import ScpiConnection, parse_idn_response
from hwtest_scpi.errors import ScpiCommandError, ScpiError, ScpiInstrumentError
from hwtest_scpi.number import (
    ScpiSpecial,
    format_bool,
    format_number,
    parse_bool,
    parse_int,
    parse_number,
    parse_numbers,
    parse_special,
)
from hwtest_scpi.transport import ScpiTransport
from hwtest_scpi.visa import VisaResource

__all__ = [
    # Connection
    "ScpiConnection",
    "parse_idn_response",
    # Errors
    "ScpiCommandError",
    "ScpiError",
    "ScpiInstrumentError",
    # Number parsing/formatting
    "ScpiSpecial",
    "format_bool",
    "format_number",
    "parse_bool",
    "parse_int",
    "parse_number",
    "parse_numbers",
    "parse_special",
    # Transport
    "ScpiTransport",
    # VISA
    "VisaResource",
]
