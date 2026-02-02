"""Reusable pytest fixtures for integration tests.

To use these fixtures, either:
1. Import them in your conftest.py:
   from hwtest_intg.fixtures.conftest import rack_can, uut_client, uut_url

2. Or use pytest_plugins in your conftest.py:
   pytest_plugins = ["hwtest_intg.fixtures.conftest"]
"""

from hwtest_intg.fixtures.conftest import rack_can, uut_client, uut_url

__all__ = ["rack_can", "uut_client", "uut_url"]
