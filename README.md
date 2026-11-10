# hwtest

Hardware test automation tools for HASS (Highly Accelerated Stress Screening) and HALT (Highly Accelerated Life Testing) via instrument automation.

## Overview

This monorepo contains multiple projects for hardware testing automation, targeting Linux systems including single-board computers (Orange Pi 5, Beelink) and Docker containers.

## Projects

The framework is Rust; test cases are short Python scripts plus YAML specifications.

| Project | Description |
|---------|-------------|
| [crates/hwtest-core](crates/hwtest-core/) | Data types, bounds, monitor engine, binary streaming protocol |
| [crates/hwtest-spec](crates/hwtest-spec/) | YAML test definitions, rack / instance / station configuration |
| [crates/hwtest-nats](crates/hwtest-nats/), [hwtest-logger](crates/hwtest-logger/), [hwtest-db](crates/hwtest-db/) | NATS JetStream streaming, CSV/InfluxDB loggers, SQLite results |
| [crates/hwtest-instruments](crates/hwtest-instruments/) | Instrument drivers and emulators (MCC DAQ HATs, Waveshare AD/DA, SCPI/BK PSU, CAN, MCP23017) |
| [crates/hwtest-rack](crates/hwtest-rack/) | Rack runtime and `hwtest-rack` server |
| [crates/hwtest-run](crates/hwtest-run/), [hwtest-runner](crates/hwtest-runner/) | Run engine; `hwtest-runner` station web service |
| [crates/hwtest-uut-sim](crates/hwtest-uut-sim/) | `hwtest-uut-sim` UUT simulator and client |
| [hwtest-core](hwtest-core/) | Python extension module (PyO3 bindings over the crates) |
| [hwtest-sdk](hwtest-sdk/) | `import hwtest`: the API test scripts use |
| [hwtest-intg](hwtest-intg/) | Test cases for the integration bench |

## Documentation

- [Coding Requirements](docs/coding-requirements.md)
- [System Requirements](docs/system-requirements.md)
- [Architecture](docs/architecture.md)

## License

This project is released into the public domain under the [Unlicense](LICENSE).
