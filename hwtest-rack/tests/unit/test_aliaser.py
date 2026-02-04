"""Unit tests for stream aliaser."""

from __future__ import annotations

import pytest

from hwtest_core.types.common import DataType, SourceId
from hwtest_core.types.streaming import StreamData, StreamField, StreamSchema

from hwtest_rack.aliaser import AliasMapping, ActiveAlias, StreamAliaser


class TestAliasMapping:
    """Tests for AliasMapping dataclass."""

    def test_create_minimal(self) -> None:
        """Test creating an AliasMapping with minimal args."""
        mapping = AliasMapping(
            physical_source="dc_psu_slot_3",
            logical_name="main_battery",
        )
        assert mapping.physical_source == "dc_psu_slot_3"
        assert mapping.logical_name == "main_battery"
        assert mapping.field_filter is None
        assert mapping.field_mapping is None

    def test_create_with_field_filter(self) -> None:
        """Test creating an AliasMapping with field filter."""
        mapping = AliasMapping(
            physical_source="dc_psu_slot_3",
            logical_name="main_battery",
            field_filter=["voltage", "current"],
        )
        assert mapping.field_filter == ["voltage", "current"]

    def test_create_with_field_mapping(self) -> None:
        """Test creating an AliasMapping with field mapping."""
        mapping = AliasMapping(
            physical_source="dc_psu_slot_3",
            logical_name="main_battery",
            field_mapping={"ch1_voltage": "voltage", "ch1_current": "current"},
        )
        assert mapping.field_mapping == {"ch1_voltage": "voltage", "ch1_current": "current"}

    def test_create_with_all_options(self) -> None:
        """Test creating an AliasMapping with all options."""
        mapping = AliasMapping(
            physical_source="dc_psu_slot_3",
            logical_name="main_battery",
            field_filter=["ch1_voltage", "ch1_current"],
            field_mapping={"ch1_voltage": "voltage", "ch1_current": "current"},
        )
        assert mapping.physical_source == "dc_psu_slot_3"
        assert mapping.logical_name == "main_battery"
        assert mapping.field_filter == ["ch1_voltage", "ch1_current"]
        assert mapping.field_mapping == {"ch1_voltage": "voltage", "ch1_current": "current"}


class TestActiveAlias:
    """Tests for ActiveAlias dataclass."""

    def test_create(self) -> None:
        """Test creating an ActiveAlias."""
        mapping = AliasMapping("source", "logical")
        schema = StreamSchema(
            source_id=SourceId("logical"),
            fields=(StreamField("voltage", DataType.F64, "V"),),
        )
        alias = ActiveAlias(mapping=mapping, logical_schema=schema)

        assert alias.mapping == mapping
        assert alias.logical_schema == schema
        assert alias.task is None


