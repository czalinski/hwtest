"""``hwtest`` command: check YAML files, run test scripts."""

from __future__ import annotations

import argparse
import runpy
import sys
from collections.abc import Sequence

from hwtest_core._native import check_spec_files


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    p = argparse.ArgumentParser(prog="hwtest", description="hwtest tools")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="validate test definition, rack, station and calibration YAML")
    c.add_argument("files", nargs="+")
    r = sub.add_parser("run", help="run a test script (same as python SCRIPT ...)")
    r.add_argument("script")
    r.add_argument("args", nargs=argparse.REMAINDER)
    a = p.parse_args(argv)
    if a.command == "check":
        failed = 0
        for path, kind, error in check_spec_files(a.files):
            if error is None:
                print(f"{path}: OK ({kind})")
            else:
                failed += 1
                print(error)
        return 1 if failed else 0
    sys.argv = [a.script, *a.args]
    runpy.run_path(a.script, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
