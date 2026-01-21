# hwtest-core

Core library for hwtest providing data types and interface definitions.

## Overview

This library contains foundational types and interfaces used by other hwtest projects. It has minimal external dependencies and does not include runnable services or applications.

## Installation

```bash
pip install -e .
```

## Usage

```python
from hwtest_core import ...  # TBD
```

## Development

### Setup

```bash
cd hwtest-core
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Testing

```bash
pytest
```

## Documentation

- [Design Specification](docs/design.md)
