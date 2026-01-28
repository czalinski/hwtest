# hwtest-rack Docker image for Orange Pi 5 / ARM64
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

# Copy packages
COPY hwtest-core ./hwtest-core
COPY hwtest-mcc ./hwtest-mcc
COPY hwtest-rack ./hwtest-rack
COPY configs ./configs

# Install Python packages
RUN pip install --no-cache-dir \
    ./hwtest-core \
    ./hwtest-mcc \
    ./hwtest-rack

# Expose API port
EXPOSE 8000

# Default command
CMD ["hwtest-rack", "/app/configs/orange_pi_5_rack.yaml", "--host", "0.0.0.0", "--port", "8000"]
