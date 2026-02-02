"""hwtest-intg: Integration tests and example code for hwtest.

This package provides:
- Integration test cases for hwtest hardware automation
- Reusable pytest fixtures for CAN and REST API testing
- Example code demonstrating hwtest package usage

The package is designed to serve as both working tests and code samples
for developers building hardware test automation with hwtest.
"""

from hwtest_intg.can.interface import RackCanInterface
from hwtest_intg.clients.uut_client import UutClient
from hwtest_intg.utils.config import load_rack_config

__version__ = "0.1.0"

__all__ = [
    "RackCanInterface",
    "UutClient",
    "load_rack_config",
]
