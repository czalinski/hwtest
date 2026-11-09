# hwtest-rack Docker image for Orange Pi 5 / ARM64

# --- Stage 1: build the Rust-backed hwtest-core wheel (abi3, works on any Python >= 3.10) ---
FROM rust:1-slim-bookworm AS core-builder
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/maturin && /opt/maturin/bin/pip install --no-cache-dir "maturin>=1.9.4,<2"
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY crates/ ./crates/
COPY hwtest-core/ ./hwtest-core/
RUN cd hwtest-core && /opt/maturin/bin/maturin build --release --out /wheels

# --- Stage 2: runtime image ---
FROM python:3.11-slim-bookworm

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    python3-dev \
    i2c-tools \
    && rm -rf /var/lib/apt/lists/*

# Install daqhats library
WORKDIR /tmp
RUN git clone https://github.com/mccdaq/daqhats.git \
    && cd daqhats \
    && pip install . \
    && cd / \
    && rm -rf /tmp/daqhats

# Create app directory
WORKDIR /app

# Copy packages (hwtest-core arrives as a prebuilt wheel)
COPY --from=core-builder /wheels /wheels
COPY hwtest-mcc ./hwtest-mcc
COPY hwtest-rack ./hwtest-rack
COPY configs ./configs

# Install Python packages
RUN pip install --no-cache-dir \
    /wheels/hwtest_core-*.whl \
    ./hwtest-mcc \
    ./hwtest-rack

# Expose API port
EXPOSE 8000

# Default command
CMD ["hwtest-rack", "/app/configs/orange_pi_5_rack.yaml", "--host", "0.0.0.0", "--port", "8000"]
