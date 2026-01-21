# Architecture

## Overview

This repository supports HASS and HALT hardware testing through instrument automation.

## Project Structure

```
hwtest/
├── docs/                 # Shared requirements and architecture
├── hwtest-core/          # Core library (data types, interfaces)
└── [future projects]/    # Additional subprojects
```

## Design Principles

- Modular architecture with clear boundaries between projects
- Core library provides shared types and interfaces with minimal dependencies
- Higher-level projects depend on hwtest-core

## hwtest-core

The foundational library containing:

- Data types for hardware test operations
- Interface definitions (protocols/ABCs)
- No runnable services or applications
- Minimal external dependencies

## Dependencies

```
hwtest-core (no external deps)
    ^
    |
[future projects depend on core]
```

## Communication Patterns

TBD

## Data Flow

TBD