class TestStreamAliaser:
    """Tests for StreamAliaser in offline mode."""

    @pytest.mark.asyncio
    async def test_start_offline_mode(self) -> None:
        """Test starting aliaser without NATS config (offline mode)."""
        aliaser = StreamAliaser(nats_config=None)
        assert not aliaser.is_running

        await aliaser.start()
        assert aliaser.is_running

        await aliaser.stop()
        assert not aliaser.is_running

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        """Test that starting multiple times is safe."""
        aliaser = StreamAliaser(nats_config=None)

        await aliaser.start()
        await aliaser.start()  # Should not raise
        assert aliaser.is_running

        await aliaser.stop()

    @pytest.mark.asyncio
    async def test_stop_idempotent(self) -> None:
        """Test that stopping multiple times is safe."""
        aliaser = StreamAliaser(nats_config=None)

        await aliaser.start()
        await aliaser.stop()
        await aliaser.stop()  # Should not raise
        assert not aliaser.is_running

    @pytest.mark.asyncio
    async def test_add_alias_offline(self) -> None:
        """Test adding an alias in offline mode."""
        aliaser = StreamAliaser(nats_config=None)
        await aliaser.start()

        await aliaser.add_alias(
            physical_source="dc_psu_slot_3",
            logical_name="main_battery",
        )

        assert "main_battery" in aliaser.list_aliases()
        await aliaser.stop()

    @pytest.mark.asyncio
    async def test_add_alias_with_filter_offline(self) -> None:
        """Test adding an alias with field filter in offline mode."""
        aliaser = StreamAliaser(nats_config=None)
        await aliaser.start()

        await aliaser.add_alias(
            physical_source="dc_psu_slot_3",
            logical_name="main_battery",
            field_filter=["voltage", "current"],
        )

        info = aliaser.get_alias_info("main_battery")
        assert info is not None
        assert info.field_filter == ["voltage", "current"]

        await aliaser.stop()

    @pytest.mark.asyncio
    async def test_add_alias_not_running_raises(self) -> None:
        """Test that adding alias when not running raises RuntimeError."""
        aliaser = StreamAliaser(nats_config=None)

        with pytest.raises(RuntimeError, match="not running"):
            await aliaser.add_alias("source", "logical")

    @pytest.mark.asyncio
    async def test_add_duplicate_alias_raises(self) -> None:
        """Test that adding duplicate logical name raises ValueError."""
        aliaser = StreamAliaser(nats_config=None)
        await aliaser.start()

        await aliaser.add_alias("source1", "logical")

        with pytest.raises(ValueError, match="already aliased"):
            await aliaser.add_alias("source2", "logical")

        await aliaser.stop()

    @pytest.mark.asyncio
    async def test_remove_alias(self) -> None:
        """Test removing an alias."""
        aliaser = StreamAliaser(nats_config=None)
        await aliaser.start()

        await aliaser.add_alias("source", "logical")
        assert "logical" in aliaser.list_aliases()

        await aliaser.remove_alias("logical")
        assert "logical" not in aliaser.list_aliases()

        await aliaser.stop()

    @pytest.mark.asyncio
    async def test_remove_nonexistent_alias(self) -> None:
        """Test removing nonexistent alias is safe."""
        aliaser = StreamAliaser(nats_config=None)
        await aliaser.start()

        # Should not raise
        await aliaser.remove_alias("nonexistent")

        await aliaser.stop()

    @pytest.mark.asyncio
    async def test_list_aliases(self) -> None:
        """Test listing all aliases."""
        aliaser = StreamAliaser(nats_config=None)
        await aliaser.start()

        await aliaser.add_alias("source1", "logical1")
        await aliaser.add_alias("source2", "logical2")
        await aliaser.add_alias("source3", "logical3")

        aliases = aliaser.list_aliases()
        assert len(aliases) == 3
        assert "logical1" in aliases
        assert "logical2" in aliases
        assert "logical3" in aliases

        await aliaser.stop()

    @pytest.mark.asyncio
    async def test_get_alias_info(self) -> None:
        """Test getting alias info."""
        aliaser = StreamAliaser(nats_config=None)
        await aliaser.start()

        await aliaser.add_alias(
            physical_source="dc_psu_slot_3",
            logical_name="main_battery",
            field_filter=["voltage"],
            field_mapping={"ch1_voltage": "voltage"},
        )

        info = aliaser.get_alias_info("main_battery")
        assert info is not None
        assert info.physical_source == "dc_psu_slot_3"
        assert info.logical_name == "main_battery"
        assert info.field_filter == ["voltage"]
        assert info.field_mapping == {"ch1_voltage": "voltage"}

        await aliaser.stop()

    @pytest.mark.asyncio
    async def test_get_alias_info_nonexistent(self) -> None:
        """Test getting info for nonexistent alias returns None."""
        aliaser = StreamAliaser(nats_config=None)
        await aliaser.start()

        assert aliaser.get_alias_info("nonexistent") is None

        await aliaser.stop()

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        """Test async context manager."""
        async with StreamAliaser(nats_config=None) as aliaser:
            assert aliaser.is_running
            await aliaser.add_alias("source", "logical")
            assert "logical" in aliaser.list_aliases()

        assert not aliaser.is_running

    @pytest.mark.asyncio
    async def test_stop_clears_aliases(self) -> None:
        """Test that stopping clears all aliases."""
        aliaser = StreamAliaser(nats_config=None)
        await aliaser.start()

        await aliaser.add_alias("source1", "logical1")
        await aliaser.add_alias("source2", "logical2")
        assert len(aliaser.list_aliases()) == 2

        await aliaser.stop()

        # After stopping, aliases should be cleared
        # Start again to verify
        await aliaser.start()
        assert len(aliaser.list_aliases()) == 0

        await aliaser.stop()


