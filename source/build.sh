#!/bin/bash
# Build and install script for VPN Split Tunnel

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Check dependencies
check_deps() {
    log_info "Checking dependencies..."

    local missing=()
    for cmd in meson ninja python3 pip3; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing dependencies: ${missing[*]}"
        log_info "Install with: sudo apt install meson ninja-build python3 python3-pip"
        exit 1
    fi

    # Check Python packages
    for pkg in gi cairo; do
        if ! python3 -c "import $pkg" 2>/dev/null; then
            log_warn "Python package '$pkg' not found, installing..."
            pip3 install --user pycairo pygobject
        fi
    done

    log_info "Dependencies OK"
}

# Build
build() {
    log_info "Building with meson..."
    cd "$PROJECT_ROOT"

    if [[ -d builddir ]]; then
        log_info "Cleaning previous build..."
        rm -rf builddir
    fi

    meson setup builddir --prefix=/usr
    meson compile -C builddir

    log_info "Build complete"
}

# Install
install() {
    log_info "Installing..."
    cd "$PROJECT_ROOT"

    if [[ ! -d builddir ]]; then
        log_error "Build directory not found. Run './build.sh build' first."
        exit 1
    fi

    sudo meson install -C builddir

    # Update icon cache
    if command -v gtk-update-icon-cache &>/dev/null; then
        sudo gtk-update-icon-cache -f /usr/share/icons/hicolor
    fi

    # Update desktop database
    if command -v update-desktop-database &>/dev/null; then
        sudo update-desktop-database /usr/share/applications
    fi

    # Reload systemd
    systemctl --user daemon-reload

    log_info "Installation complete"
    log_info "Run 'vpn-split-tunnel' to start"
}

# Uninstall
uninstall() {
    log_info "Uninstalling..."
    cd "$PROJECT_ROOT"

    if [[ -d builddir ]]; then
        sudo meson install -C builddir --destdir=/ --no-rebuild 2>/dev/null | \
        while read -r file; do
            sudo rm -f "$file"
        done
    fi

    # Remove the system-wide policy file (per-user config under
    # ~/.config/vpn-split-tunnel/ is intentionally left alone).
    sudo rm -f /usr/share/polkit-1/actions/com.github.sina.vpn-split-tunnel.policy

    log_info "Uninstall complete"
}

# Development run
run_dev() {
    log_info "Running in development mode..."
    cd "$PROJECT_ROOT"

    # Run straight from the source tree. No editable pip install: the
    # meson-python backend rebuilds the whole package for it here, and there
    # is nothing to build -- the module runs from src/ as-is.
    PYTHONPATH="$PROJECT_ROOT/src" python3 -m vpn_split_tunnel "$@"
}

# Run tests
test() {
    log_info "Running tests..."
    cd "$PROJECT_ROOT"

    if ! python3 -c "import pytest" 2>/dev/null; then
        log_error "pytest is not installed for python3"
        log_info "Install with: pip3 install pytest (or: source venv/bin/activate && pip install pytest)"
        exit 1
    fi
    PYTHONPATH="$PROJECT_ROOT/src" python3 -m pytest tests/ -q
}

# Clean
clean() {
    log_info "Cleaning..."
    cd "$PROJECT_ROOT"
    rm -rf builddir dist *.egg-info .pytest_cache .coverage coverage.xml
    rm -rf src/vpn_split_tunnel.egg-info
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    log_info "Clean complete"
}

# Main
case "${1:-}" in
    build)
        check_deps
        build
        ;;
    install)
        check_deps
        build
        install
        ;;
    uninstall)
        uninstall
        ;;
    run)
        run_dev
        ;;
    test)
        test
        ;;
    clean)
        clean
        ;;
    *)
        echo "Usage: $0 {build|install|uninstall|run|test|clean}"
        echo ""
        echo "Commands:"
        echo "  build     - Build the project"
        echo "  install   - Build and install system-wide"
        echo "  uninstall - Remove installed files"
        echo "  run       - Run in development mode"
        echo "  test      - Run tests"
        echo "  clean     - Clean build artifacts"
        exit 1
        ;;
esac