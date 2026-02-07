#!/usr/bin/env python3
"""Coverage runner and analysis tool for hwtest.

This script runs pytest with coverage and generates reports that distinguish
between coverage from mocked tests vs non-mocked/integration tests.

Usage:
    # Run all unit tests with coverage
    python scripts/run_coverage.py

    # Run specific package
    python scripts/run_coverage.py --package hwtest-core

    # Run with integration tests
    python scripts/run_coverage.py --include-integration

    # Generate mocking analysis report
    python scripts/run_coverage.py --analyze-mocks

    # Open HTML report in browser
    python scripts/run_coverage.py --open
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import webbrowser
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent

# All packages in the monorepo
PACKAGES = [
    "hwtest-core",
    "hwtest-scpi",
    "hwtest-bkprecision",
    "hwtest-mcc",
    "hwtest-waveshare",
    "hwtest-sim-pi4-waveshare",
    "hwtest-rack",
    "hwtest-logger",
    "hwtest-db",
    "hwtest-nats",
    "hwtest-testcase",
    "hwtest-intg",
]


@dataclass
class CoverageStats:
    """Coverage statistics for a file or package."""

    total_lines: int = 0
    covered_lines: int = 0
    mocked_only_lines: int = 0
    integration_covered_lines: int = 0
    files: dict[str, dict] = field(default_factory=dict)

    @property
    def coverage_percent(self) -> float:
        """Calculate coverage percentage."""
        if self.total_lines == 0:
            return 100.0
        return (self.covered_lines / self.total_lines) * 100

    @property
    def mocked_only_percent(self) -> float:
        """Calculate percentage of coverage that is mock-only."""
        if self.covered_lines == 0:
            return 0.0
        return (self.mocked_only_lines / self.covered_lines) * 100


def run_pytest_with_coverage(
    packages: list[str] | None = None,
    include_integration: bool = False,
    verbose: bool = True,
) -> int:
    """Run pytest with coverage collection.

    For monorepos, runs tests per-package to avoid import conflicts,
    then combines coverage data.

    Args:
        packages: List of packages to test, or None for all.
        include_integration: Whether to include integration tests.
        verbose: Whether to show verbose output.

    Returns:
        0 if all tests passed, 1 otherwise.
    """
    # Ensure coverage directory exists
    coverage_dir = PROJECT_ROOT / "coverage"
    coverage_dir.mkdir(exist_ok=True)

    # Determine which packages to test
    test_packages = packages if packages else PACKAGES

    # Track results
    all_passed = True
    coverage_files = []

    # Run tests for each package separately
    for pkg in test_packages:
        pkg_path = PROJECT_ROOT / pkg
        test_path = pkg_path / "tests" / "unit"

        if not test_path.exists():
            continue

        print(f"\n{'='*60}")
        print(f"Testing: {pkg}")
        print(f"{'='*60}")

        # Coverage file for this package
        cov_file = coverage_dir / f".coverage.{pkg}"
        coverage_files.append(cov_file)

        # Build pytest command
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            f"--cov={pkg_path / 'src'}",
            f"--cov-report=",  # Suppress individual reports
            "--cov-context=test",
            str(test_path),
        ]

        if verbose:
            cmd.append("-v")

        # Set environment for coverage data file
        env = dict(os.environ)
        env["COVERAGE_FILE"] = str(cov_file)

        result = subprocess.run(cmd, cwd=pkg_path, env=env)

        if result.returncode != 0:
            all_passed = False

    # Add integration tests if requested
    if include_integration:
        intg_path = PROJECT_ROOT / "hwtest-intg" / "tests" / "integration"
        if intg_path.exists():
            print(f"\n{'='*60}")
            print("Testing: hwtest-intg (integration)")
            print(f"{'='*60}")

            cov_file = coverage_dir / ".coverage.intg"
            coverage_files.append(cov_file)

            cmd = [
                sys.executable,
                "-m",
                "pytest",
                "--cov",
                "--cov-report=",
                "--cov-context=test",
                str(intg_path),
            ]

            if verbose:
                cmd.append("-v")

            env = dict(os.environ)
            env["COVERAGE_FILE"] = str(cov_file)

            result = subprocess.run(cmd, cwd=PROJECT_ROOT / "hwtest-intg", env=env)

            if result.returncode != 0:
                all_passed = False

    # Combine coverage data
    print(f"\n{'='*60}")
    print("Combining coverage data...")
    print(f"{'='*60}")

    existing_cov_files = [str(f) for f in coverage_files if f.exists()]

    if existing_cov_files:
        # Combine all coverage files
        combine_cmd = [
            sys.executable,
            "-m",
            "coverage",
            "combine",
            "--keep",
        ] + existing_cov_files

        env = dict(os.environ)
        env["COVERAGE_FILE"] = str(coverage_dir / ".coverage")

        subprocess.run(combine_cmd, cwd=PROJECT_ROOT, env=env)

        # Generate reports
        report_env = dict(os.environ)
        report_env["COVERAGE_FILE"] = str(coverage_dir / ".coverage")

        # Terminal report
        subprocess.run(
            [sys.executable, "-m", "coverage", "report", "--show-missing"],
            cwd=PROJECT_ROOT,
            env=report_env,
        )

        # HTML report
        subprocess.run(
            [sys.executable, "-m", "coverage", "html", "-d", str(coverage_dir / "html")],
            cwd=PROJECT_ROOT,
            env=report_env,
        )

        # JSON report
        subprocess.run(
            [sys.executable, "-m", "coverage", "json", "-o", str(coverage_dir / "coverage.json")],
            cwd=PROJECT_ROOT,
            env=report_env,
        )

        print(f"\nCoverage HTML report: {coverage_dir / 'html' / 'index.html'}")

    return 0 if all_passed else 1


def analyze_mocked_coverage() -> CoverageStats:
    """Analyze coverage data to identify mock-only coverage.

    This reads the coverage JSON and identifies lines that are only
    covered by tests containing 'mock' in their name or using the
    @pytest.mark.uses_mock marker.

    Returns:
        CoverageStats with mock analysis.
    """
    coverage_json = PROJECT_ROOT / "coverage" / "coverage.json"

    if not coverage_json.exists():
        print(f"Coverage data not found at {coverage_json}")
        print("Run coverage first: python scripts/run_coverage.py")
        return CoverageStats()

    with open(coverage_json) as f:
        data = json.load(f)

    stats = CoverageStats()

    # Patterns that indicate a mocked test
    mock_patterns = ["mock", "Mock", "patch", "MagicMock", "create_mock"]

    for filename, file_data in data.get("files", {}).items():
        # Skip test files
        if "/tests/" in filename:
            continue

        executed_lines = set(file_data.get("executed_lines", []))
        missing_lines = set(file_data.get("missing_lines", []))
        contexts = file_data.get("contexts", {})

        total = len(executed_lines) + len(missing_lines)
        covered = len(executed_lines)

        stats.total_lines += total
        stats.covered_lines += covered

        # Analyze contexts for each line
        mocked_only = 0
        integration_covered = 0

        for line_str, line_contexts in contexts.items():
            is_mocked = False
            is_integration = False

            for ctx in line_contexts:
                # Check if context indicates mocked test
                if any(pattern in ctx for pattern in mock_patterns):
                    is_mocked = True
                elif "integration" in ctx.lower():
                    is_integration = True

            if is_mocked and not is_integration:
                mocked_only += 1
            if is_integration:
                integration_covered += 1

        stats.mocked_only_lines += mocked_only
        stats.integration_covered_lines += integration_covered

        # Store per-file stats
        rel_path = filename.replace(str(PROJECT_ROOT) + "/", "")
        stats.files[rel_path] = {
            "total": total,
            "covered": covered,
            "mocked_only": mocked_only,
            "integration": integration_covered,
            "percent": (covered / total * 100) if total > 0 else 100,
        }

    return stats


def print_mock_analysis(stats: CoverageStats) -> None:
    """Print mock coverage analysis report.

    Args:
        stats: Coverage statistics with mock analysis.
    """
    print("\n" + "=" * 80)
    print("MOCK COVERAGE ANALYSIS")
    print("=" * 80)
    print()
    print(f"Total lines:                {stats.total_lines:,}")
    print(f"Covered lines:              {stats.covered_lines:,} ({stats.coverage_percent:.1f}%)")
    print(f"Covered by mocks only:      {stats.mocked_only_lines:,} ({stats.mocked_only_percent:.1f}% of covered)")
    print(f"Covered by integration:     {stats.integration_covered_lines:,}")
    print()

    if stats.mocked_only_lines > 0:
        print("Files with significant mock-only coverage (candidates for integration tests):")
        print("-" * 80)

        # Sort by mock-only lines
        sorted_files = sorted(
            stats.files.items(),
            key=lambda x: x[1]["mocked_only"],
            reverse=True,
        )

        for filename, file_stats in sorted_files[:20]:
            if file_stats["mocked_only"] > 0:
                print(
                    f"  {filename}: {file_stats['mocked_only']} lines mock-only "
                    f"({file_stats['covered']}/{file_stats['total']} covered)"
                )

        print()
        print("Consider adding integration tests or simulators for these files.")


def generate_mock_report_html(stats: CoverageStats) -> None:
    """Generate an HTML report highlighting mock-only coverage.

    Args:
        stats: Coverage statistics with mock analysis.
    """
    html_path = PROJECT_ROOT / "coverage" / "mock_analysis.html"

    html = """<!DOCTYPE html>
