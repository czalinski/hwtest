#!/usr/bin/env bash
# Build release artifacts for the dev machine (x86_64) and the Pis / Orange Pi (aarch64).
#
# Output: dist/*.whl  (abi3 wheels: one file per architecture serves Python >= 3.10)
#
# Cross-compilation uses maturin's zig linker, so no aarch64 sysroot is needed.
# Prerequisites (one time):
#   rustup target add aarch64-unknown-linux-gnu x86_64-unknown-linux-gnu
#   pip install "maturin>=1.9.4,<2" "ziglang>=0.13"
#
# Install on a Pi:
#   scp dist/hwtest_core-*aarch64*.whl hwtest@<pi>:/tmp/
#   ssh hwtest@<pi> 'source ~/.venv/bin/activate && pip install --force-reinstall /tmp/hwtest_core-*aarch64*.whl'
#
# Usage: scripts/build_release.sh [--target aarch64|x86_64|all]   (default: all)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${2:-all}"
[[ "${1:-}" == "--target" ]] || TARGET="all"

# Python packages backed by Rust (add hwtest-<pkg> here as packages are ported).
NATIVE_PACKAGES=(hwtest-core)

die() { echo "error: $*" >&2; exit 1; }
command -v cargo >/dev/null || die "cargo not found (install Rust: https://rustup.rs)"
command -v maturin >/dev/null || die "maturin not found (pip install 'maturin>=1.9.4,<2')"
python3 -c "import ziglang" 2>/dev/null || die "zig not found (pip install 'ziglang>=0.13')"
for t in x86_64 aarch64; do
    rustup target list --installed | grep -q "^$t-unknown-linux-gnu" \
        || die "missing target: rustup target add $t-unknown-linux-gnu"
done

build() {
    local pkg="$1"; shift
    (cd "$REPO_ROOT/$pkg" && maturin build --release --out "$REPO_ROOT/dist" "$@")
}

for pkg in "${NATIVE_PACKAGES[@]}"; do
    # zig links against an old glibc baseline for both architectures, so the
    # wheels run on any distro from glibc 2.17 up (not just the build host's).
    if [[ "$TARGET" == "all" || "$TARGET" == "x86_64" ]]; then
        build "$pkg" --target x86_64-unknown-linux-gnu --zig --compatibility manylinux2014
    fi
    if [[ "$TARGET" == "all" || "$TARGET" == "aarch64" ]]; then
        build "$pkg" --target aarch64-unknown-linux-gnu --zig --compatibility manylinux2014
    fi
done

echo
echo "Built:"
ls -1 "$REPO_ROOT/dist"/*.whl
