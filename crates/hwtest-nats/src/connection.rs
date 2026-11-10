//! Connection management.

use std::sync::Arc;

use async_nats::jetstream::{self, stream};
use async_nats::ConnectOptions;

use crate::config::NatsConfig;
use crate::error::{NatsError, OpExt, Result};

/// A shared connection (cheap to clone).
#[derive(Clone)]
pub struct NatsConnection {
    client: async_nats::Client,
    jetstream: jetstream::Context,
    config: Arc<NatsConfig>,
}

impl std::fmt::Debug for NatsConnection {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("NatsConnection")
            .field("servers", &self.config.servers)
            .finish_non_exhaustive()
    }
}

impl NatsConnection {
    /// Connects using `config`.
    ///
    /// # Errors
    /// [`NatsError::Config`] for invalid settings, [`NatsError::Connect`] if no
    /// server could be reached within `connect_timeout`.
    pub async fn connect(config: NatsConfig) -> Result<Self> {
        config.validate()?;
        let mut opts = ConnectOptions::new()
            .connection_timeout(NatsConfig::secs(config.connect_timeout))
            .reconnect_delay_callback({
                let wait = NatsConfig::secs(config.reconnect_time_wait);
                move |_| wait
            });
        if config.max_reconnect_attempts >= 0 {
            opts = opts.max_reconnects(usize::try_from(config.max_reconnect_attempts).ok());
        } else {
            opts = opts.max_reconnects(None);
        }
        match (&config.user, &config.password, &config.token) {
            (Some(u), Some(p), _) => opts = opts.user_and_password(u.clone(), p.clone()),
            (_, _, Some(t)) => opts = opts.token(t.clone()),
            _ => {}
        }
        let servers = config.servers.join(",");
        let addrs: Vec<async_nats::ServerAddr> = config
            .servers
            .iter()
            .map(|s| {
                s.parse().map_err(|e: std::io::Error| {
                    NatsError::Config(format!("bad server URL {s:?}: {e}"))
                })
            })
            .collect::<Result<_>>()?;
        let client = tokio::time::timeout(
            NatsConfig::secs(config.connect_timeout),
            async_nats::connect_with_options(addrs, opts),
        )
        .await
        .map_err(|_| NatsError::Connect { servers: servers.clone(), message: "timed out".into() })?
        .map_err(|e| NatsError::Connect { servers, message: e.to_string() })?;
        let jetstream = jetstream::new(client.clone());
        Ok(Self { client, jetstream, config: Arc::new(config) })
    }

    /// The configuration.
    pub fn config(&self) -> &NatsConfig {
        &self.config
    }

    /// The core client.
    pub fn client(&self) -> &async_nats::Client {
        &self.client
    }

    /// The `JetStream` context.
    pub fn jetstream(&self) -> &jetstream::Context {
        &self.jetstream
    }

    /// Creates the telemetry stream if it does not exist and returns it.
    ///
    /// # Errors
    /// [`NatsError::Operation`] if the server rejects the request.
    pub async fn ensure_stream(&self) -> Result<stream::Stream> {
        self.jetstream
            .get_or_create_stream(stream::Config {
                name: self.config.stream_name.clone(),
                subjects: vec![format!("{}.>", self.config.subject_prefix)],
                retention: stream::RetentionPolicy::Limits,
                max_age: NatsConfig::secs(self.config.max_age),
                storage: stream::StorageType::File,
                discard: stream::DiscardPolicy::Old,
                ..Default::default()
            })
            .await
            .op("ensure stream")
    }

    /// Publishes a JSON message on a core subject (captured by the stream).
    ///
    /// # Errors
    /// [`NatsError::Operation`] on failure.
    pub async fn publish_json<T: serde::Serialize>(
        &self,
        subject: String,
        value: &T,
    ) -> Result<()> {
        let payload = serde_json::to_vec(value).map_err(hwtest_core::Error::from)?;
        self.client.publish(subject, payload.into()).await.op("publish")
    }

    /// Flushes and closes the connection.
    ///
    /// # Errors
    /// [`NatsError::Operation`] if draining fails.
    pub async fn close(&self) -> Result<()> {
        self.client.drain().await.op("drain")
    }
}
