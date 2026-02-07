"""Command-line interface for hwtest-rack.

Provides commands for rack management, including calibration.

Usage:
    # Initialize a new rack instance
    hwtest-rack init --class pi5_mcc_intg_a --serial 001 --description "Lab bench A"

    # Run calibration
    hwtest-rack calibrate --serial 001

    # Show rack instance info
    hwtest-rack info --serial 001
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from hwtest_rack.calibrate import calibrate_mcc118, CalibrationResult
from hwtest_rack.instance import (
    CalibrationMetadata,
    RackInstanceConfig,
    find_instance_config,
    load_instance_config,
)


def setup_logging(debug: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize a new rack instance configuration."""
    print(f"Creating new rack instance config:")
    print(f"  Rack class: {args.rack_class}")
    print(f"  Serial number: {args.serial}")
    print(f"  Description: {args.description or '(none)'}")

    # Check if config already exists
    existing = find_instance_config(args.rack_class, args.serial)
    if existing and not args.force:
        print(f"\nError: Instance config already exists at {existing}")
        print("Use --force to overwrite")
        return 1

    # Create new instance config
    config = RackInstanceConfig.create_new(
        serial_number=args.serial,
        rack_class=args.rack_class,
        description=args.description or "",
    )

    # Save to file
    if args.output:
        save_path = Path(args.output)
    else:
        save_path = None  # Use default location

    saved_path = config.save(save_path)
    print(f"\nSaved to: {saved_path}")
    print("\nNext steps:")
    print(f"  1. Run calibration: hwtest-rack calibrate --serial {args.serial}")
    print(f"  2. Or manually edit calibration values in the YAML file")

    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Run calibration for a rack instance."""
    # Find or create instance config
    try:
        config = load_instance_config(args.rack_class, args.serial)
        print(f"Loaded instance config from: {config.source_path}")
    except FileNotFoundError:
        if args.create:
            print(f"Creating new instance config for {args.rack_class} serial {args.serial}")
            config = RackInstanceConfig.create_new(
                serial_number=args.serial,
                rack_class=args.rack_class,
            )
        else:
            print(f"Error: Instance config not found for class '{args.rack_class}' serial '{args.serial}'")
            print("Use --create to create a new config, or run 'hwtest-rack init' first")
            return 1

    print(f"\nCalibrating rack: {config.instance.rack_class} #{config.instance.serial_number}")
    print(f"  MCC 152 address: {args.mcc152_address}, channel: {args.mcc152_channel}")
    print(f"  MCC 118 address: {args.mcc118_address}, channel: {args.mcc118_channel}")
    print(f"  Reference voltages: {args.voltages}")
    print()

    # Run calibration
    result = calibrate_mcc118(
        mcc152_address=args.mcc152_address,
        mcc152_channel=args.mcc152_channel,
        mcc118_address=args.mcc118_address,
        mcc118_channel=args.mcc118_channel,
        reference_voltages=args.voltages,
        settling_time=args.settling_time,
        samples_per_point=args.samples,
    )

    if not result.success:
        print(f"Calibration failed: {result.error}")
        return 1

    print(f"\nCalibration Results:")
    print(f"  Scale factor: {result.scale_factor:.4f}")
    print(f"  Reference points:")
    for point in result.points:
        print(
            f"    {point.reference_voltage:.2f}V -> "
            f"{point.measured_voltage:.3f}V (scale: {point.scale_factor:.4f})"
        )

    if args.dry_run:
        print("\nDry run - not saving results")
        return 0

    # Update config with calibration results
    config = RackInstanceConfig(
        instance=config.instance,
        calibration={
            **config.calibration,
            "mcc118_scale_factor": result.scale_factor,
        },
        metadata=CalibrationMetadata(
            calibrated_at=result.timestamp,
            calibrated_by="hwtest-rack calibrate",
            reference_instrument=result.reference_instrument,
            notes=result.notes,
        ),
        source_path=config.source_path,
    )

    saved_path = config.save()
    print(f"\nCalibration saved to: {saved_path}")

    return 0


def cmd_info(args: argparse.Namespace) -> int:
    """Show rack instance information."""
    try:
        config = load_instance_config(args.rack_class, args.serial)
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        return 1

    print(f"Rack Instance: {config.instance.rack_class} #{config.instance.serial_number}")
    print(f"  Description: {config.instance.description or '(none)'}")
    print(f"  Config file: {config.source_path}")
    print()
    print("Calibration:")
    for name, value in sorted(config.calibration.items()):
        print(f"  {name}: {value}")
    print()
    print("Calibration Metadata:")
    print(f"  Calibrated at: {config.metadata.calibrated_at or '(unknown)'}")
    print(f"  Calibrated by: {config.metadata.calibrated_by or '(unknown)'}")
    print(f"  Reference: {config.metadata.reference_instrument or '(unknown)'}")
    print(f"  Notes: {config.metadata.notes or '(none)'}")

    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """List available rack instance configs."""
    from hwtest_rack.instance import _get_search_paths

    paths = _get_search_paths()
    found_any = False

    for search_dir in paths:
        if not search_dir.is_dir():
            continue

        configs = list(search_dir.glob("*.yaml")) + list(search_dir.glob("*.yml"))
        if configs:
            print(f"{search_dir}:")
            for config_path in sorted(configs):
                try:
                    config = RackInstanceConfig.from_yaml(config_path)
                    print(
                        f"  {config_path.name}: "
                        f"{config.instance.rack_class} #{config.instance.serial_number}"
                    )
                    found_any = True
                except Exception as exc:
                    print(f"  {config_path.name}: (error: {exc})")

    if not found_any:
        print("No rack instance configs found.")
        print("\nSearch paths:")
        for path in paths:
            print(f"  {path}")
        print("\nCreate one with: hwtest-rack init --class <rack_class> --serial <serial>")

    return 0


def parse_voltages(value: str) -> list[float]:
    """Parse comma-separated voltage values."""
    return [float(v.strip()) for v in value.split(",")]


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="hwtest-rack management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize a new rack instance config")
    init_parser.add_argument(
        "--class", dest="rack_class", required=True,
        help="Rack class identifier (e.g., pi5_mcc_intg_a)"
    )
    init_parser.add_argument(
        "--serial", required=True,
        help="Serial number for this rack instance"
    )
    init_parser.add_argument(
        "--description", "-d",
        help="Description of this rack instance"
    )
    init_parser.add_argument(
        "--output", "-o",
        help="Output path (default: ~/.config/hwtest/racks/<class>_<serial>.yaml)"
    )
    init_parser.add_argument(
        "--force", "-f", action="store_true",
        help="Overwrite existing config"
    )

    # calibrate command
    cal_parser = subparsers.add_parser("calibrate", help="Run calibration")
    cal_parser.add_argument(
        "--class", dest="rack_class", default="pi5_mcc_intg_a",
        help="Rack class identifier (default: pi5_mcc_intg_a)"
    )
    cal_parser.add_argument(
        "--serial", required=True,
        help="Serial number of rack instance to calibrate"
    )
    cal_parser.add_argument(
        "--create", action="store_true",
        help="Create instance config if it doesn't exist"
    )
    cal_parser.add_argument(
        "--mcc152-address", type=int, default=0,
        help="MCC 152 HAT address (default: 0)"
    )
    cal_parser.add_argument(
        "--mcc152-channel", type=int, default=0,
        help="MCC 152 analog output channel (default: 0)"
    )
    cal_parser.add_argument(
        "--mcc118-address", type=int, default=4,
        help="MCC 118 HAT address (default: 4)"
    )
    cal_parser.add_argument(
        "--mcc118-channel", type=int, default=0,
        help="MCC 118 analog input channel (default: 0)"
    )
    cal_parser.add_argument(
        "--voltages", type=parse_voltages, default=[1.0, 2.5, 4.0],
        help="Reference voltages to use, comma-separated (default: 1.0,2.5,4.0)"
    )
    cal_parser.add_argument(
        "--settling-time", type=float, default=0.1,
        help="Settling time after voltage change in seconds (default: 0.1)"
    )
    cal_parser.add_argument(
        "--samples", type=int, default=10,
        help="Number of samples to average per point (default: 10)"
    )
    cal_parser.add_argument(
        "--dry-run", action="store_true",
        help="Run calibration but don't save results"
    )

    # info command
    info_parser = subparsers.add_parser("info", help="Show rack instance information")
    info_parser.add_argument(
        "--class", dest="rack_class", default="pi5_mcc_intg_a",
        help="Rack class identifier (default: pi5_mcc_intg_a)"
    )
    info_parser.add_argument(
        "--serial",
        help="Serial number (if not specified, shows first found for class)"
    )

    # list command
    subparsers.add_parser("list", help="List available rack instance configs")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    setup_logging(args.debug)

    if args.command == "init":
        return cmd_init(args)
    elif args.command == "calibrate":
        return cmd_calibrate(args)
    elif args.command == "info":
        return cmd_info(args)
    elif args.command == "list":
        return cmd_list(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
