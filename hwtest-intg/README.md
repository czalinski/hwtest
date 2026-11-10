# hwtest-intg — Pi 5 rack ↔ Pi 4 UUT bench

The test cases for the integration bench. They're written the same way as any
product's tests: YAML definitions in `configs/`, short Python scripts in
`scripts/`.

| File | What it is |
|------|------------|
| `configs/pi5_mcc_intg_a_rack.yaml` | Rack class: MCC 152 / 134 / 118 HATs and their logical channels |
| `configs/pi5_bench_a_station.yaml` | Station for `hwtest-runner` (rack, UUT URL, test list) |
| `configs/voltage_echo_monitor.yaml` + `scripts/voltage_echo_monitor.py` | Rack → UUT ADC → UUT DAC → rack echo loop (functional / HASS / HALT) |
| `configs/analog_paths.yaml` + `scripts/analog_paths.py` | Each analog direction on its own |
| `configs/can_communication.yaml` + `scripts/can_communication.py` | UUT heartbeat (0x100, 10 Hz) and echo (+0x10) |
| `scripts/_bench.py` | Bench wiring helpers (and the emulated "wires") |
| `tests/test_bench_emulated.py` | Runs all scripts with no hardware |

Wiring: MCC 152 AO0 → UUT ADS1256 CH0; UUT DAC8532 CH0 → MCC 118 CH0.

## On the bench

```bash
# Pi 4 (UUT)
hwtest-uut-sim --port 8080 --can-interface can0 --waveshare-adda --no-gpio

# Pi 5 (rack): one script directly ...
export UUT_URL=http://192.168.68.4:8080
python scripts/voltage_echo_monitor.py --serial 001              # one pass
python scripts/voltage_echo_monitor.py --serial 001 --mode hass  # until failure / Ctrl-C
python scripts/can_communication.py

# ... or the operator web page
hwtest-runner configs/pi5_bench_a_station.yaml --port 8000
```

InfluxDB logging in the echo test is off unless `TELEMETRY_ENABLED=1` (with
`INFLUXDB_TOKEN`). CSV logs go to `CSV_LOG_DIR` (default `./logs`).

## Without hardware

```bash
python -m pytest tests/        # rack emulated, UUT simulator in-process, virtual CAN bus

# or by hand:
hwtest-uut-sim --port 8080 --emulate --no-can &
UUT_URL=http://127.0.0.1:8080 python scripts/voltage_echo_monitor.py --emulate
```

Fault injection: `curl -X PUT $UUT_URL/failure/config -H 'content-type: application/json'
-d '{"delay_seconds": 5, "duration_seconds": 0, "voltage_offset": 1.0}'`.
After 5 s the UUT DAC output is 1 V high, and the echo test fails.

## Calibration

`uut_adc_scale_factor` and `mcc118_scale_factor` come from the rack instance
file (`hwtest-rack calibrate --serial 001`). Both should be about 1.0. The old
Python ADS1256 driver read half the true voltage, which an earlier
`uut_adc_scale_factor` of about 2.0 made up for. **Re-calibrate after
upgrading.**
