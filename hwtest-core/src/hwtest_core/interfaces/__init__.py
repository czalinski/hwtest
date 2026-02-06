"""Protocol-based interface definitions for hwtest.

This package defines abstract interfaces (using typing.Protocol) for the
major components of the hwtest framework. These interfaces enable loose
coupling between components and allow for multiple implementations.

Interface Categories:
    Telemetry: TelemetryPublisher, TelemetrySubscriber - JSON telemetry pub/sub
    Streaming: StreamPublisher, StreamSubscriber - Binary streaming pub/sub
    State: StatePublisher, StateSubscriber - Environmental state management
    Monitor: Monitor, ThresholdProvider - Threshold evaluation
    Logger: Logger, StreamLogger - Data persistence

All interfaces are designed for async operation and support the async
context manager protocol where appropriate.
"""

from hwtest_core.interfaces.logger import (
    Logger,
    StreamLogger,
)
from hwtest_core.interfaces.monitor import (
    Monitor,
    ThresholdProvider,
)
from hwtest_core.interfaces.state import (
    StatePublisher,
    StateSubscriber,
)
from hwtest_core.interfaces.streaming import (
    StreamPublisher,
    StreamSubscriber,
)
from hwtest_core.interfaces.telemetry import (
    TelemetryPublisher,
    TelemetrySubscriber,
)

__all__ = [
    # Logger interfaces
    "Logger",
    "StreamLogger",
    # Streaming interfaces
    "StreamPublisher",
    "StreamSubscriber",
    # Telemetry interfaces
    "TelemetryPublisher",
    "TelemetrySubscriber",
    # State interfaces
    "StatePublisher",
    "StateSubscriber",
    # Monitor interfaces
    "Monitor",
    "ThresholdProvider",
]
