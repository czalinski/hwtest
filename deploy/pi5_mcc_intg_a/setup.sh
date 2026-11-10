#!/bin/bash
# Setup script for hwtest Rack Server on Raspberry Pi 5
#
# Usage:
#   sudo ./setup.sh native    # Install native systemd service
#   sudo ./setup.sh docker    # Install Docker-based service
#   sudo ./setup.sh uninstall # Remove installation

set -e

INSTALL_DIR="/opt/hwtest"
SERVICE_NAME="hwtest-rack"
SERVICE_USER="hwtest"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

check_pi() {
    if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
        log_warn "This doesn't appear to be a Raspberry Pi. Continuing anyway..."
    fi
}

install_native() {
    log_info "Installing native hwtest-rack service..."

    # Create service user if it doesn't exist
    if ! id "$SERVICE_USER" &>/dev/null; then
        log_info "Creating service user: $SERVICE_USER"
        useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    fi

    # Add user to hardware groups
    log_info "Adding $SERVICE_USER to hardware groups..."
    usermod -a -G spi,gpio,i2c "$SERVICE_USER" 2>/dev/null || true

    # Create install directory
    log_info "Creating install directory: $INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$INSTALL_DIR/configs"
    mkdir -p "$INSTALL_DIR/logs"

    # Rust binaries: use a release build from scripts/build_release.sh if present,
    # otherwise build here (as the invoking user, so target/ isn't root-owned).
    BUILD_USER="${SUDO_USER:-root}"
    CARGO_BIN="$(getent passwd "$BUILD_USER" | cut -d: -f6)/.cargo/bin"
    if [[ -x "$CARGO_BIN/cargo" ]]; then
        export PATH="$CARGO_BIN:$PATH"
    fi
    ARCH="$(uname -m)"
    BIN_SRC="$REPO_ROOT/dist/$ARCH"
    if [[ ! -x "$BIN_SRC/hwtest-rack" ]]; then
        if ! command -v cargo >/dev/null 2>&1; then
            log_error "No prebuilt binaries in $BIN_SRC and no Rust toolchain. Either run"
            log_error "  scripts/build_release.sh --target $ARCH   (on the dev machine, then copy dist/)"
            log_error "or install Rust: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
            exit 1
        fi
        log_info "Building Rust binaries (this takes a while on a Pi)..."
        sudo -u "$BUILD_USER" env PATH="$PATH" cargo build --release --locked \
            --manifest-path "$REPO_ROOT/Cargo.toml" \
            -p hwtest-rack -p hwtest-runner -p hwtest-instruments --bins
        BIN_SRC="$REPO_ROOT/target/release"
    fi
    log_info "Installing binaries to $INSTALL_DIR/bin..."
    mkdir -p "$INSTALL_DIR/bin"
    for b in hwtest-rack hwtest-runner hwtest-hat-scan; do
        [[ -x "$BIN_SRC/$b" ]] && install -m 0755 "$BIN_SRC/$b" "$INSTALL_DIR/bin/"
    done

    # The rack server loads MCC's C library at runtime.
    if ! ldconfig -p | grep -q libdaqhats; then
        log_warn "libdaqhats not found. Install it from https://github.com/mccdaq/daqhats:"
        log_warn "  git clone https://github.com/mccdaq/daqhats && cd daqhats && sudo ./install.sh"
    fi

    # Python environment for test scripts (hwtest-core wheel + hwtest SDK).
    log_info "Creating Python virtual environment for test scripts..."
    python3 -m venv "$INSTALL_DIR/venv"
    "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
    WHEEL="$(ls "$REPO_ROOT"/dist/hwtest_core-*"$ARCH"*.whl 2>/dev/null | head -1 || true)"
    if [[ -z "$WHEEL" ]]; then
        command -v cargo >/dev/null 2>&1 || { log_error "no hwtest_core wheel in dist/ and no Rust toolchain"; exit 1; }
        WHEEL="$REPO_ROOT/hwtest-core"
    fi
    "$INSTALL_DIR/venv/bin/pip" install "$WHEEL" "$REPO_ROOT/hwtest-sdk"

    # Copy configuration
    log_info "Copying rack configuration..."
    cp "$REPO_ROOT/configs/pi5_mcc_intg_a_rack.yaml" "$INSTALL_DIR/configs/"
    mkdir -p /etc/hwtest/racks
    log_info "Rack calibration files go in /etc/hwtest/racks (hwtest-rack init / calibrate)."

    # Set ownership
    chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

    # Install systemd service
    log_info "Installing systemd service..."
    cp "$SCRIPT_DIR/hwtest-rack.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"

    log_info "Starting service..."
    systemctl start "$SERVICE_NAME"

    # Check status
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log_info "Service started successfully!"
        log_info "Dashboard available at: http://$(hostname -I | awk '{print $1}'):8080"
    else
        log_error "Service failed to start. Check: journalctl -u $SERVICE_NAME"
        exit 1
    fi
}

install_docker() {
    log_info "Installing Docker-based hwtest-rack service..."

    # Check Docker is installed
    if ! command -v docker &>/dev/null; then
        log_error "Docker is not installed. Install it first:"
        log_error "  curl -fsSL https://get.docker.com | sh"
        log_error "  sudo usermod -aG docker $USER"
        exit 1
    fi

    # Check docker compose
    if ! docker compose version &>/dev/null; then
        log_error "Docker Compose is not installed or not working."
        exit 1
    fi

    # Build and start
    log_info "Building Docker image..."
    cd "$SCRIPT_DIR"
    docker compose build

    log_info "Starting container..."
    docker compose up -d

    # Check status
    sleep 5
    if docker compose ps | grep -q "running"; then
        log_info "Container started successfully!"
        log_info "Dashboard available at: http://$(hostname -I | awk '{print $1}'):8080"
    else
        log_error "Container failed to start. Check: docker compose logs"
        exit 1
    fi
}

uninstall() {
    log_info "Uninstalling hwtest-rack..."

    # Stop and disable systemd service
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        log_info "Stopping systemd service..."
        systemctl stop "$SERVICE_NAME"
    fi
    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl disable "$SERVICE_NAME"
    fi
    rm -f /etc/systemd/system/hwtest-rack.service
    systemctl daemon-reload

    # Stop Docker container
    if command -v docker &>/dev/null; then
        cd "$SCRIPT_DIR" 2>/dev/null || true
        docker compose down 2>/dev/null || true
    fi

    # Remove install directory (optional - ask user)
    if [[ -d "$INSTALL_DIR" ]]; then
        read -p "Remove $INSTALL_DIR? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -rf "$INSTALL_DIR"
            log_info "Removed $INSTALL_DIR"
        fi
    fi

    log_info "Uninstall complete."
}

show_usage() {
    echo "Usage: $0 {native|docker|uninstall}"
    echo ""
    echo "Commands:"
    echo "  native    Install as native systemd service (recommended)"
    echo "  docker    Install as Docker container"
    echo "  uninstall Remove installation"
    exit 1
}

# Main
check_root
check_pi

case "${1:-}" in
    native)
        install_native
        ;;
    docker)
        install_docker
        ;;
    uninstall)
        uninstall
        ;;
    *)
        show_usage
        ;;
esac
