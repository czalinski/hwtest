"""Interface definitions for hwtest."""

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
