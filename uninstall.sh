#!/usr/bin/env bash
# Remove IP Changer. Use --purge to remove configuration and history too.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_USER=""
PURGE=0
DRY_RUN=0
IS_TERMUX=0

usage() {
    cat <<'EOF'
IP Changer uninstaller

Usage:
  sudo ./uninstall.sh [options]

Options:
  --user USER   remove the installation for USER
  --purge       also delete configuration, state and logs
  --dry-run     show what would be removed
  -h, --help    show this help

The Tor package, the Tor group and torrc backups are left in place.
The managed ControlPort block is removed from torrc.
EOF
}

log() { printf '[*] %s\n' "$*"; }
warn() { printf '[!] %s\n' "$*" >&2; }
die() { printf '[x] %s\n' "$*" >&2; exit 1; }

run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '    would run: %s\n' "$*"
    else
        "$@"
    fi
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --user)
            [ "$#" -ge 2 ] || die "--user needs a value"
            TARGET_USER="$2"
            shift 2
            ;;
        --user=*)
            TARGET_USER="${1#*=}"
            shift
            ;;
        --purge)
            PURGE=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown option: $1 (try --help)"
            ;;
    esac
done

if [ -n "${TERMUX_VERSION:-}" ] || [[ "${PREFIX:-}" == *com.termux* ]]; then
    IS_TERMUX=1
fi

EUID_VALUE="$(id -u)"
CURRENT_USER="$(id -un)"
if [ -z "$TARGET_USER" ]; then
    if [ "$EUID_VALUE" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
        TARGET_USER="$SUDO_USER"
    else
        TARGET_USER="$CURRENT_USER"
    fi
fi

if [ "$IS_TERMUX" -eq 1 ]; then
    PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
    TARGET_HOME="${HOME:?HOME is not set}"
    INSTALL_DIR="$PREFIX/share/ip-changer"
    BIN_DIR="$PREFIX/bin"
    TORRC="$PREFIX/etc/tor/torrc"
    SUDO=()
else
    if [ "$EUID_VALUE" -ne 0 ]; then
        command -v sudo >/dev/null 2>&1 || die "sudo is required when not running as root"
        SUDO=(sudo)
    else
        SUDO=()
    fi
    if [ "$TARGET_USER" = "root" ]; then
        TARGET_HOME="/root"
    else
        TARGET_HOME="$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6 || true)"
        [ -n "$TARGET_HOME" ] || TARGET_HOME="$(eval echo "~$TARGET_USER")"
    fi
    INSTALL_DIR="/opt/ip-changer"
    BIN_DIR="/usr/local/bin"
    TORRC="/etc/tor/torrc"
    if [ "$EUID_VALUE" -ne 0 ]; then
        INSTALL_DIR="$TARGET_HOME/.local/share/ip-changer"
        BIN_DIR="$TARGET_HOME/.local/bin"
    fi
fi

log "Stopping IP Changer..."
if [ "$IS_TERMUX" -eq 1 ]; then
    if command -v sv >/dev/null 2>&1; then
        run sv down ipchanger || true
    fi
    run rm -rf "$PREFIX/var/service/ipchanger"
    run rm -f "$TARGET_HOME/.termux/boot/start-ipchanger"
else
    if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
        run "${SUDO[@]}" systemctl disable --now ipchanger.service || true
        run "${SUDO[@]}" rm -f /etc/systemd/system/ipchanger.service
        run "${SUDO[@]}" systemctl daemon-reload || true
    fi
fi

log "Removing the managed torrc block..."
if [ -f "$TORRC" ]; then
    if [ "$IS_TERMUX" -eq 1 ] || [ "$EUID_VALUE" -ne 0 ]; then
        run python3 "$SCRIPT_DIR/scripts/configure_tor.py" "$TORRC" --remove || warn "could not update torrc"
    else
        run "${SUDO[@]}" python3 "$SCRIPT_DIR/scripts/configure_tor.py" "$TORRC" --remove || warn "could not update torrc"
    fi
fi

log "Removing files..."
run rm -f "$BIN_DIR/ipchanger"
run rm -rf "$INSTALL_DIR"

if [ "$PURGE" -eq 1 ]; then
    log "Purging configuration, state and logs..."
    run rm -rf "$TARGET_HOME/.config/ipchanger"
    run rm -rf "$TARGET_HOME/.local/state/ipchanger"
fi

if [ "$IS_TERMUX" -eq 0 ] && command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    run "${SUDO[@]}" systemctl try-restart tor.service || true
fi

log "Done."
if [ "$PURGE" -eq 0 ]; then
    log "Configuration and logs were kept; use --purge to remove them."
fi
