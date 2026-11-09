# hwtest

Hardware test automation tools for HASS (Highly Accelerated Stress Screening) and HALT (Highly Accelerated Life Testing) via instrument automation.

## Overview

This monorepo contains multiple projects for hardware testing automation, targeting Linux systems including single-board computers (Orange Pi 5, Beelink) and Docker containers.

## Projects

| Project | Description |
|---------|-------------|
| [crates/hwtest-core](crates/hwtest-core/) | Rust implementation of core data types and wire protocols |
| [hwtest-core](hwtest-core/) | Python package (PyO3 bindings) with data types and interface definitions |

## Documentation

- [Coding Requirements](docs/coding-requirements.md)
- [System Requirements](docs/system-requirements.md)
- [Architecture](docs/architecture.md)

## License

This project is released into the public domain under the [Unlicense](LICENSE).
