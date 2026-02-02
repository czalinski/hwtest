#!/usr/bin/env python3
"""Combined ADC + CAN example demonstrating dual-HAT operation.

This example shows how to use the ADS1263 ADC and MCP2515 CAN controller
simultaneously on a Raspberry Pi with both HATs connected.

Hardware Setup:
- RS485 CAN HAT: Uses kernel driver on spi0.0 (CE0/GPIO8)
- High-Precision AD HAT: Uses spidev0.1 with software CS on GPIO22

The key to coexistence is using software-controlled chip select for the
ADC to avoid conflict with the CAN controller's hardware CS.

Requirements:
    pip install RPi.GPIO spidev python-can

Usage:
    python adc_can_combined.py
"""

from __future__ import annotations

import time

from hwtest_uut import (
    Ads1263,
    Ads1263Config,
    Ads1263Gain,
    CanConfig,
    CanInterface,
    CanMessage,
)


def main() -> None:
    """Read ADC channels and send values over CAN."""
    # Configure ADC with software chip select
    adc_config = Ads1263Config(
        spi_bus=0,
        spi_device=1,  # Use spidev0.1 to avoid conflict with CAN on spidev0.0
        cs_pin=22,     # Software-controlled chip select
        drdy_pin=17,
        reset_pin=18,
        vref=2.5,
        gain=Ads1263Gain.GAIN_1,
    )

    # Configure CAN interface
    can_config = CanConfig(
        interface="can0",
        bitrate=500000,
    )

    adc = Ads1263(config=adc_config)
    can = CanInterface(config=can_config)

    try:
        print("Opening ADC...")
        adc.open()
        chip_id = adc.get_chip_id()
        print(f"ADC chip ID: 0x{chip_id:02X}")

        print("Opening CAN interface...")
        can.open()
        print("CAN interface ready")

        print("\nReading ADC channels and sending over CAN...")
        print("Press Ctrl+C to stop\n")

        while True:
            # Read all 10 ADC channels
            voltages = adc.read_all_channels()

            print("ADC Readings:")
            for i, voltage in enumerate(voltages):
                print(f"  CH{i}: {voltage:+.6f} V")

            # Pack first 4 channel readings into a CAN message
            # Each voltage scaled to 16-bit signed integer (mV resolution)
            data = bytearray(8)
            for i in range(4):
                mv = int(voltages[i] * 1000)  # Convert to millivolts
                mv = max(-32768, min(32767, mv))  # Clamp to int16 range
                data[i * 2] = (mv >> 8) & 0xFF
                data[i * 2 + 1] = mv & 0xFF

            msg = CanMessage(
                arbitration_id=0x100,
                data=bytes(data),
            )
            can.send(msg)
            print(f"Sent CAN message: ID=0x{msg.arbitration_id:03X} Data={data.hex()}")

            print()
            time.sleep(1.0)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        adc.close()
        can.close()
        print("Cleanup complete")


if __name__ == "__main__":
    main()
