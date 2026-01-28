"""Unit tests for rack REST API server."""

from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hwtest_core.types.common import InstrumentIdentity

from hwtest_rack import server
from hwtest_rack.config import ExpectedIdentity, InstrumentConfig, RackConfig
from hwtest_rack.models import InstrumentState
from hwtest_rack.rack import Rack
from hwtest_rack.server import create_app


@pytest.fixture
def mock_rack() -> MagicMock:
    """Create a mock rack instance."""
    rack = MagicMock(spec=Rack)
    rack.rack_id = "test-rack"
    rack.state = "ready"

    from hwtest_rack.models import IdentityModel, InstrumentStatus, RackStatus

    instrument_status = InstrumentStatus(
        name="psu01",
        driver="hwtest_bkprecision.psu:create_instrument",
        state=InstrumentState.READY,
        expected_manufacturer="B&K Precision",
        expected_model="9115",
        identity=IdentityModel(
            manufacturer="B&K Precision",
            model="9115",
            serial="ABC123",
            firmware="1.0",
        ),
        error=None,
    )

    rack.get_status.return_value = RackStatus(
        rack_id="test-rack",
        description="Test rack",
        state="ready",
        instruments=[instrument_status],
    )
    rack.list_instruments.return_value = [instrument_status]
    rack.get_instrument_status.return_value = instrument_status

    return rack


@pytest.fixture
def client(mock_rack: MagicMock) -> TestClient:
    """Create a test client with mocked rack."""
    app = create_app()

    # Patch the global rack
    with patch.object(server, "_rack", mock_rack):
        yield TestClient(app)


class TestHealthEndpoint:
    def test_health_ready(self, client: TestClient, mock_rack: MagicMock) -> None:
        mock_rack.state = "ready"
        with patch.object(server, "_rack", mock_rack):
            response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["rack_id"] == "test-rack"

    def test_health_error(self, client: TestClient, mock_rack: MagicMock) -> None:
        mock_rack.state = "error"
        with patch.object(server, "_rack", mock_rack):
            response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"


class TestStatusEndpoint:
    def test_status(self, client: TestClient, mock_rack: MagicMock) -> None:
        with patch.object(server, "_rack", mock_rack):
            response = client.get("/status")

        assert response.status_code == 200
        data = response.json()
        assert data["rack_id"] == "test-rack"
        assert data["description"] == "Test rack"
        assert data["state"] == "ready"
        assert len(data["instruments"]) == 1
        assert data["instruments"][0]["name"] == "psu01"


class TestInstrumentsEndpoint:
    def test_list_instruments(self, client: TestClient, mock_rack: MagicMock) -> None:
        with patch.object(server, "_rack", mock_rack):
            response = client.get("/instruments")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "psu01"
        assert data[0]["state"] == "ready"

    def test_get_instrument(self, client: TestClient, mock_rack: MagicMock) -> None:
        with patch.object(server, "_rack", mock_rack):
            response = client.get("/instruments/psu01")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "psu01"
        assert data["identity"]["manufacturer"] == "B&K Precision"
        assert data["identity"]["serial"] == "ABC123"

    def test_get_instrument_not_found(
        self, client: TestClient, mock_rack: MagicMock
    ) -> None:
        mock_rack.get_instrument_status.return_value = None
        with patch.object(server, "_rack", mock_rack):
            response = client.get("/instruments/nonexistent")

        assert response.status_code == 404


class TestDashboard:
    def test_dashboard_html(self, client: TestClient, mock_rack: MagicMock) -> None:
        with patch.object(server, "_rack", mock_rack):
            response = client.get("/")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "test-rack" in response.text
        assert "psu01" in response.text
        assert "B&amp;K Precision" in response.text or "B&K Precision" in response.text


class TestRackIntegration:
    def test_rack_with_mock_instrument(self) -> None:
        """Test rack initialization with a mock instrument factory."""
        # Create a mock instrument
        mock_instrument = MagicMock()
        mock_instrument.get_identity.return_value = InstrumentIdentity(
            manufacturer="Test Co",
            model="TestModel",
            serial="12345",
            firmware="1.0",
        )

        def mock_factory(**kwargs: object) -> MagicMock:
            return mock_instrument

        config = RackConfig(
            rack_id="test-rack",
            description="Test",
            instruments=(
                InstrumentConfig(
                    name="test01",
                    driver="mock.module:create_instrument",
                    identity=ExpectedIdentity("Test Co", "TestModel"),
                    kwargs={"arg1": "value1"},
                ),
            ),
        )

        rack = Rack(config)

        # Patch the load_driver function
        with patch("hwtest_rack.rack.load_driver", return_value=mock_factory):
            rack.initialize()

        assert rack.state == "ready"
        status = rack.get_instrument_status("test01")
        assert status is not None
        assert status.state == InstrumentState.READY
        assert status.identity is not None
        assert status.identity.serial == "12345"

    def test_rack_identity_mismatch(self) -> None:
        """Test that rack detects identity mismatch."""
        mock_instrument = MagicMock()
        mock_instrument.get_identity.return_value = InstrumentIdentity(
            manufacturer="Wrong Co",
            model="WrongModel",
            serial="12345",
            firmware="1.0",
        )

        def mock_factory(**kwargs: object) -> MagicMock:
            return mock_instrument

        config = RackConfig(
            rack_id="test-rack",
            description="Test",
            instruments=(
                InstrumentConfig(
                    name="test01",
                    driver="mock.module:create_instrument",
                    identity=ExpectedIdentity("Expected Co", "ExpectedModel"),
                    kwargs={},
                ),
            ),
        )

        rack = Rack(config)

        with patch("hwtest_rack.rack.load_driver", return_value=mock_factory):
            rack.initialize()

        assert rack.state == "error"
        status = rack.get_instrument_status("test01")
        assert status is not None
        assert status.state == InstrumentState.ERROR
        assert "mismatch" in (status.error or "").lower()
