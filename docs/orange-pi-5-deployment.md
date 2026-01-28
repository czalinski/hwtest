# Orange Pi 5 Max Deployment Guide

Deploy hwtest-rack to an Orange Pi 5 Max 8GB running Armbian with Docker.

## Prerequisites

- Orange Pi 5 Max 8GB with Armbian installed
- Network connectivity
- MCC DAQ HAT stack installed (MCC 152, MCC 118, MCC 134)
- Local Docker registry available at `registry.local:5000` (adjust as needed)

## 1. Host System Setup

### 1.1 Install Docker

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sudo sh

# Add current user to docker group
sudo usermod -aG docker $USER

# Apply group membership (or logout/login)
newgrp docker

# Verify installation
docker --version
```

### 1.2 Enable I2C and SPI for MCC HATs

MCC DAQ HATs communicate via I2C and SPI. Enable these interfaces:

```bash
# Install required tools
sudo apt install -y i2c-tools

# Enable I2C and SPI via armbian-config
sudo armbian-config
# Navigate to: System > Hardware > Enable i2c1, spi1

# Or manually add to /boot/armbianEnv.txt:
sudo tee -a /boot/armbianEnv.txt << 'EOF'
overlays=i2c1 spi1
EOF

# Reboot to apply
sudo reboot
```

### 1.3 Verify Hardware Access

After reboot, verify I2C devices are visible:

```bash
# List I2C buses
ls /dev/i2c-*

# Scan for MCC HATs (typically on i2c-1)
sudo i2cdetect -y 1

# Expected addresses for MCC HATs:
# 0x50-0x57: EEPROM (address depends on HAT address jumpers)
# 0x60-0x67: MCC 118/134 ADC
# 0x20-0x27: MCC 152 GPIO expander
```

### 1.4 Install daqhats Library on Host

The daqhats library must be installed on the host for EEPROM configuration:

```bash
# Install build dependencies
sudo apt install -y git python3-pip python3-dev

# Clone and install daqhats
cd /tmp
git clone https://github.com/mccdaq/daqhats.git
cd daqhats
sudo ./install.sh

# Verify HAT detection
daqhats_list_boards
```

## 2. Build Docker Image

### 2.1 Create Dockerfile

Create `Dockerfile` in the repository root:

```dockerfile
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
```

### 2.2 Create .dockerignore

```bash
cat > .dockerignore << 'EOF'
.git
.venv
__pycache__
*.pyc
*.egg-info
.pytest_cache
.mypy_cache
docs
EOF
```

### 2.3 Build and Push Image

```bash
# Build for ARM64
docker build -t hwtest-rack:latest .

# Tag for local registry
docker tag hwtest-rack:latest registry.local:5000/hwtest-rack:latest

# Push to local registry
docker push registry.local:5000/hwtest-rack:latest
```

## 3. Deploy to Orange Pi 5

### 3.1 Pull Image

On the Orange Pi 5:

```bash
# Pull from local registry
docker pull registry.local:5000/hwtest-rack:latest
```

### 3.2 Create Rack Configuration

Create or copy the rack configuration:

```bash
# Create config directory
sudo mkdir -p /opt/hwtest/configs

# Copy configuration (adjust paths as needed)
sudo tee /opt/hwtest/configs/rack.yaml << 'EOF'
rack:
  id: "orange-pi-5-integration"
  description: "Orange Pi 5 Max integration test rack"

instruments:
  dio_controller:
    driver: "hwtest_mcc.mcc152:create_instrument"
    identity:
      manufacturer: "Measurement Computing"
      model: "MCC 152"
    kwargs:
      address: 0
      source_id: "dio_controller"
      dio_channels:
        - id: 0
          name: "relay_dut_power"
          direction: "OUTPUT"
          initial_value: false
        - id: 1
          name: "relay_load"
          direction: "OUTPUT"
          initial_value: false
        - id: 2
          name: "sensor_door"
          direction: "INPUT"
        - id: 3
          name: "sensor_estop"
          direction: "INPUT"
      analog_channels:
        - id: 0
          name: "control_voltage"
          initial_voltage: 0.0
        - id: 1
          name: "reference_voltage"
          initial_voltage: 2.5

  voltage_daq:
    driver: "hwtest_mcc.mcc118:create_instrument"
    identity:
      manufacturer: "Measurement Computing"
      model: "MCC 118"
    kwargs:
      address: 1
      sample_rate: 1000.0
      source_id: "voltage_daq"
      channels:
        - id: 0
          name: "dut_voltage"
        - id: 1
          name: "dut_current_sense"
        - id: 2
          name: "supply_voltage"
        - id: 3
          name: "reference_voltage"

  thermocouple_daq:
    driver: "hwtest_mcc.mcc134:create_instrument"
    identity:
      manufacturer: "Measurement Computing"
      model: "MCC 134"
    kwargs:
      address: 4
      source_id: "thermocouple_daq"
      update_interval: 1.0
      channels:
        - id: 0
          name: "chamber_temp"
          tc_type: "TYPE_K"
        - id: 1
          name: "dut_temp"
          tc_type: "TYPE_K"
        - id: 2
          name: "ambient_temp"
          tc_type: "TYPE_T"
        - id: 3
          name: "heatsink_temp"
          tc_type: "TYPE_K"
EOF
```

### 3.3 Run Container

```bash
# Run with hardware access
docker run -d \
  --name hwtest-rack \
  --restart unless-stopped \
  --privileged \
  -v /dev/i2c-1:/dev/i2c-1 \
  -v /dev/spidev1.0:/dev/spidev1.0 \
  -v /dev/spidev1.1:/dev/spidev1.1 \
  -v /opt/hwtest/configs:/app/configs:ro \
  -p 8000:8000 \
  registry.local:5000/hwtest-rack:latest \
  hwtest-rack /app/configs/rack.yaml --host 0.0.0.0 --port 8000
