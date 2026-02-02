"""Test configuration for hwtest-intg.

This conftest.py imports fixtures from the package and configures pytest markers.
"""

# Import fixtures from the package to make them available to all tests
from hwtest_intg.fixtures.conftest import (  # noqa: F401
    can_interface_name,
    rack_can,
    rack_can_config,
    uut_client,
    uut_url,
)


def pytest_configure(config: object) -> None:
    """Configure pytest markers."""
    # Note: markers are also defined in pyproject.toml
    pass
