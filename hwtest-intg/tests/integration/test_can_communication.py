"""Integration tests for CAN communication between rack and UUT.

These tests verify:
- UUT heartbeat transmission and timing
- CAN echo functionality

Requirements:
- Pi 5 rack with CAN interface (can0)
- Pi Zero UUT running uut-simulator on the same CAN bus
- Network connectivity to UUT for REST API

Environment variables:
    UUT_URL: URL of UUT simulator (e.g., http://192.168.68.100:8080)
    CAN_INTERFACE: CAN interface name (default: can0)

Run with:
    UUT_URL=http://192.168.68.xxx:8080 pytest tests/integration/ -v
"""

from __future__ import annotations

import pytest

from hwtest_intg.can.interface import (
    ECHO_ID_OFFSET,
    RACK_TEST_MSG_ID,
    UUT_HEARTBEAT_ID,
    RackCanInterface,
)
from hwtest_intg.clients.uut_client import EchoConfig, UutClient

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


class TestHeartbeat:
    """Tests for UUT heartbeat functionality."""

    @pytest.mark.timeout(5)
    async def test_heartbeat_received(
        self,
        rack_can: RackCanInterface,
        uut_client: UutClient,
    ) -> None:
        """Verify that the UUT sends heartbeat messages.

        The UUT should send heartbeat messages at 10 Hz (every 100ms).
        We should receive at least one within 200ms.
        """
        # Verify heartbeat is running on UUT
        status = await uut_client.can_get_heartbeat()
        assert status.running, "Heartbeat should be running"
        assert status.arbitration_id == UUT_HEARTBEAT_ID

        # Wait for a heartbeat message
        msg = await rack_can.wait_for_heartbeat(timeout=0.5)

        assert msg is not None, "Should receive heartbeat within 500ms"
        assert msg.arbitration_id == UUT_HEARTBEAT_ID
        assert len(msg.data) == 8, "Heartbeat should contain 8-byte counter"

    @pytest.mark.timeout(10)
    async def test_heartbeat_rate(
        self,
        rack_can: RackCanInterface,
        uut_client: UutClient,
    ) -> None:
        """Verify heartbeat rate is approximately 10 Hz.

        Collect heartbeats for 1 second and verify we get 9-11 messages.
        Allow some tolerance for timing jitter.
        """
        # Verify heartbeat is running
        status = await uut_client.can_get_heartbeat()
        assert status.running, "Heartbeat should be running"

        # Collect messages for 1 second
        messages = await rack_can.collect_messages(
            duration=1.0,
            filter_id=UUT_HEARTBEAT_ID,
        )

        # Should get approximately 10 messages (10 Hz)
        # Allow 9-11 for timing tolerance
        assert 9 <= len(messages) <= 11, f"Expected 9-11 heartbeats at 10 Hz, got {len(messages)}"

    @pytest.mark.timeout(5)
    async def test_heartbeat_counter_increments(
        self,
        rack_can: RackCanInterface,
        uut_client: UutClient,
    ) -> None:
        """Verify the heartbeat counter increments."""
        # Verify heartbeat is running
        status = await uut_client.can_get_heartbeat()
        assert status.running, "Heartbeat should be running"

        # Collect a few heartbeats
        messages = await rack_can.collect_messages(
            duration=0.5,
            filter_id=UUT_HEARTBEAT_ID,
        )

        assert len(messages) >= 2, "Need at least 2 heartbeats to verify incrementing"

        # Extract counters and verify they increment
        counters = [int.from_bytes(msg.data, byteorder="big") for msg in messages]

        for i in range(1, len(counters)):
            # Counter should increment (allowing for wrap-around)
            expected = (counters[i - 1] + 1) & 0xFFFFFFFFFFFFFFFF
            assert (
                counters[i] == expected
            ), f"Counter should increment: {counters[i-1]} -> {counters[i]}"


