# hwtest-core

Core data types and wire protocols for hwtest, implemented in Rust and exposed
to Python through PyO3.

## Layout

```
crates/hwtest-core/        Pure-Rust implementation (no Python dependency)
hwtest-core/native/        PyO3 bindings -> hwtest_core._native extension module
hwtest-core/src/hwtest_core/
    types/*.py             Public modules; re-export the native classes
    _enums.py              DataType, ValueQuality, BoundType, MonitorVerdict (Python enums)
    _native.pyi            Type stubs for the extension (keep in sync with native/src)
    errors.py              Exception hierarchy (raised from Rust as well)
    interfaces/            typing.Protocol definitions for Python implementations
```

Python code imports exactly as before:

```python
from hwtest_core.types.streaming import StreamField, StreamSchema, StreamData
from hwtest_core.types.bounds import bound_check_from_dict

check = bound_check_from_dict({"within_range": [3.3, 0.1]})   # same as YAML
check.check(3.35)  # True
```

## Installation

Requires a Rust toolchain (https://rustup.rs) and Python >= 3.10.

```bash
pip install -e ".[dev]"          # builds the extension with maturin (debug build)
maturin build --release          # release wheel in ../target/wheels/
```

After changing Rust code, re-run `pip install -e .` (or `maturin develop`) to rebuild.

## Testing

```bash
cargo test                       # Rust unit + Python wire-compatibility tests (repo root)
cargo clippy --all-targets
python -m pytest tests/unit      # Python API tests against the native extension
python -m mypy --strict src/
```

## Behaviour notes

The Rust implementation is wire-compatible with the original Python code (binary frames and JSON
are checked byte-for-byte against fixtures in `crates/hwtest-core/tests/fixtures/`). Intentional
differences:

- Non-finite measurements (NaN, ±inf) fail every bound check except `special: any`.
- `Special` only accepts known kinds (`any`); a typo raises `ThresholdError`.
- Negative or NaN tolerances are rejected.
- NaN values are written to JSON as `null` (valid JSON) and read back as NaN.
- Truncated/corrupt frames raise `ValueError` with a description (not `IndexError`/`struct.error`).
- `Timestamp.from_datetime` is exact to the microsecond (no float round-trip).
- `SerializationError` (missing fields, bad JSON) also subclasses `ValueError`.
- `EnvironmentalState.metadata` must be JSON-compatible; the attribute returns a copy.

## Documentation

- [Design Specification](docs/design.md)
