"""NATS/JetStream integration for hwtest telemetry streaming.

This package provides implementations of the StreamPublisher and StreamSubscriber
protocols using NATS JetStream as the transport layer.

Example usage:

    from hwtest_nats import NatsStreamPublisher, NatsStreamSubscriber, NatsConfig

    # Publisher
    config = NatsConfig(servers=["nats://localhost:4222"])
    publisher = NatsStreamPublisher(config, schema)
    await publisher.start()
    await publisher.publish(data)
    await publisher.stop()

    # Subscriber
    subscriber = NatsStreamSubscriber(config)
    await subscriber.connect()
    await subscriber.subscribe("voltage_daq")
    async for data in subscriber.data():
        process(data)
    await subscriber.disconnect()
"""

from hwtest_nats.config import NatsConfig
from hwtest_nats.connection import NatsConnection
from hwtest_nats.monitor import TelemetryMonitor
from hwtest_nats.publisher import NatsStreamPublisher
from hwtest_nats.state import NatsStatePublisher, NatsStateSubscriber, StateError
from hwtest_nats.subscriber import NatsStreamSubscriber

__all__ = [
    "NatsConfig",
    "NatsConnection",
    "NatsStatePublisher",
    "NatsStateSubscriber",
    "NatsStreamPublisher",
    "NatsStreamSubscriber",
    "StateError",
    "TelemetryMonitor",
]
