"""CAN heartbeat and echo between the rack (CAN_INTERFACE, default can0) and
the UUT (bounds in can_communication.yaml; 1 = OK)."""

import hwtest

test = hwtest.Test("can_communication.yaml")

PAYLOAD = bytes([0x01, 0x02, 0x03, 0x04])


def echoed(ctx, sent_id, timeout=0.5):
    """Sends a frame from the rack; returns the echo or None."""
    ctx.can.send(sent_id, PAYLOAD)
    return ctx.can.wait_for(sent_id + ctx.param("echo_offset"), timeout)


@test.step
def can(ctx, state):
    hb_id = ctx.param("heartbeat_id")
    test_id = ctx.param("test_message_id")
    offset = ctx.param("echo_offset")
    window = ctx.param("collect_seconds")

    hb = ctx.uut.can_heartbeat()
    beats = ctx.can.collect(window, hb_id)
    counters = [int.from_bytes(m["data"], "big") for m in beats]
    counter_ok = len(counters) >= 2 and all(b - a == 1 for a, b in zip(counters, counters[1:]))

    ctx.uut.can_clear_received()
    ctx.uut.can_set_echo(True, offset)
    echo = echoed(ctx, test_id, 1.0)
    echo_ok = echo is not None and echo["data"] == PAYLOAD

    ctx.uut.can_set_echo(True, offset, [test_id + 1])
    filter_ok = echoed(ctx, test_id, 0.3) is None and echoed(ctx, test_id + 1) is not None

    ctx.uut.can_set_echo(False)
    disabled_ok = echoed(ctx, test_id, 0.3) is None

    received = ctx.uut.can_received()
    uut_received_ok = any(
        m["arbitration_id"] == test_id and bytes(m["data"]) == PAYLOAD for m in received
    )

    ctx.measure(
        heartbeat_running=int(hb["running"] and hb["arbitration_id"] == hb_id),
        heartbeat_rate_hz=len(beats) / window,
        heartbeat_counter_ok=int(counter_ok),
        echo_ok=int(echo_ok),
        echo_filter_ok=int(filter_ok),
        echo_disabled_ok=int(disabled_ok),
        uut_received_ok=int(uut_received_ok),
    )


@test.teardown
def stop(ctx):
    ctx.uut.can_set_echo(False)


if __name__ == "__main__":
    test.main()
