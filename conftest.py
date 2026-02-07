"""Root conftest.py for hwtest monorepo.

This provides shared pytest configuration and fixtures across all packages.
It also automatically detects and marks tests that use mocking.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from _pytest.config import Config
    from _pytest.nodes import Item
    from _pytest.python import Module


# Add all package src directories to path for imports
PROJECT_ROOT = Path(__file__).parent
for pkg_dir in PROJECT_ROOT.glob("hwtest-*/src"):
    if str(pkg_dir) not in sys.path:
        sys.path.insert(0, str(pkg_dir))


def pytest_configure(config: Config) -> None:
    """Register custom markers.

    Args:
        config: pytest configuration object.
    """
    config.addinivalue_line(
        "markers",
        "uses_mock: Test uses mocking (auto-detected or manually marked)",
    )
    config.addinivalue_line(
        "markers",
        "integration: Integration test requiring real hardware",
    )
    config.addinivalue_line(
        "markers",
        "slow: Slow-running test",
    )


class MockDetector(ast.NodeVisitor):
    """AST visitor to detect mock usage in test functions."""

    # Patterns that indicate mocking
    MOCK_PATTERNS = frozenset({
        # unittest.mock
        "MagicMock",
        "Mock",
        "patch",
        "create_autospec",
        "PropertyMock",
        "AsyncMock",
        # pytest-mock
        "mocker",
        # Common mock helper patterns
        "mock",
        "fake",
        "stub",
    })

    def __init__(self) -> None:
        """Initialize the detector."""
        self.uses_mock = False
        self.mock_imports: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        """Check import statements for mock modules.

        Args:
            node: Import AST node.
        """
        for alias in node.names:
            if "mock" in alias.name.lower():
                self.mock_imports.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Check from-import statements for mock modules.

        Args:
            node: ImportFrom AST node.
        """
        if node.module and "mock" in node.module.lower():
            for alias in node.names:
                self.mock_imports.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Check function calls for mock usage.

        Args:
            node: Call AST node.
        """
        # Check for direct mock calls like MagicMock(), patch()
        if isinstance(node.func, ast.Name):
            if node.func.id in self.MOCK_PATTERNS or node.func.id in self.mock_imports:
                self.uses_mock = True
        # Check for attribute calls like unittest.mock.MagicMock()
        elif isinstance(node.func, ast.Attribute):
            if node.func.attr in self.MOCK_PATTERNS:
                self.uses_mock = True
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """Check variable names for mock fixtures.

        Args:
            node: Name AST node.
        """
        # Check for pytest-mock's mocker fixture
        if node.id == "mocker":
            self.uses_mock = True
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Check function definitions for mock in parameter names.

        Args:
            node: FunctionDef AST node.
        """
        for arg in node.args.args:
            if "mock" in arg.arg.lower() or arg.arg == "mocker":
                self.uses_mock = True
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Check async function definitions for mock in parameter names.

        Args:
            node: AsyncFunctionDef AST node.
        """
        for arg in node.args.args:
            if "mock" in arg.arg.lower() or arg.arg == "mocker":
                self.uses_mock = True
        self.generic_visit(node)


def _check_test_uses_mock(item: Item) -> bool:
    """Check if a test function uses mocking.

    Args:
        item: pytest test item.

    Returns:
        True if test uses mocking.
    """
    # Check if already manually marked
    if item.get_closest_marker("uses_mock"):
        return True

    # Check test function name
    if hasattr(item, "name"):
        name_lower = item.name.lower()
        if "mock" in name_lower or "fake" in name_lower or "stub" in name_lower:
            return True

    # Try to get the source and analyze it
    try:
        if hasattr(item, "obj") and item.obj is not None:
            import inspect
            source = inspect.getsource(item.obj)

            # Parse and analyze the source
            try:
                tree = ast.parse(source)
                detector = MockDetector()
                detector.visit(tree)
                return detector.uses_mock
            except SyntaxError:
                pass
    except (OSError, TypeError):
        pass

    return False


def pytest_collection_modifyitems(config: Config, items: list[Item]) -> None:
    """Auto-detect and mark tests that use mocking.

    Args:
        config: pytest configuration object.
        items: List of collected test items.
    """
    uses_mock_marker = pytest.mark.uses_mock

    for item in items:
        # Skip if already has the marker
        if item.get_closest_marker("uses_mock"):
            continue

        # Check if test uses mocking
        if _check_test_uses_mock(item):
            item.add_marker(uses_mock_marker)


def pytest_report_header(config: Config) -> list[str]:
    """Add coverage mode info to pytest header.

    Args:
        config: pytest configuration object.

    Returns:
        List of header lines.
    """
    lines = ["hwtest monorepo test suite"]

    # Check if coverage is enabled
    if config.option.cov_source:
        lines.append("Coverage: enabled with mock detection")

    return lines
