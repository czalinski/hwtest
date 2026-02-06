"""Stream aliaser for republishing streams under logical names.

The StreamAliaser subscribes to physical instrument streams and republishes
the data under logical channel names. This allows test code and monitors to
subscribe to logical names while preserving physical stream data for debugging.

Data flow:
    Physical Instrument Stream (e.g., "dc_psu_slot_3")
        │
        ▼
    StreamAliaser
        │
        ├── Republish as "main_battery" (channel 1 data)
        └── Republish as "cpu_power" (channel 2 data)

The aliaser adds ~1-2ms latency per hop, which is well within the 25ms budget.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from hwtest_core.types.common import DataType, SourceId
from hwtest_core.types.streaming import StreamData, StreamField, StreamSchema

logger = logging.getLogger(__name__)


@dataclass
class AliasMapping:
    """Mapping from a physical source to a logical name.

    Args:
        physical_source: Source ID of the physical instrument stream.
        logical_name: Logical name to republish under.
        field_filter: Optional list of field names to include (None = all fields).
        field_mapping: Optional field renaming (physical name -> logical name).
    """

    physical_source: str
    logical_name: str
    field_filter: list[str] | None = None
    field_mapping: dict[str, str] | None = None


@dataclass
class ActiveAlias:
    """An active alias with its subscriber, publisher, and task.

    Tracks the runtime state of an alias including the background task
    that handles republishing and the derived logical schema.

    Attributes:
        mapping: The alias mapping configuration.
        logical_schema: The schema for the logical stream (derived from physical).
        task: Background asyncio task handling the republishing loop.
    """

    mapping: AliasMapping
    logical_schema: StreamSchema
    task: asyncio.Task[None] | None = None


class StreamAliaser:
    """Republishes physical streams under logical channel names.

    The aliaser subscribes to physical instrument streams and republishes
    the data under logical names. Both physical and logical streams remain
    available for subscribers.

    Example:
        aliaser = StreamAliaser(nats_config)
        await aliaser.start()

        # Add alias: physical "dc_psu_slot_3" -> logical "main_battery"
        await aliaser.add_alias(
            physical_source="dc_psu_slot_3",
            logical_name="main_battery",
            field_filter=["voltage", "current"],  # Optional: only these fields
        )

        await aliaser.stop()

    Note:
        This class requires hwtest-nats to be installed. If not available,
        a stub implementation is provided that logs warnings.
    """

    def __init__(self, nats_config: Any | None = None) -> None:
        """Initialize the aliaser.

        Args:
            nats_config: NatsConfig for NATS connection. If None, aliaser
                         operates in offline mode (no-op).
        """
        self._config = nats_config
        self._aliases: dict[str, ActiveAlias] = {}
        self._running = False
        self._connection: Any = None  # NatsConnection when hwtest-nats available

    @property
    def is_running(self) -> bool:
        """Return True if the aliaser is running.

        Returns:
            True if start() has been called and stop() has not.
        """
        return self._running

    async def start(self) -> None:
        """Start the aliaser and connect to NATS.

        If NATS is not configured, operates in offline mode where aliases
        are tracked but no actual republishing occurs. This allows testing
        without a NATS server.

        Raises:
            ImportError: Logged as warning if hwtest-nats not installed
                (falls back to offline mode).
        """
        if self._running:
            return

        if self._config is None:
            logger.info("StreamAliaser started in offline mode (no NATS config)")
            self._running = True
            return

        try:
            from hwtest_nats import NatsConnection  # type: ignore[import-not-found]

            self._connection = NatsConnection(self._config)
            await self._connection.connect()
            await self._connection.ensure_stream()
            self._running = True
            logger.info("StreamAliaser started with NATS connection")

        except ImportError:
            logger.warning("hwtest-nats not installed, StreamAliaser running in offline mode")
            self._running = True

    async def stop(self) -> None:
        """Stop the aliaser and all active alias tasks.

        Cancels all background alias tasks, clears the alias registry,
        and disconnects from NATS. Safe to call multiple times.
        """
        if not self._running:
            return

        # Cancel all alias tasks
        for alias in self._aliases.values():
            if alias.task is not None:
                alias.task.cancel()
                try:
                    await alias.task
                except asyncio.CancelledError:
                    pass

        self._aliases.clear()

        # Disconnect from NATS
        if self._connection is not None:
            await self._connection.disconnect()
            self._connection = None

        self._running = False
        logger.info("StreamAliaser stopped")

    async def add_alias(
        self,
        physical_source: str,
        logical_name: str,
        field_filter: list[str] | None = None,
        field_mapping: dict[str, str] | None = None,
    ) -> None:
        """Add an alias to republish a physical stream under a logical name.

        Args:
            physical_source: Source ID of the physical instrument stream.
            logical_name: Logical name to republish under.
            field_filter: Optional list of field names to include (None = all).
            field_mapping: Optional field renaming (physical -> logical).

        Raises:
            ValueError: If logical_name is already registered.
            RuntimeError: If aliaser is not running.
        """
        if not self._running:
            raise RuntimeError("Aliaser is not running")

        if logical_name in self._aliases:
            raise ValueError(f"Logical name '{logical_name}' already aliased")

        mapping = AliasMapping(
            physical_source=physical_source,
            logical_name=logical_name,
            field_filter=field_filter,
            field_mapping=field_mapping,
        )

        # In offline mode, just store the mapping
        if self._connection is None:
            # Create a placeholder schema
            placeholder_schema = StreamSchema(
                source_id=SourceId(logical_name),
                fields=(StreamField("placeholder", DataType.F64, ""),),
            )
            self._aliases[logical_name] = ActiveAlias(
                mapping=mapping,
                logical_schema=placeholder_schema,
            )
            logger.info(
                "Registered alias (offline): %s -> %s",
                physical_source,
                logical_name,
            )
            return

        # Start the alias task
        task = asyncio.create_task(
            self._alias_loop(mapping),
            name=f"alias_{logical_name}",
        )

        # Placeholder until we receive the physical schema
        placeholder_schema = StreamSchema(
            source_id=SourceId(logical_name),
            fields=(StreamField("pending", DataType.F64, ""),),
        )

        self._aliases[logical_name] = ActiveAlias(
            mapping=mapping,
            logical_schema=placeholder_schema,
            task=task,
        )

        logger.info(
            "Added alias: %s -> %s (filter=%s)",
            physical_source,
            logical_name,
            field_filter,
        )

    async def remove_alias(self, logical_name: str) -> None:
        """Remove an alias.

        Args:
            logical_name: The logical name to remove.
        """
        alias = self._aliases.pop(logical_name, None)
        if alias is None:
            return

        if alias.task is not None:
            alias.task.cancel()
            try:
                await alias.task
            except asyncio.CancelledError:
                pass

        logger.info("Removed alias: %s", logical_name)

    def list_aliases(self) -> list[str]:
        """List all registered logical names.

        Returns:
            List of logical names.
        """
        return list(self._aliases.keys())

    def get_alias_info(self, logical_name: str) -> AliasMapping | None:
        """Get the mapping info for a logical name.

        Args:
            logical_name: The logical name.

        Returns:
            The AliasMapping, or None if not found.
        """
        alias = self._aliases.get(logical_name)
        if alias is None:
            return None
        return alias.mapping

    async def _alias_loop(self, mapping: AliasMapping) -> None:
        """Background task that subscribes to physical stream and republishes.

        Subscribes to the physical source stream, waits for its schema,
        builds a logical schema, creates a publisher for the logical stream,
        and continuously republishes received data.

        Args:
            mapping: The alias mapping configuration.
        """
        try:
            from hwtest_nats import (  # type: ignore[import-not-found]
                NatsStreamPublisher,
                NatsStreamSubscriber,
            )
        except ImportError:
            logger.error("hwtest-nats not available for alias loop")
            return

        subscriber: Any = None
        publisher: Any = None

        try:
            # Create subscriber for physical stream
            subscriber = NatsStreamSubscriber(
                self._config,
                connection=self._connection,
            )
            await subscriber.subscribe(mapping.physical_source)

            # Wait for physical schema
            physical_schema = await subscriber.get_schema(timeout=30.0)
            logger.debug(
                "Received physical schema for %s: %d fields",
                mapping.physical_source,
                len(physical_schema.fields),
            )

            # Build logical schema
            logical_schema = self._build_logical_schema(physical_schema, mapping)

            # Update the stored schema
            if mapping.logical_name in self._aliases:
                self._aliases[mapping.logical_name].logical_schema = logical_schema

            # Create publisher for logical stream
            publisher = NatsStreamPublisher(
                self._config,
                logical_schema,
                connection=self._connection,
            )
            await publisher.start()

            logger.info(
                "Alias active: %s -> %s (%d fields)",
                mapping.physical_source,
                mapping.logical_name,
                len(logical_schema.fields),
            )

            # Republish data
            async for data in subscriber.data():
                logical_data = self._transform_data(data, physical_schema, mapping)
                if logical_data is not None:
                    await publisher.publish(logical_data)

        except asyncio.CancelledError:
            logger.debug("Alias task cancelled: %s", mapping.logical_name)
        except TimeoutError:
            logger.error("Timeout waiting for schema from %s", mapping.physical_source)
        except Exception:
            logger.exception("Error in alias loop for %s", mapping.logical_name)
        finally:
            if publisher is not None:
                await publisher.stop()
            if subscriber is not None:
                await subscriber.disconnect()

    def _build_logical_schema(
        self,
        physical_schema: StreamSchema,
        mapping: AliasMapping,
    ) -> StreamSchema:
        """Build a logical schema from a physical schema.

        Args:
            physical_schema: The physical instrument schema.
            mapping: The alias mapping configuration.

        Returns:
            A new schema with the logical source_id and optionally filtered/renamed fields.
        """
        fields: list[StreamField] = []

        for field in physical_schema.fields:
            # Apply field filter
            if mapping.field_filter is not None:
                if field.name not in mapping.field_filter:
                    continue

            # Apply field mapping (rename)
            name = field.name
            if mapping.field_mapping is not None:
                name = mapping.field_mapping.get(field.name, field.name)

            fields.append(StreamField(name, field.dtype, field.unit))

        return StreamSchema(
            source_id=SourceId(mapping.logical_name),
            fields=tuple(fields),
        )

    def _transform_data(
        self,
        data: StreamData,
        physical_schema: StreamSchema,
        mapping: AliasMapping,
    ) -> StreamData | None:
        """Transform physical data for the logical stream.

        Args:
            data: The physical stream data.
            physical_schema: The physical schema.
            mapping: The alias mapping.

        Returns:
            Transformed StreamData, or None if no fields match filter.
        """
        # If no filter, use data as-is (just need new schema_id)
        if mapping.field_filter is None:
            logical_schema = self._aliases[mapping.logical_name].logical_schema
            return StreamData(
                schema_id=logical_schema.schema_id,
                timestamp_ns=data.timestamp_ns,
                period_ns=data.period_ns,
                samples=data.samples,
            )

        # Build field indices for filtering
        field_indices: list[int] = []
        for i, field in enumerate(physical_schema.fields):
            if field.name in mapping.field_filter:
                field_indices.append(i)

        if not field_indices:
            return None

        # Filter samples
        filtered_samples: list[tuple[int | float, ...]] = []
        for sample in data.samples:
            filtered_sample = tuple(sample[i] for i in field_indices)
            filtered_samples.append(filtered_sample)

        logical_schema = self._aliases[mapping.logical_name].logical_schema
        return StreamData(
            schema_id=logical_schema.schema_id,
            timestamp_ns=data.timestamp_ns,
            period_ns=data.period_ns,
            samples=tuple(filtered_samples),
        )

    async def __aenter__(self) -> StreamAliaser:
        """Enter async context.

        Starts the aliaser and returns self for use in async with blocks.

        Returns:
            This StreamAliaser instance.
        """
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exit async context.

        Stops the aliaser, cancelling all alias tasks and disconnecting from NATS.

        Args:
            exc_type: Exception type if an exception was raised.
            exc_val: Exception value if an exception was raised.
            exc_tb: Exception traceback if an exception was raised.
        """
        await self.stop()
