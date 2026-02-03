"""Unit tests for NATS configuration."""

import pytest

from hwtest_nats.config import NatsConfig


class TestNatsConfig:
    """Tests for NatsConfig."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = NatsConfig()

        assert config.servers == ("nats://localhost:4222",)
        assert config.stream_name == "TELEMETRY"
        assert config.subject_prefix == "telemetry"
        assert config.connect_timeout == 5.0
        assert config.schema_publish_interval == 1.0

    def test_custom_servers(self) -> None:
        """Test configuration with custom servers."""
        config = NatsConfig(servers=("nats://server1:4222", "nats://server2:4222"))

        assert len(config.servers) == 2
        assert "nats://server1:4222" in config.servers

    def test_from_url(self) -> None:
        """Test creating config from a single URL."""
        config = NatsConfig.from_url("nats://myserver:4222", stream_name="TEST")

        assert config.servers == ("nats://myserver:4222",)
        assert config.stream_name == "TEST"

    def test_get_subject(self) -> None:
        """Test subject generation."""
        config = NatsConfig(subject_prefix="test")

        assert config.get_subject("sensor1") == "test.sensor1"
        assert config.get_schema_subject("sensor1") == "test.sensor1.schema"
        assert config.get_data_subject("sensor1") == "test.sensor1.data"

    def test_validation_empty_servers(self) -> None:
        """Test validation fails for empty servers."""
        with pytest.raises(ValueError, match="At least one NATS server URL"):
            NatsConfig(servers=())

    def test_validation_invalid_timeout(self) -> None:
        """Test validation fails for invalid timeout."""
        with pytest.raises(ValueError, match="connect_timeout must be positive"):
            NatsConfig(connect_timeout=0)

        with pytest.raises(ValueError, match="connect_timeout must be positive"):
            NatsConfig(connect_timeout=-1)

    def test_validation_invalid_schema_interval(self) -> None:
        """Test validation fails for invalid schema interval."""
        with pytest.raises(ValueError, match="schema_publish_interval must be positive"):
            NatsConfig(schema_publish_interval=0)

    def test_frozen(self) -> None:
        """Test that config is immutable."""
        config = NatsConfig()

        with pytest.raises(AttributeError):
            config.stream_name = "OTHER"  # type: ignore[misc]

    def test_auth_options(self) -> None:
        """Test authentication options."""
        config_user = NatsConfig(user="admin", password="secret")
        assert config_user.user == "admin"
        assert config_user.password == "secret"

        config_token = NatsConfig(token="mytoken")
        assert config_token.token == "mytoken"

    def test_consumer_config(self) -> None:
        """Test consumer configuration options."""
        config = NatsConfig(
            consumer_durable_name="my-consumer",
            consumer_deliver_policy="new",
            consumer_ack_wait=60.0,
        )

        assert config.consumer_durable_name == "my-consumer"
        assert config.consumer_deliver_policy == "new"
        assert config.consumer_ack_wait == 60.0
