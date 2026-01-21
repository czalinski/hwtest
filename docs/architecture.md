# Architecture

## Overview

This repository supports HASS (Highly Accelerated Stress Screening) and HALT (Highly Accelerated Life Testing) for manufactured electronic hardware through instrument automation.

During HALT/HASS testing, environmental stresses (vibration and thermal) are applied to a Unit Under Test (UUT) while multiple measurements are monitored to ensure they remain within expected norms. The expected norms vary depending on the current environmental state and device state, requiring a dynamic monitoring approach.

## Primary Use Case

The system is designed for hardware reliability testing where:

1. **Environmental stresses** are applied to the UUT (vibration, thermal cycling, etc.)
2. **Multiple measurements** are continuously collected from the UUT and test environment
3. **Telemetry data** is logged to a telemetry server for analysis and record-keeping
4. **Test cases** exercise the UUT under various conditions
5. **Environmental states** are recorded as discrete states that define expected measurement norms
6. **Monitors** continuously evaluate telemetry values against state-dependent thresholds

## System Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Test Execution                                  │
│  ┌─────────────┐    ┌─────────────────┐    ┌─────────────────────────────┐  │
│  │  Test Case  │───>│ Environmental   │───>│ State-Dependent Thresholds  │  │
│  │  Executor   │    │ State Manager   │    │ (expected norms per state)  │  │
│  └─────────────┘    └─────────────────┘    └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Measurement Layer                                 │
│  ┌─────────────┐    ┌─────────────────┐    ┌─────────────────────────────┐  │
│  │ Instruments │───>│   Telemetry     │───>│     Telemetry Server        │  │
│  │   & UUT     │    │   Collection    │    │     (logging/storage)       │  │
│  └─────────────┘    └─────────────────┘    └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Monitoring Layer                                  │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                         Monitors                                     │    │
│  │  - Continuously observe telemetry values                            │    │
│  │  - Compare against thresholds for current environmental state       │    │
│  │  - Determine pass/fail status                                       │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Core Concepts

### Unit Under Test (UUT)

The electronic hardware being tested. May have multiple measurement points and controllable states.

### Environmental State

A discrete state representing the current test conditions (e.g., "room temperature", "thermal stress +85C", "vibration 10G"). Each state defines what measurement values are considered acceptable.

#### Transition States

When changing between environmental states, a **transition state** is used where measurements are ignored. This accounts for:

- Instrument latency (measurements lag behind actual conditions)
- Physical settling time (thermal mass, vibration ramp-up/down)
- Avoiding false failures during state changes

Keeping overall system latency low reduces the duration of transition states, tightening test windows and improving fault detection accuracy.

### Telemetry

Continuous measurement data collected from the UUT and test environment. Logged to a telemetry server for real-time monitoring and post-test analysis.

#### Telemetry Server Requirements

The telemetry server is a critical component. Requirements:

- **Low latency**: Minimize time from message send to client receipt. Lab instruments inherently have latency; additional system latency compounds the problem, leading to false failures or missed faults.
- **Common timestamps**: Messages should have consistent timestamps for correlating measurements from multiple sources.
- **Multi-value analysis**: Fault determination may require analyzing multiple telemetry values together in context of the current environmental state.
- **Persistence**: Historical data needed for post-test analysis.

#### Technology Selection: NATS JetStream

**NATS JetStream** is the selected telemetry transport. Rationale:

- **Sub-millisecond latency**: Significantly lower than Kafka/RedPanda
- **Lightweight**: ~50MB footprint, suitable for SBC deployments (Orange Pi 5, Beelink)
- **Built-in persistence**: JetStream provides durable message storage
- **Exactly-once semantics**: Message deduplication and delivery guarantees
- **Native timestamps**: Server-side timestamping with configurable clock sources
- **Simple clustering**: Easy to deploy in small-scale lab environments

The smaller ecosystem compared to Kafka is mitigated by the hwtest core libraries, which will abstract the messaging layer. Developers using this toolset interact with hwtest APIs, not NATS directly.