<html>
<head>
    <title>Mock Coverage Analysis - hwtest</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 20px; }}
        h1 {{ color: #333; }}
        .summary {{ background: #f5f5f5; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
        .summary-stat {{ display: inline-block; margin-right: 30px; }}
        .stat-value {{ font-size: 24px; font-weight: bold; color: #2196F3; }}
        .stat-label {{ color: #666; font-size: 14px; }}
        .warning {{ color: #FF9800; }}
        table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
        th, td {{ text-align: left; padding: 12px; border-bottom: 1px solid #ddd; }}
        th {{ background: #f0f0f0; }}
        tr:hover {{ background: #f5f5f5; }}
        .mock-high {{ background: #fff3e0; }}
        .mock-medium {{ background: #fffde7; }}
        .bar {{ height: 8px; background: #e0e0e0; border-radius: 4px; }}
        .bar-fill {{ height: 100%; border-radius: 4px; }}
        .bar-covered {{ background: #4CAF50; }}
        .bar-mocked {{ background: #FF9800; }}
        .legend {{ margin-top: 20px; }}
        .legend-item {{ display: inline-block; margin-right: 20px; }}
        .legend-color {{ display: inline-block; width: 16px; height: 16px; border-radius: 3px; margin-right: 5px; vertical-align: middle; }}
    </style>
</head>
<body>
    <h1>Mock Coverage Analysis</h1>
    <p>Lines covered only by mocked tests are candidates for integration testing or simulators.</p>

    <div class="summary">
        <div class="summary-stat">
            <div class="stat-value">{coverage_percent:.1f}%</div>
            <div class="stat-label">Total Coverage</div>
        </div>
        <div class="summary-stat">
            <div class="stat-value {mock_class}">{mocked_only_percent:.1f}%</div>
            <div class="stat-label">Mock-Only Coverage</div>
        </div>
        <div class="summary-stat">
            <div class="stat-value">{total_lines:,}</div>
            <div class="stat-label">Total Lines</div>
        </div>
        <div class="summary-stat">
            <div class="stat-value">{mocked_only_lines:,}</div>
            <div class="stat-label">Mock-Only Lines</div>
        </div>
    </div>

    <div class="legend">
        <span class="legend-item"><span class="legend-color" style="background: #4CAF50;"></span> Integration/Real Coverage</span>
        <span class="legend-item"><span class="legend-color" style="background: #FF9800;"></span> Mock-Only Coverage</span>
        <span class="legend-item"><span class="legend-color" style="background: #e0e0e0;"></span> Not Covered</span>
    </div>

    <table>
        <tr>
            <th>File</th>
            <th>Coverage</th>
            <th>Lines</th>
            <th>Mock-Only</th>
            <th>Visual</th>
        </tr>
        {file_rows}
    </table>
</body>
</html>
"""

    file_rows = []
    sorted_files = sorted(
        stats.files.items(),
        key=lambda x: x[1]["mocked_only"],
        reverse=True,
    )

    for filename, file_stats in sorted_files:
        total = file_stats["total"]
        covered = file_stats["covered"]
        mocked = file_stats["mocked_only"]

        if total == 0:
            continue

        covered_pct = (covered / total) * 100
        mocked_pct = (mocked / total) * 100 if mocked > 0 else 0
        real_pct = covered_pct - mocked_pct

        row_class = ""
        if mocked > 10:
            row_class = "mock-high"
        elif mocked > 5:
            row_class = "mock-medium"

        bar_html = f"""
        <div class="bar">
            <div class="bar-fill bar-covered" style="width: {real_pct}%; display: inline-block;"></div>
            <div class="bar-fill bar-mocked" style="width: {mocked_pct}%; display: inline-block;"></div>
        </div>
        """

        file_rows.append(f"""
        <tr class="{row_class}">
            <td>{filename}</td>
            <td>{covered_pct:.1f}%</td>
            <td>{covered}/{total}</td>
            <td>{mocked}</td>
            <td style="width: 200px;">{bar_html}</td>
        </tr>
        """)

    mock_class = "warning" if stats.mocked_only_percent > 30 else ""

    html = html.format(
        coverage_percent=stats.coverage_percent,
        mocked_only_percent=stats.mocked_only_percent,
        mock_class=mock_class,
        total_lines=stats.total_lines,
        mocked_only_lines=stats.mocked_only_lines,
        file_rows="\n".join(file_rows),
    )

    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html)
    print(f"\nMock analysis report written to: {html_path}")


def main() -> int:
    """Main entry point.

    Returns:
        Exit code.
    """
    parser = argparse.ArgumentParser(
        description="Run coverage and analyze mock usage",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--package", "-p",
        action="append",
        dest="packages",
        help="Specific package(s) to test (can specify multiple)",
    )
    parser.add_argument(
        "--include-integration", "-i",
        action="store_true",
        help="Include integration tests in coverage",
    )
    parser.add_argument(
        "--analyze-mocks", "-m",
        action="store_true",
        help="Analyze and report mock-only coverage",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip running tests, only analyze existing coverage data",
    )
    parser.add_argument(
        "--open", "-o",
        action="store_true",
        help="Open HTML report in browser after running",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Minimal output",
    )

    args = parser.parse_args()

    # Ensure coverage directory exists
    coverage_dir = PROJECT_ROOT / "coverage"
    coverage_dir.mkdir(exist_ok=True)

    exit_code = 0

    # Run tests if not skipped
    if not args.skip_tests:
        exit_code = run_pytest_with_coverage(
            packages=args.packages,
            include_integration=args.include_integration,
            verbose=not args.quiet,
        )

    # Analyze mock coverage
    if args.analyze_mocks or args.skip_tests:
        stats = analyze_mocked_coverage()
        if not args.quiet:
            print_mock_analysis(stats)
        generate_mock_report_html(stats)

    # Open report in browser
    if args.open:
        html_report = PROJECT_ROOT / "coverage" / "html" / "index.html"
        if html_report.exists():
            webbrowser.open(f"file://{html_report}")
        else:
            print(f"HTML report not found at {html_report}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
