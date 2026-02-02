# hwtest Rack Server for Raspberry Pi 5 with MCC HATs
#
# IMPORTANT: This container requires privileged mode for MCC HAT access:
#   docker run --privileged -v /etc/mcc:/etc/mcc:ro ...
#
# Or use docker-compose with the provided docker-compose.yaml

FROM python:3.11-slim-bookworm

LABEL maintainer="hwtest"
LABEL description="hwtest rack server for Pi 5 MCC integration testing"

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /opt/hwtest

# Copy packages (order matters for layer caching)
COPY hwtest-core/ ./hwtest-core/
COPY hwtest-mcc/ ./hwtest-mcc/
COPY hwtest-rack/ ./hwtest-rack/
COPY configs/ ./configs/

# Install Python packages
RUN pip install --no-cache-dir \
    ./hwtest-core \
    ./hwtest-mcc \
    ./hwtest-rack

# Expose rack server port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# Run the rack server
CMD ["hwtest-rack", "/opt/hwtest/configs/pi5_mcc_intg_a_rack.yaml", "--host", "0.0.0.0", "--port", "8080"]