class TestStreamAliaserSchemaBuilding:
    """Tests for schema building logic."""

    def test_build_logical_schema_no_filter(self) -> None:
        """Test building logical schema without field filter."""
        aliaser = StreamAliaser(nats_config=None)

        physical_schema = StreamSchema(
            source_id=SourceId("dc_psu_slot_3"),
            fields=(
                StreamField("voltage", DataType.F64, "V"),
                StreamField("current", DataType.F64, "A"),
                StreamField("power", DataType.F64, "W"),
            ),
        )

        mapping = AliasMapping(
            physical_source="dc_psu_slot_3",
            logical_name="main_battery",
        )

        logical_schema = aliaser._build_logical_schema(physical_schema, mapping)

        assert logical_schema.source_id == SourceId("main_battery")
        assert len(logical_schema.fields) == 3
        assert logical_schema.fields[0].name == "voltage"
        assert logical_schema.fields[1].name == "current"
        assert logical_schema.fields[2].name == "power"

    def test_build_logical_schema_with_filter(self) -> None:
        """Test building logical schema with field filter."""
        aliaser = StreamAliaser(nats_config=None)

        physical_schema = StreamSchema(
            source_id=SourceId("dc_psu_slot_3"),
            fields=(
                StreamField("voltage", DataType.F64, "V"),
                StreamField("current", DataType.F64, "A"),
                StreamField("power", DataType.F64, "W"),
            ),
        )

        mapping = AliasMapping(
            physical_source="dc_psu_slot_3",
            logical_name="main_battery",
            field_filter=["voltage", "current"],
        )

        logical_schema = aliaser._build_logical_schema(physical_schema, mapping)

        assert logical_schema.source_id == SourceId("main_battery")
        assert len(logical_schema.fields) == 2
        assert logical_schema.fields[0].name == "voltage"
        assert logical_schema.fields[1].name == "current"

    def test_build_logical_schema_with_mapping(self) -> None:
        """Test building logical schema with field mapping (rename)."""
        aliaser = StreamAliaser(nats_config=None)

        physical_schema = StreamSchema(
            source_id=SourceId("dc_psu_slot_3"),
            fields=(
                StreamField("ch1_voltage", DataType.F64, "V"),
                StreamField("ch1_current", DataType.F64, "A"),
            ),
        )

        mapping = AliasMapping(
            physical_source="dc_psu_slot_3",
            logical_name="main_battery",
            field_mapping={"ch1_voltage": "voltage", "ch1_current": "current"},
        )

        logical_schema = aliaser._build_logical_schema(physical_schema, mapping)

        assert logical_schema.source_id == SourceId("main_battery")
        assert len(logical_schema.fields) == 2
        assert logical_schema.fields[0].name == "voltage"
        assert logical_schema.fields[1].name == "current"

    def test_build_logical_schema_with_filter_and_mapping(self) -> None:
        """Test building logical schema with both filter and mapping."""
        aliaser = StreamAliaser(nats_config=None)

        physical_schema = StreamSchema(
            source_id=SourceId("dc_psu_slot_3"),
            fields=(
                StreamField("ch1_voltage", DataType.F64, "V"),
                StreamField("ch1_current", DataType.F64, "A"),
                StreamField("ch2_voltage", DataType.F64, "V"),
                StreamField("ch2_current", DataType.F64, "A"),
            ),
        )

        mapping = AliasMapping(
            physical_source="dc_psu_slot_3",
            logical_name="main_battery",
            field_filter=["ch1_voltage", "ch1_current"],
            field_mapping={"ch1_voltage": "voltage", "ch1_current": "current"},
        )

        logical_schema = aliaser._build_logical_schema(physical_schema, mapping)

        assert logical_schema.source_id == SourceId("main_battery")
        assert len(logical_schema.fields) == 2
        assert logical_schema.fields[0].name == "voltage"
        assert logical_schema.fields[1].name == "current"
        # Preserve units from physical schema
        assert logical_schema.fields[0].unit == "V"
        assert logical_schema.fields[1].unit == "A"