```

**Device access notes:**
- `--privileged` grants full hardware access (can be restricted with specific device flags)
- I2C and SPI devices must be mapped into the container
- Device paths may vary; check with `ls /dev/i2c-* /dev/spidev*`

### 3.4 Alternative: Restricted Device Access

For tighter security, use explicit device access instead of `--privileged`:

```bash
docker run -d \
  --name hwtest-rack \
  --restart unless-stopped \
  --device /dev/i2c-1 \
  --device /dev/spidev1.0 \
  --device /dev/spidev1.1 \
  --device /dev/gpiochip0 \
  --device /dev/gpiochip1 \
  -v /opt/hwtest/configs:/app/configs:ro \
  -p 8000:8000 \
  registry.local:5000/hwtest-rack:latest \
  hwtest-rack /app/configs/rack.yaml --host 0.0.0.0 --port 8000
```

## 4. Create systemd Service (Optional)

For automatic startup without Docker's restart policy:

```bash
sudo tee /etc/systemd/system/hwtest-rack.service << 'EOF'
[Unit]
Description=hwtest Rack Service
After=docker.service
Requires=docker.service

[Service]
Type=simple
Restart=always
RestartSec=10
ExecStartPre=-/usr/bin/docker stop hwtest-rack
ExecStartPre=-/usr/bin/docker rm hwtest-rack
ExecStart=/usr/bin/docker run \
  --name hwtest-rack \
  --privileged \
  -v /dev/i2c-1:/dev/i2c-1 \
  -v /dev/spidev1.0:/dev/spidev1.0 \
  -v /dev/spidev1.1:/dev/spidev1.1 \
  -v /opt/hwtest/configs:/app/configs:ro \
  -p 8000:8000 \
  registry.local:5000/hwtest-rack:latest \
  hwtest-rack /app/configs/rack.yaml --host 0.0.0.0 --port 8000
ExecStop=/usr/bin/docker stop hwtest-rack

[Install]
WantedBy=multi-user.target
EOF

# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable hwtest-rack
sudo systemctl start hwtest-rack
```

## 5. Verification

### 5.1 Check Container Status

```bash
# View container logs
docker logs -f hwtest-rack

# Check container status
docker ps -a | grep hwtest-rack
```

### 5.2 Test API Endpoints

```bash
# Health check
curl http://localhost:8000/health

# Get rack status
curl http://localhost:8000/status | python3 -m json.tool

# List instruments
curl http://localhost:8000/instruments | python3 -m json.tool

# Get specific instrument
curl http://localhost:8000/instruments/voltage_daq | python3 -m json.tool
```

### 5.3 Open Dashboard

Browse to `http://<orange-pi-ip>:8000/` to view the HTML dashboard.

## 6. Troubleshooting

### HATs Not Detected

```bash
# Check if daqhats sees the boards
docker exec hwtest-rack daqhats_list_boards

# Verify I2C devices are accessible
docker exec hwtest-rack i2cdetect -y 1

# Check HAT EEPROM addresses match configuration
# Address 0 = jumpers off: 0x50
# Address 1 = A0 jumper:   0x51
# Address 4 = A2 jumper:   0x54
```

### Permission Denied

```bash
# Ensure user is in i2c and spi groups on host
sudo usermod -aG i2c,spi $USER

# Check device permissions
ls -la /dev/i2c-* /dev/spidev*

# Try running with --privileged to isolate permission issues
```

### Container Won't Start

```bash
# Check logs for errors
docker logs hwtest-rack

# Run interactively for debugging
docker run -it --rm \
  --privileged \
  -v /dev/i2c-1:/dev/i2c-1 \
  registry.local:5000/hwtest-rack:latest \
  /bin/bash

# Inside container, test Python imports
python3 -c "import daqhats; print(daqhats.hat_list())"
```

### Wrong Instrument Detected

If rack reports identity mismatch:
1. Verify HAT address jumpers match config
2. Check `daqhats_list_boards` output
3. Ensure HAT EEPROMs are programmed (run `daqhats_read_eeproms` on host)

## 7. MCC HAT Address Reference

| Address | A2 | A1 | A0 | Binary |
|---------|----|----|-----|--------|
| 0       | 0  | 0  | 0   | 000    |
| 1       | 0  | 0  | 1   | 001    |
| 2       | 0  | 1  | 0   | 010    |
| 3       | 0  | 1  | 1   | 011    |
| 4       | 1  | 0  | 0   | 100    |
| 5       | 1  | 0  | 1   | 101    |
| 6       | 1  | 1  | 0   | 110    |
| 7       | 1  | 1  | 1   | 111    |

Default configuration uses:
- **MCC 152** at address 0 (no jumpers)
- **MCC 118** at address 1 (A0 jumper installed)
- **MCC 134** at address 4 (A2 jumper installed)

## 8. Updating

To update the deployment:

```bash
# On build machine: rebuild and push
docker build -t hwtest-rack:latest .
docker tag hwtest-rack:latest registry.local:5000/hwtest-rack:latest
docker push registry.local:5000/hwtest-rack:latest

# On Orange Pi 5: pull and restart
docker pull registry.local:5000/hwtest-rack:latest
docker stop hwtest-rack
docker rm hwtest-rack
# Run container again (or restart systemd service)
sudo systemctl restart hwtest-rack
```