class TestCanEcho:
    """Tests for CAN echo functionality."""

    @pytest.fixture(autouse=True)
    async def setup_echo(self, uut_client: UutClient) -> None:
        """Enable echo mode before each test."""
        await uut_client.can_set_echo(EchoConfig(enabled=True, id_offset=ECHO_ID_OFFSET))
        # Clear any buffered messages
        await uut_client.can_clear_received()

    @pytest.mark.timeout(5)
    async def test_echo_basic(
        self,
        rack_can: RackCanInterface,
        uut_client: UutClient,
    ) -> None:
        """Verify basic echo functionality.

        Send a message and verify it's echoed back with the correct ID offset.
        """
        # Verify echo is configured
        config = await uut_client.can_get_echo()
        assert config.enabled, "Echo should be enabled"
        assert config.id_offset == ECHO_ID_OFFSET

        # Send test message and wait for echo
        test_data = b"\x01\x02\x03\x04\x05\x06\x07\x08"
        echo = await rack_can.echo_test(
            message_id=RACK_TEST_MSG_ID,
            data=test_data,
            expected_echo_id=RACK_TEST_MSG_ID + ECHO_ID_OFFSET,
            timeout=1.0,
        )

        assert echo is not None, "Should receive echo within 1 second"
        assert echo.arbitration_id == RACK_TEST_MSG_ID + ECHO_ID_OFFSET
        assert echo.data == test_data

    @pytest.mark.timeout(10)
    async def test_echo_multiple_messages(
        self,
        rack_can: RackCanInterface,
        uut_client: UutClient,
    ) -> None:
        """Verify multiple messages are echoed correctly."""
        num_messages = 5
        echoes_received = 0

        for i in range(num_messages):
            test_data = bytes([i] * 8)
            echo = await rack_can.echo_test(
                message_id=RACK_TEST_MSG_ID,
                data=test_data,
                timeout=1.0,
            )

            if echo is not None and echo.data == test_data:
                echoes_received += 1

        assert (
            echoes_received == num_messages
        ), f"Should receive all {num_messages} echoes, got {echoes_received}"

    @pytest.mark.timeout(5)
    async def test_echo_filter(
        self,
        rack_can: RackCanInterface,
        uut_client: UutClient,
    ) -> None:
        """Verify echo filter functionality.

        When filter_ids is set, only matching messages should be echoed.
        """
        # Configure echo with filter
        filtered_id = 0x300
        await uut_client.can_set_echo(
            EchoConfig(
                enabled=True,
                id_offset=ECHO_ID_OFFSET,
                filter_ids=[filtered_id],
            )
        )

        # Send a message that should NOT be echoed
        rack_can.send(RACK_TEST_MSG_ID, b"\x01\x02\x03\x04")

        # Send a message that SHOULD be echoed
        echo = await rack_can.echo_test(
            message_id=filtered_id,
            data=b"\xaa\xbb\xcc\xdd",
            expected_echo_id=filtered_id + ECHO_ID_OFFSET,
            timeout=1.0,
        )

        assert echo is not None, "Filtered message should be echoed"
        assert echo.arbitration_id == filtered_id + ECHO_ID_OFFSET

    @pytest.mark.timeout(5)
    async def test_echo_disabled(
        self,
        rack_can: RackCanInterface,
        uut_client: UutClient,
    ) -> None:
        """Verify messages are not echoed when echo is disabled."""
        # Disable echo
        await uut_client.can_set_echo(EchoConfig(enabled=False))

        # Send a message
        rack_can.send(RACK_TEST_MSG_ID, b"\x01\x02\x03\x04")

        # Wait briefly for any echo (should not receive one)
        echo = await rack_can.echo_test(
            message_id=RACK_TEST_MSG_ID,
            data=b"\x01\x02\x03\x04",
            timeout=0.3,
        )

        assert echo is None, "Should not receive echo when disabled"


class TestCanRoundtrip:
    """Tests for CAN message round-trip through the UUT."""

    @pytest.mark.timeout(5)
    async def test_uut_receives_rack_message(
        self,
        rack_can: RackCanInterface,
        uut_client: UutClient,
    ) -> None:
        """Verify the UUT receives messages sent from the rack.

        Check the UUT's received message buffer via REST API.
        """
        # Clear UUT receive buffer
        await uut_client.can_clear_received()

        # Send a message from rack
        test_data = b"\xde\xad\xbe\xef"
        rack_can.send(RACK_TEST_MSG_ID, test_data)

        # Give the UUT time to receive and buffer
        import asyncio

        await asyncio.sleep(0.1)

        # Check UUT received the message
        received = await uut_client.can_get_received()

        # Filter for our test message (exclude heartbeats, etc.)
        our_messages = [m for m in received if m["arbitration_id"] == RACK_TEST_MSG_ID]

        assert len(our_messages) >= 1, "UUT should receive our message"
        assert bytes(our_messages[-1]["data"]) == test_data
