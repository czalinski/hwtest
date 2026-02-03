"""Configuration for NATS connections."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NatsConfig:
    """Configuration for connecting to NATS servers.

    Attributes:
        servers: List of NATS server URLs (e.g., ["nats://localhost:4222"]).
        stream_name: JetStream stream name for telemetry data.
        subject_prefix: Prefix for subjects. Full subject is "{prefix}.{source_id}".
        connect_timeout: Connection timeout in seconds.
        reconnect_time_wait: Time to wait between reconnection attempts in seconds.
        max_reconnect_attempts: Maximum number of reconnection attempts (-1 for unlimited).
        schema_publish_interval: Interval for publishing schema messages in seconds.
        user: Optional username for authentication.
        password: Optional password for authentication.
        token: Optional token for authentication.
    """

    servers: tuple[str, ...] = ("nats://localhost:4222",)
    stream_name: str = "TELEMETRY"
    subject_prefix: str = "telemetry"
    connect_timeout: float = 5.0
    reconnect_time_wait: float = 1.0
    max_reconnect_attempts: int = -1
    schema_publish_interval: float = 1.0
    user: str | None = None
    password: str | None = None
    token: str | None = None

    # JetStream consumer configuration
    consumer_durable_name: str | None = None
    consumer_deliver_policy: str = "all"  # "all", "last", "new", "by_start_time"
    consumer_ack_wait: float = 30.0

    def __post_init__(self) -> None:
        """Validate configuration."""
        if not self.servers:
            raise ValueError("At least one NATS server URL is required")
        if self.connect_timeout <= 0:
            raise ValueError("connect_timeout must be positive")
        if self.schema_publish_interval <= 0:
            raise ValueError("schema_publish_interval must be positive")

    @classmethod
    def from_url(cls, url: str, **kwargs: object) -> NatsConfig:
        """Create config from a single NATS URL.

        Args:
            url: NATS server URL (e.g., "nats://localhost:4222").
            **kwargs: Additional configuration options.

        Returns:
            NatsConfig instance.
        """
        return cls(servers=(url,), **kwargs)  # type: ignore[arg-type]

    def get_subject(self, source_id: str) -> str:
        """Get the full subject for a source.

        Args:
            source_id: The source identifier.

        Returns:
            Full subject string like "telemetry.voltage_daq".
        """
        return f"{self.subject_prefix}.{source_id}"

    def get_schema_subject(self, source_id: str) -> str:
        """Get the subject for schema messages.

        Args:
            source_id: The source identifier.

        Returns:
            Schema subject string like "telemetry.voltage_daq.schema".
        """
        return f"{self.subject_prefix}.{source_id}.schema"

    def get_data_subject(self, source_id: str) -> str:
        """Get the subject for data messages.

        Args:
            source_id: The source identifier.

        Returns:
            Data subject string like "telemetry.voltage_daq.data".
        """
        return f"{self.subject_prefix}.{source_id}.data"
