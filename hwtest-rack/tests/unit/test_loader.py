"""Unit tests for dynamic instrument loader."""

from __future__ import annotations

import pytest

from hwtest_rack.loader import load_driver


class TestLoadDriver:
    def test_load_valid_driver(self) -> None:
        # Load a function from the standard library as a test
        factory = load_driver("os.path:join")
        assert callable(factory)
        assert factory("a", "b") == "a/b"

    def test_load_builtin_function(self) -> None:
        factory = load_driver("builtins:len")
        assert callable(factory)
        assert factory([1, 2, 3]) == 3

    def test_missing_colon(self) -> None:
        with pytest.raises(ValueError, match="module:function"):
            load_driver("os.path.join")

    def test_empty_module(self) -> None:
        with pytest.raises(ValueError, match="module and function names required"):
            load_driver(":join")

    def test_empty_function(self) -> None:
        with pytest.raises(ValueError, match="module and function names required"):
            load_driver("os.path:")

    def test_nonexistent_module(self) -> None:
        with pytest.raises(ImportError, match="Failed to import"):
            load_driver("nonexistent_module_xyz:func")

    def test_nonexistent_function(self) -> None:
        with pytest.raises(AttributeError, match="no attribute"):
            load_driver("os.path:nonexistent_function_xyz")

    def test_not_callable(self) -> None:
        with pytest.raises(TypeError, match="not callable"):
            load_driver("os:name")  # os.name is a string, not callable
