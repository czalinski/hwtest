# hwtest rack server for the Orange Pi 5 rack (configs/orange_pi_5_rack.yaml).
# Same image as deploy/pi5_mcc_intg_a/Dockerfile with a different default config;
# pick any rack with --build-arg RACK_CONFIG=configs/<rack>.yaml.
#
# Needs hardware access (SPI, GPIO):
#   docker run --privileged -v /etc/mcc:/etc/mcc:ro -p 8000:8000 ...
#
# Build on the Pi, or cross-build: docker buildx build --platform linux/arm64 ...

ARG RACK_CONFIG=configs/orange_pi_5_rack.yaml

# --- Stage 1: Rust binaries -----------------------------------------------------
FROM rust:1-slim-bookworm AS builder
WORKDIR /src
COPY Cargo.toml Cargo.lock rustfmt.toml ./
COPY crates/ ./crates/
COPY sql/ ./sql/
COPY hwtest-core/native/ ./hwtest-core/native/
RUN cargo build --release --locked -p hwtest-rack --bin hwtest-rack \
    && cargo build --release --locked -p hwtest-instruments --bin hwtest-hat-scan

# --- Stage 2: libdaqhats (MCC's C library, loaded by hwtest-rack at runtime) ----
FROM debian:bookworm-slim AS daqhats
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git ca-certificates pkg-config libgpiod-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/mccdaq/daqhats.git /daqhats \
    && make -C /daqhats/lib all \
    && make -C /daqhats/lib install

# --- Stage 3: runtime -------------------------------------------------------------
FROM debian:bookworm-slim
ARG RACK_CONFIG
LABEL description="hwtest rack server (Rust) for MCC DAQ HAT racks"
RUN apt-get update && apt-get install -y --no-install-recommends curl libgpiod2 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=daqhats /usr/local/lib/libdaqhats.so* /usr/local/lib/
RUN ldconfig
COPY --from=builder /src/target/release/hwtest-rack /src/target/release/hwtest-hat-scan /usr/local/bin/
COPY ${RACK_CONFIG} /etc/hwtest/rack.yaml

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Calibration files: mount ~/.config/hwtest/racks at /etc/hwtest/racks and add --serial.
CMD ["hwtest-rack", "serve", "/etc/hwtest/rack.yaml", "--host", "0.0.0.0", "--port", "8000"]
