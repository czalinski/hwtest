"""MCC DAQ HAT scanner for SPI bus enumeration.

This module provides a command-line tool to scan for MCC DAQ HATs on the SPI bus
by attempting to open each HAT type at each address. This bypasses the EEPROM-based
detection that the daqhats library normally uses, which doesn't work on some SBCs
like the Orange Pi 5.

Usage:
    python -m hwtest_mcc.scanner
    # or if installed with console script:
    mcc-scan
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any


@dataclass
class HatInfo:
    """Information about a detected MCC HAT."""

    address: int
    model: str
    serial: str


# HAT types to probe and their daqhats class names
HAT_TYPES: list[tuple[str, str]] = [
    ("MCC 118", "mcc118"),
    ("MCC 134", "mcc134"),
    ("MCC 152", "mcc152"),
]


def _try_open_hat(daqhats_module: Any, hat_class_name: str, address: int) -> str | None:
    """Try to open a HAT at the given address and return its serial if successful.

    Args:
        daqhats_module: The imported daqhats module.
        hat_class_name: Name of the HAT class (e.g., "mcc118").
        address: HAT address to probe (0-7).

    Returns:
        Serial number string if HAT was found, None otherwise.
    """
    hat_class = getattr(daqhats_module, hat_class_name, None)
    if hat_class is None:
        return None

    try:
        hat = hat_class(address)
        serial: str = hat.serial()
        return serial
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def scan_hats(
    addresses: list[int] | None = None,
    verbose: bool = False,
) -> list[HatInfo]:
    """Scan for MCC DAQ HATs on the SPI bus.

    Probes each address by attempting to open each HAT type. When a HAT
    responds successfully, its model and serial number are recorded.

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

            serial = _try_open_hat(daqhats, class_name, address)

            if serial is not None:
                if verbose:
                    print(f" FOUND (serial: {serial})", file=sys.stderr)
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
            "This bypasses EEPROM detection and probes each address directly."
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
