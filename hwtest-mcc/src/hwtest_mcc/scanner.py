"""MCC DAQ HAT scanner for SPI bus enumeration.

This module provides a command-line tool to scan for MCC DAQ HATs on the SPI bus
by attempting to open each HAT type at each address and verifying communication.
This bypasses the EEPROM-based detection that the daqhats library normally uses,
which doesn't work on some SBCs like the Orange Pi 5.

The scanner performs actual hardware communication to verify each HAT type:
- MCC 118: Reads an analog input voltage
- MCC 134: Reads a thermocouple temperature
- MCC 152: Reads the digital I/O port

Usage:
    python -m hwtest_mcc.scanner
    # or if installed with console script:
    mcc-scan
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from typing import Any


@dataclass
class HatInfo:
    """Information about a detected MCC HAT.

    Attributes:
        address: HAT address on the stack (0-7).
        model: Model name (e.g., "MCC 118", "MCC 134", "MCC 152").
        serial: Serial number string from the HAT's EEPROM.
    """

    address: int
    model: str
    serial: str


# HAT types to probe and their daqhats class names
HAT_TYPES: list[tuple[str, str]] = [
    ("MCC 118", "mcc118"),
    ("MCC 134", "mcc134"),
    ("MCC 152", "mcc152"),
]


def _verify_mcc118(hat: Any) -> bool:
    """Verify MCC 118 by reading an analog input.

    Args:
        hat: The opened mcc118 HAT object.

    Returns:
        True if the HAT responds with valid data.
    """
    try:
        # Read channel 0 voltage. Valid range is -10V to +10V.
        voltage: float = hat.a_in_read(0)
        # Check for valid voltage (not NaN and within range)
        if math.isnan(voltage) or math.isinf(voltage):
            return False
        if not -15.0 <= voltage <= 15.0:  # Slightly wider than spec for tolerance
            return False
        return True
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _verify_mcc134(hat: Any) -> bool:
    """Verify MCC 134 by reading a thermocouple temperature.

    Args:
        hat: The opened mcc134 HAT object.

    Returns:
        True if the HAT responds with valid data.
    """
    try:
        # Need to configure thermocouple type first (Type K is common)
        # TC_TYPE_K = 1 in daqhats
        hat.tc_type_write(0, 1)
        # Read channel 0 temperature
        temp: float = hat.t_in_read(0)
        # Check for valid temperature (not NaN)
        # Open thermocouple reads as very large negative or OPEN_TC_VALUE
        # A real reading even with no probe should be a valid float in reasonable range
        if math.isnan(temp) or math.isinf(temp):
            return False
        # MCC 134 returns -9999.0 or similar for open thermocouple, which is still valid
        # as it means the HAT is responding. Very extreme values suggest no HAT.
        if temp < -300.0 or temp > 2000.0:
            # Check if it's the open thermocouple sentinel value
            if abs(temp - (-9999.0)) < 1.0:
                return True  # Open TC but HAT is present
            return False
        return True
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _verify_mcc152(hat: Any) -> bool:
    """Verify MCC 152 by reading the digital I/O port.

    Args:
        hat: The opened mcc152 HAT object.

    Returns:
        True if the HAT responds with valid data.
    """
    try:
        # Read the DIO input port. Should return 0-255.
        value: int = hat.dio_input_read_port()
        # Any value 0-255 is valid
        if not isinstance(value, int):
            return False
        if not 0 <= value <= 255:
            return False
        return True
    except Exception:  # pylint: disable=broad-exception-caught
        return False


# Map HAT class names to their verification functions
_VERIFY_FUNCTIONS: dict[str, Any] = {
    "mcc118": _verify_mcc118,
    "mcc134": _verify_mcc134,
    "mcc152": _verify_mcc152,
}


def _try_open_and_verify_hat(
    daqhats_module: Any,
    hat_class_name: str,
    address: int,
    verbose: bool = False,
) -> str | None:
    """Try to open a HAT and verify it responds correctly.

    Args:
        daqhats_module: The imported daqhats module.
        hat_class_name: Name of the HAT class (e.g., "mcc118").
        address: HAT address to probe (0-7).
        verbose: If True, print verification details.

    Returns:
        Serial number string if HAT was found and verified, None otherwise.
    """
    hat_class = getattr(daqhats_module, hat_class_name, None)
    if hat_class is None:
        return None

    try:
        hat = hat_class(address)
    except Exception:  # pylint: disable=broad-exception-caught
        return None

    # Check serial first - "00000000" means EEPROM wasn't read (likely no HAT)
    try:
        serial: str = hat.serial()
    except Exception:  # pylint: disable=broad-exception-caught
        serial = ""

    if serial == "00000000" or serial == "":
        if verbose:
            print(" (no valid EEPROM)", end="", file=sys.stderr)
        return None

    # Verify with hardware communication
    verify_func = _VERIFY_FUNCTIONS.get(hat_class_name)
    if verify_func is None:
        return None

    if not verify_func(hat):
        if verbose:
            print(" (opened but verification failed)", end="", file=sys.stderr)
        return None

    return serial


def scan_hats(
    addresses: list[int] | None = None,
    verbose: bool = False,
) -> list[HatInfo]:
    """Scan for MCC DAQ HATs on the SPI bus.

    Probes each address by attempting to open each HAT type and verifying
    communication. When a HAT responds with valid data, its model and
    serial number are recorded.

    Args:
        addresses: List of addresses to scan (0-7). Defaults to all addresses.
        verbose: If True, print progress messages during scanning.

    Returns:
        List of HatInfo objects for detected HATs.

    Raises:
        ImportError: If the daqhats library is not installed.
    """
    try:
        import daqhats  # type: ignore[import-not-found]  # pylint: disable=import-outside-toplevel
    except ImportError as exc:
        raise ImportError(
            "daqhats library is not installed. Install with: pip install daqhats"
        ) from exc

    if addresses is None:
        addresses = list(range(8))

    found: list[HatInfo] = []

    for address in addresses:
        if verbose:
            print(f"Scanning address {address}...", file=sys.stderr)

        for model_name, class_name in HAT_TYPES:
            if verbose:
                print(f"  Trying {model_name}...", end="", file=sys.stderr)

            serial = _try_open_and_verify_hat(daqhats, class_name, address, verbose)

            if serial is not None:
                if verbose:
                    print(f" VERIFIED (serial: {serial})", file=sys.stderr)
                found.append(HatInfo(address=address, model=model_name, serial=serial))
                # Found a HAT at this address, move to next address
                break
            if verbose:
                print(" not found", file=sys.stderr)

    return found


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point for the MCC HAT scanner.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 for success, 1 for errors).
    """
    parser = argparse.ArgumentParser(
        prog="mcc-scan",
        description=(
            "Scan for MCC DAQ HATs on the SPI bus. "
            "This bypasses EEPROM detection and verifies each HAT with "
            "actual hardware communication."
        ),
    )
    parser.add_argument(
        "-a",
        "--address",
        type=int,
        action="append",
        dest="addresses",
        metavar="ADDR",
        help="Specific address(es) to scan (0-7). Can be specified multiple times. "
        "Default: scan all addresses 0-7.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed progress during scanning.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only output found HATs, no header or summary.",
    )

    args = parser.parse_args(argv)

    # Validate addresses
    if args.addresses:
        for addr in args.addresses:
            if not 0 <= addr <= 7:
                print(f"Error: address must be 0-7, got {addr}", file=sys.stderr)
                return 1

    try:
        found = scan_hats(addresses=args.addresses, verbose=args.verbose)
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"Error during scan: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        print()
        print("MCC DAQ HAT Scan Results")
        print("=" * 40)

    if not found:
        if not args.quiet:
            print("No MCC HATs found.")
        return 0

    for hat in found:
        if args.quiet:
            print(f"{hat.address}\t{hat.model}\t{hat.serial}")
        else:
            print(f"Address {hat.address}: {hat.model} (serial: {hat.serial})")

    if not args.quiet:
        print()
        print(f"Found {len(found)} HAT(s).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
