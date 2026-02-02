"""Reusable pytest fixtures for integration tests.

These fixtures can be imported into your test conftest.py or used via
pytest_plugins = ["hwtest_intg.fixtures.conftest"]

Environment variables:
    UUT_URL: URL of the UUT simulator (default: http://localhost:8080)
    CAN_INTERFACE: CAN interface name (default: can0)
"""

from __future__ import annotations

import os
from typing import AsyncGenerator, Generator

import pytest

from hwtest_intg.can.interface import RackCanConfig, RackCanInterface
from hwtest_intg.clients.uut_client import UutClient


@pytest.fixture
def uut_url() -> str:
    """Get the UUT simulator URL from environment.

    Returns:
        UUT simulator URL (default: http://localhost:8080).

    Environment:
        UUT_URL: Override the default URL.
    """
    return os.environ.get("UUT_URL", "http://localhost:8080")


@pytest.fixture
def can_interface_name() -> str:
    """Get the CAN interface name from environment.

    Returns:
        CAN interface name (default: can0).

    Environment:
        CAN_INTERFACE: Override the default interface.
    """
    return os.environ.get("CAN_INTERFACE", "can0")


@pytest.fixture
def rack_can_config(can_interface_name: str) -> RackCanConfig:
    """Create a CAN interface configuration.

    Args:
        can_interface_name: CAN interface name from fixture.

    Returns:
        CAN interface configuration.
    """
    return RackCanConfig(interface=can_interface_name)


@pytest.fixture
def rack_can(rack_can_config: RackCanConfig) -> Generator[RackCanInterface, None, None]:
    """Provide an opened CAN interface for tests.

    This fixture opens the CAN interface before the test and closes it
    after the test completes.

    Args:
        rack_can_config: CAN configuration from fixture.

    Yields:
        Opened CAN interface.

    Example:
        def test_can_send(rack_can):
            rack_can.send(0x200, b"\\x01\\x02\\x03")
    """
    can = RackCanInterface(config=rack_can_config)
    can.open()
    try:
        yield can
    finally:
        can.close()


@pytest.fixture
async def uut_client(uut_url: str) -> AsyncGenerator[UutClient, None]:
    """Provide an async UUT client for tests.

    This fixture creates an HTTP client connected to the UUT simulator.

    Args:
        uut_url: UUT simulator URL from fixture.

    Yields:
        Connected UUT client.

    Example:
        async def test_uut_health(uut_client):
            health = await uut_client.health()
            assert health.status == "healthy"
    """
    async with UutClient(uut_url) as client:
        yield client
