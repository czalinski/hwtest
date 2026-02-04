# Systemd Service Files

## Enterprise Server (InfluxDB + Grafana)

Install and enable the InfluxDB service:

```bash
# Copy service file
sudo cp hwtest-influxdb.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable and start
sudo systemctl enable hwtest-influxdb
sudo systemctl start hwtest-influxdb

# Check status
sudo systemctl status hwtest-influxdb
```

Access:
- InfluxDB: http://localhost:8086 (admin / hwtest-dev-password)
- Grafana: http://localhost:3000 (admin / hwtest-dev-password)

## UUT (Pi 4)

### Prerequisites

1. Install hwtest-uut package:
```bash
pip install --user /path/to/hwtest-uut
# Or from the repo:
pip install --user -e /home/pi/hwtest/hwtest-uut
```

2. Configure CAN interface in `/boot/firmware/config.txt`:
```
dtparam=spi=on
dtoverlay=spi1-1cs
dtoverlay=mcp251xfd,spi0-0,interrupt=25
dtoverlay=mcp251xfd,spi1-0,interrupt=24
```

### Install Services

```bash
# Copy service files
sudo cp can0.service /etc/systemd/system/
sudo cp hwtest-uut.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable and start CAN interface
sudo systemctl enable can0
sudo systemctl start can0

# Enable and start UUT simulator
sudo systemctl enable hwtest-uut
sudo systemctl start hwtest-uut

# Check status
sudo systemctl status hwtest-uut
```

### Failure Injection Behavior

The UUT simulator is configured with cyclic failure injection:

| Parameter | Value | Description |
|-----------|-------|-------------|
| `--failure-delay` | 300s | Time before failure activates (5 minutes) |
| `--failure-duration` | 10s | How long failure stays active |
| `--failure-offset` | 1.0V | Voltage offset during failure |

Cycle: Normal (5 min) → Failure (10s) → Normal (5 min) → Failure (10s) → ...

### Logs

```bash
# View UUT logs
sudo journalctl -u hwtest-uut -f

# View CAN logs
sudo journalctl -u can0
```

### REST API

UUT simulator API available at `http://<uut-ip>:8080`:
- `GET /health` - Health check
- `GET /failure/status` - Failure injection status
- `PUT /failure/config` - Configure failure injection
- `POST /failure/reset` - Reset failure timer
