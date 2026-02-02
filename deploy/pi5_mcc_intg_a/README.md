# Pi 5 MCC Integration Test Rack A

Deployment files for the Raspberry Pi 5 MCC DAQ integration test rack.

## Hardware

- Raspberry Pi 5
- MCC 152 (address 0) - Digital I/O + Analog Out
- MCC 134 (address 1) - Thermocouple DAQ
- MCC 118 (address 4) - Voltage DAQ
- Waveshare RS485 CAN HAT B (modified for CE1)

## Quick Start

### Option 1: Native Installation (Recommended)

```bash
# On the Pi 5, clone the repo
git clone https://github.com/czalinski/hwtest.git
cd hwtest/deploy/pi5_mcc_intg_a

# Install as systemd service
sudo ./setup.sh native

# Check status
sudo systemctl status hwtest-rack
journalctl -u hwtest-rack -f
```

### Option 2: Docker Installation

```bash
# Install Docker if not already installed
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in

# Clone and deploy
git clone https://github.com/czalinski/hwtest.git
cd hwtest/deploy/pi5_mcc_intg_a
sudo ./setup.sh docker

# Or manually:
docker compose build
docker compose up -d
docker compose logs -f
```

## Accessing the Dashboard

Once running, access the web dashboard from any browser:

```
http://<pi-ip-address>:8080
```

API endpoints:
- `/` - HTML dashboard
- `/health` - Health check
- `/status` - Full rack status (JSON)
- `/instruments` - List instruments (JSON)
- `/instruments/{name}` - Instrument details (JSON)
- `/docs` - OpenAPI documentation

## Management Commands

```bash
# Native service
sudo systemctl status hwtest-rack
sudo systemctl restart hwtest-rack
sudo systemctl stop hwtest-rack
journalctl -u hwtest-rack -f

# Docker
docker compose ps
docker compose logs -f
docker compose restart
docker compose down
```

## Uninstall

```bash
sudo ./setup.sh uninstall
```

## Configuration

The rack configuration is at `/opt/hwtest/configs/pi5_mcc_intg_a_rack.yaml`.

To modify, edit and restart:
```bash
sudo nano /opt/hwtest/configs/pi5_mcc_intg_a_rack.yaml
sudo systemctl restart hwtest-rack
```