class TestStreamAliaserDataTransform:
    """Tests for data transformation logic."""

    @pytest.mark.asyncio
    async def test_transform_data_no_filter(self) -> None:
        """Test transforming data without filter passes all samples."""
        aliaser = StreamAliaser(nats_config=None)
        await aliaser.start()

        physical_schema = StreamSchema(
            source_id=SourceId("dc_psu_slot_3"),
            fields=(
                StreamField("voltage", DataType.F64, "V"),
                StreamField("current", DataType.F64, "A"),
            ),
        )

        mapping = AliasMapping(
            physical_source="dc_psu_slot_3",
            logical_name="main_battery",
        )

        # Manually set up the alias with schema
        await aliaser.add_alias(mapping.physical_source, mapping.logical_name)
        aliaser._aliases[mapping.logical_name].logical_schema = aliaser._build_logical_schema(
            physical_schema, mapping
        )

        physical_data = StreamData(
            schema_id=physical_schema.schema_id,
            timestamp_ns=1000000000,
            period_ns=1000000,
            samples=((12.0, 1.5), (12.1, 1.6)),
        )

        logical_data = aliaser._transform_data(physical_data, physical_schema, mapping)

        assert logical_data is not None
        assert logical_data.timestamp_ns == 1000000000
        assert logical_data.period_ns == 1000000
        assert logical_data.samples == ((12.0, 1.5), (12.1, 1.6))

        await aliaser.stop()

    @pytest.mark.asyncio
    async def test_transform_data_with_filter(self) -> None:
        """Test transforming data with field filter."""
        aliaser = StreamAliaser(nats_config=None)
        await aliaser.start()

        physical_schema = StreamSchema(
            source_id=SourceId("dc_psu_slot_3"),
            fields=(
                StreamField("voltage", DataType.F64, "V"),
                StreamField("current", DataType.F64, "A"),
                StreamField("power", DataType.F64, "W"),
            ),
        )

        mapping = AliasMapping(
            physical_source="dc_psu_slot_3",
            logical_name="main_battery",
            field_filter=["voltage", "current"],  # Filter out power
        )

        await aliaser.add_alias(
            mapping.physical_source,
            mapping.logical_name,
            field_filter=mapping.field_filter,
        )
        aliaser._aliases[mapping.logical_name].logical_schema = aliaser._build_logical_schema(
            physical_schema, mapping
        )

        physical_data = StreamData(
            schema_id=physical_schema.schema_id,
            timestamp_ns=1000000000,
            period_ns=1000000,
            samples=((12.0, 1.5, 18.0), (12.1, 1.6, 19.36)),
        )

        logical_data = aliaser._transform_data(physical_data, physical_schema, mapping)

        assert logical_data is not None
        # Should only have voltage and current (indices 0 and 1)
        assert logical_data.samples == ((12.0, 1.5), (12.1, 1.6))

        await aliaser.stop()

    @pytest.mark.asyncio
    async def test_transform_data_filter_no_match_returns_none(self) -> None:
        """Test transforming data with filter that matches no fields."""
        aliaser = StreamAliaser(nats_config=None)
        await aliaser.start()

        physical_schema = StreamSchema(
            source_id=SourceId("dc_psu_slot_3"),
            fields=(
                StreamField("voltage", DataType.F64, "V"),
                StreamField("current", DataType.F64, "A"),
            ),
        )

        mapping = AliasMapping(
            physical_source="dc_psu_slot_3",
            logical_name="main_battery",
            field_filter=["nonexistent_field"],  # Filter that matches nothing
        )

        await aliaser.add_alias(
            mapping.physical_source,
            mapping.logical_name,
            field_filter=mapping.field_filter,
        )
        aliaser._aliases[mapping.logical_name].logical_schema = aliaser._build_logical_schema(
            physical_schema, mapping
        )

        physical_data = StreamData(
            schema_id=physical_schema.schema_id,
            timestamp_ns=1000000000,
            period_ns=1000000,
            samples=((12.0, 1.5),),
        )

        logical_data = aliaser._transform_data(physical_data, physical_schema, mapping)

        # Should return None when no fields match
        assert logical_data is None

        await aliaser.stop()