#### Abstraction Principle

The core libraries will provide:

- Abstract interfaces for telemetry publishing and subscribing
- Concrete NATS JetStream implementation
- Potential for alternative backends if requirements change

```
┌─────────────────────────────────────────┐
│         Application Code                │
├─────────────────────────────────────────┤
│     hwtest-core Telemetry API           │  ← Developers use this
├─────────────────────────────────────────┤
│   NATS JetStream Implementation         │  ← Hidden from developers
└─────────────────────────────────────────┘
```

### Monitors

Components that continuously evaluate telemetry data against state-dependent thresholds. A monitor determines whether current measurements are within acceptable norms for the current environmental state.

### Thresholds/Expected Norms

Acceptable ranges or limits for measurements that vary by environmental state. For example, current draw limits may differ between room temperature and thermal stress conditions.

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

### Telemetry Publishing

- Measurement sources publish telemetry data to a central telemetry server
- Monitors subscribe to relevant telemetry streams

### State Notifications

- Test case executor broadcasts environmental state changes
- Monitors receive state updates to adjust their threshold evaluation

### Monitor Results

- Monitors report pass/fail status based on current telemetry and state
- Results may trigger test case decisions or alerts

## Data Flow

1. **Measurement Acquisition**: Instruments and UUT produce raw measurement data
2. **Telemetry Logging**: Measurements are timestamped and sent to telemetry server
3. **State Context**: Test executor sets current environmental state
4. **Threshold Lookup**: Monitors retrieve applicable thresholds for current state
5. **Evaluation**: Monitors compare telemetry values against thresholds
6. **Result Reporting**: Pass/fail determinations are logged and/or acted upon

## Latency and Timestamp Requirements

### Why Latency Matters

System latency directly impacts test quality:

- **False failures**: High latency causes measurements to be evaluated against the wrong environmental state (e.g., evaluating thermal stress readings against room temperature thresholds)
- **Missed faults**: Transient faults may occur and clear before they are observed
- **Longer transition states**: High latency requires longer "ignore" windows during state transitions, reducing effective test coverage
- **Correlation errors**: When analyzing multiple values together, latency differences between sources cause misaligned data

### Latency Budget

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        End-to-End Latency Budget                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Instrument ──> Collector ──> Telemetry Server ──> Monitor ──> Action   │
│      T1            T2               T3               T4          T5     │
│                                                                         │
│  T1: Instrument latency (hardware-dependent, often 10-100ms)            │
│  T2: Collection/serialization (<5ms target)                             │
│  T3: Server publish-to-subscribe (<10ms target)                         │
│  T4: Monitor processing (<5ms target)                                   │
│  T5: Action/alert dispatch (<5ms target)                                │
│                                                                         │
│  Software-controlled total (T2+T3+T4+T5): <25ms target                  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

The software-controlled latency budget targets <25ms end-to-end. Instrument latency (T1) is hardware-dependent and typically dominates; minimizing software latency keeps the overall budget tight.

### Timestamp Requirements

#### Source Timestamps

All telemetry messages must include a **source timestamp** captured as close to the measurement as possible:

- Timestamp at point of acquisition, not at publish time
- Microsecond resolution minimum
- Monotonic within a single source

#### Clock Synchronization

For multi-source correlation, clocks must be synchronized:

- **Requirement**: All nodes synchronized via NTP or PTP
- **Target accuracy**: <1ms clock skew between nodes
- **PTP preferred** for sub-millisecond requirements on dedicated test networks

#### Timestamp Fields

Each telemetry message should carry:

| Field | Description |
|-------|-------------|
| `source_timestamp` | When the measurement was taken (source clock) |
| `publish_timestamp` | When the message was sent to the server |
| `server_timestamp` | When the server received the message (optional, for latency diagnostics) |

### Latency Monitoring

The system should track latency metrics for diagnostics:

- Publish-to-receive latency per message
- 95th/99th percentile latencies over time windows
- Alerts when latency exceeds thresholds
