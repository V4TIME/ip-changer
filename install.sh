#!/usr/bin/env bash
# IP Changer installer for Debian/Ubuntu/Kali/Parrot, Arch, Fedora,
# openSUSE, Alpine, macOS and Termux.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="ip-changer"
DISPLAY_NAME="IP Changer"
INTERVAL=""
TARGET_USER=""
INSTALL_SERVICE=1
DRY_RUN=0
NEEDS_RELOGIN=0
TOR_GROUP=""
CONTROL_PORT=9051
SOCKS_PORT=9050
IS_TERMUX=0

usage() {
    cat <<'EOF'
IP Changer installer

Usage:
  sudo ./install.sh [options]

Options:
  --interval TIME      rotation interval (90, 5m, 1h30m; default 5m)
  --user USER          install for USER when run as root
  --no-service         install files and config, do not enable a service
  --dry-run            show the detected plan, change nothing
  -h, --help           show this help

Examples:
  sudo ./install.sh --interval 5m
  ./install.sh --interval 2m                 # normal user, uses sudo
  ./install.sh --no-service --interval 10m   # just install the CLI/panel
  ./install.sh --dry-run

Termux:
  ./install.sh --interval 5m
  # No root or systemd is required. The installer uses termux-services if
  # available, otherwise it starts Tor in the background and prints the
  # one-line command to keep it running after a reboot.
EOF
}

log() { printf '[*] %s\n' "$*"; }
ok() { printf '[+] %s\n' "$*"; }
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
        --interval)
            [ "$#" -ge 2 ] || die "--interval needs a value"
            INTERVAL="$2"
            shift 2
            ;;
        --interval=*)
            INTERVAL="${1#*=}"
            shift
            ;;
        --user)
            [ "$#" -ge 2 ] || die "--user needs a value"
            TARGET_USER="$2"
            shift 2
            ;;
        --user=*)
            TARGET_USER="${1#*=}"
            shift
            ;;
        --no-service)
            INSTALL_SERVICE=0
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
OS_NAME="$(uname -s)"
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
    case "$OS_NAME" in
        Linux)
            INSTALL_DIR="/opt/ip-changer"
            BIN_DIR="/usr/local/bin"
            TORRC="/etc/tor/torrc"
            ;;
        Darwin)
            INSTALL_DIR="/usr/local/opt/ip-changer"
            BIN_DIR="/usr/local/bin"
            BREW_PREFIX=""
            if command -v brew >/dev/null 2>&1; then
                BREW_PREFIX="$(brew --prefix 2>/dev/null || true)"
            fi
            if [ -n "$BREW_PREFIX" ]; then
                TORRC="$BREW_PREFIX/etc/tor/torrc"
            elif [ -d /opt/homebrew/etc/tor ]; then
                TORRC="/opt/homebrew/etc/tor/torrc"
            else
                TORRC="/usr/local/etc/tor/torrc"
            fi
            ;;
        *)
            die "unsupported operating system: $OS_NAME"
            ;;
    esac
    if [ "$TARGET_USER" = "root" ]; then
        TARGET_HOME="/root"
    else
        TARGET_HOME="$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6 || true)"
        [ -n "$TARGET_HOME" ] || TARGET_HOME="$(eval echo "~$TARGET_USER")"
    fi
    if [ "$EUID_VALUE" -ne 0 ]; then
        INSTALL_DIR="$TARGET_HOME/.local/share/ip-changer"
        BIN_DIR="$TARGET_HOME/.local/bin"
    fi
fi

[ -d "$TARGET_HOME" ] || die "home directory for $TARGET_USER was not found: $TARGET_HOME"

if [ -z "$INTERVAL" ] && [ -t 0 ]; then
    printf 'Rotation interval [5m]: '
    read -r INTERVAL
fi
INTERVAL="${INTERVAL:-5m}"

as_target() {
    if [ "$CURRENT_USER" = "$TARGET_USER" ]; then
        "$@"
        return
    fi
    if [ "$EUID_VALUE" -eq 0 ]; then
        if [ "$TARGET_USER" = "root" ]; then
            "$@"
        else
            command sudo -u "$TARGET_USER" -H "$@"
        fi
    else
        command sudo -u "$TARGET_USER" -H "$@"
    fi
}

install_packages() {
    if [ "$IS_TERMUX" -eq 1 ]; then
        run pkg update -y || warn "pkg update failed; continuing with installed packages"
        run pkg install -y python tor termux-services || warn "could not install termux-services; Tor will be started manually"
        return
    fi
    if command -v apt-get >/dev/null 2>&1; then
        run "${SUDO[@]}" apt-get update
        run "${SUDO[@]}" apt-get install -y tor python3 ca-certificates
    elif command -v pacman >/dev/null 2>&1; then
        run "${SUDO[@]}" pacman -Sy --needed --noconfirm tor python ca-certificates
    elif command -v dnf >/dev/null 2>&1; then
        run "${SUDO[@]}" dnf install -y tor python3 ca-certificates
    elif command -v zypper >/dev/null 2>&1; then
        run "${SUDO[@]}" zypper --non-interactive install tor python3 ca-certificates
    elif command -v apk >/dev/null 2>&1; then
        run "${SUDO[@]}" apk add --no-cache tor python3 ca-certificates
    elif command -v brew >/dev/null 2>&1; then
        run brew install tor python3
    else
        warn "no supported package manager found; install tor and python3 manually"
    fi
}

log "IP Changer installer"
log "target user: $TARGET_USER"
log "install dir: $INSTALL_DIR"
log "binary dir:  $BIN_DIR"
log "torrc:       $TORRC"
log "interval:    $INTERVAL"
log "service:     $([ "$INSTALL_SERVICE" -eq 1 ] && echo yes || echo no)"

if [ "$DRY_RUN" -eq 1 ]; then
    log "dry-run: no packages, files, torrc or services will be changed"
    exit 0
fi

log "Installing Tor and Python..."
install_packages

PYTHON_BIN="$(command -v python3 || true)"
[ -n "$PYTHON_BIN" ] || die "python3 was not found after package installation"
"$PYTHON_BIN" - <<'PY' || die "Python 3.10 or newer is required"
import sys
if sys.version_info < (3, 10):
    raise SystemExit(1)
PY
TOR_BIN="$(command -v tor || true)"
if [ -z "$TOR_BIN" ] && [ "$OS_NAME" = "Darwin" ] && [ -n "${BREW_PREFIX:-}" ]; then
    TOR_BIN="$BREW_PREFIX/bin/tor"
    [ -x "$TOR_BIN" ] || TOR_BIN=""
fi
[ -n "$TOR_BIN" ] || die "tor was not found after package installation"

if [ ! -f "$TORRC" ]; then
    if [ "$IS_TERMUX" -eq 1 ]; then
        mkdir -p "$(dirname "$TORRC")"
        touch "$TORRC"
    else
        die "torrc not found at $TORRC (is the Tor package installed correctly?)"
    fi
fi

log "Configuring Tor control port and cookie authentication..."
if [ "$IS_TERMUX" -eq 1 ]; then
    CONFIG_OUTPUT="$(python3 "$SCRIPT_DIR/scripts/configure_tor.py" "$TORRC" --control-port 9051)"
else
    CONFIG_OUTPUT="$("${SUDO[@]}" python3 "$SCRIPT_DIR/scripts/configure_tor.py" "$TORRC" --control-port 9051)"
fi
printf '%s\n' "$CONFIG_OUTPUT"
CONTROL_PORT="$(printf '%s\n' "$CONFIG_OUTPUT" | sed -n 's/^CONTROL_PORT=//p' | tail -n 1)"
SOCKS_PORT="$(printf '%s\n' "$CONFIG_OUTPUT" | sed -n 's/^SOCKS_PORT=//p' | tail -n 1)"
CONTROL_PORT="${CONTROL_PORT:-9051}"
SOCKS_PORT="${SOCKS_PORT:-9050}"

if [ "$IS_TERMUX" -eq 0 ] && [ "$OS_NAME" = "Linux" ]; then
    for group in debian-tor tor toranon _tor; do
        if getent group "$group" >/dev/null 2>&1; then
            TOR_GROUP="$group"
            break
        fi
    done
    if [ -z "$TOR_GROUP" ]; then
        log "Creating the 'tor' group..."
        run "${SUDO[@]}" groupadd tor || true
        TOR_GROUP="tor"
    fi
    if [ "$TARGET_USER" != "root" ]; then
        if ! id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx "$TOR_GROUP"; then
            log "Adding $TARGET_USER to the '$TOR_GROUP' group..."
            run "${SUDO[@]}" usermod -aG "$TOR_GROUP" "$TARGET_USER"
            NEEDS_RELOGIN=1
        fi
    fi
fi

log "Installing IP Changer files..."
if [ "$IS_TERMUX" -eq 0 ] && [ "$EUID_VALUE" -eq 0 ]; then
    run "${SUDO[@]}" install -d -m 755 "$INSTALL_DIR" "$BIN_DIR"
    run "${SUDO[@]}" rm -rf "$INSTALL_DIR/ipchanger" "$INSTALL_DIR/docs" "$INSTALL_DIR/scripts"
    run "${SUDO[@]}" cp -R "$SCRIPT_DIR/ipchanger" "$INSTALL_DIR/"
    run "${SUDO[@]}" cp -R "$SCRIPT_DIR/docs" "$INSTALL_DIR/"
    run "${SUDO[@]}" cp -R "$SCRIPT_DIR/scripts" "$INSTALL_DIR/"
    run "${SUDO[@]}" cp "$SCRIPT_DIR/README.md" "$SCRIPT_DIR/LICENSE" "$SCRIPT_DIR/pyproject.toml" "$INSTALL_DIR/"
    run "${SUDO[@]}" chmod -R a+rX "$INSTALL_DIR"
    WRAPPER_FILE="$(mktemp)"
    cat > "$WRAPPER_FILE" <<EOF
#!/bin/sh
PYTHONPATH="$INSTALL_DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PYTHON_BIN" -m ipchanger "\$@"
EOF
    run "${SUDO[@]}" install -m 755 "$WRAPPER_FILE" "$BIN_DIR/ipchanger"
    rm -f "$WRAPPER_FILE"
else
    mkdir -p "$INSTALL_DIR" "$BIN_DIR"
    rm -rf "$INSTALL_DIR/ipchanger" "$INSTALL_DIR/docs" "$INSTALL_DIR/scripts"
    cp -R "$SCRIPT_DIR/ipchanger" "$INSTALL_DIR/"
    cp -R "$SCRIPT_DIR/docs" "$INSTALL_DIR/"
    cp -R "$SCRIPT_DIR/scripts" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/README.md" "$SCRIPT_DIR/LICENSE" "$SCRIPT_DIR/pyproject.toml" "$INSTALL_DIR/"
    chmod -R a+rX "$INSTALL_DIR"
    cat > "$BIN_DIR/ipchanger" <<EOF
#!/bin/sh
PYTHONPATH="$INSTALL_DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PYTHON_BIN" -m ipchanger "\$@"
EOF
    chmod 755 "$BIN_DIR/ipchanger"
fi

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not in your PATH; add it to use the 'ipchanger' command" ;;
esac

log "Writing configuration for $TARGET_USER..."
as_target "$BIN_DIR/ipchanger" config set interval "$INTERVAL"
as_target "$BIN_DIR/ipchanger" config set control_port "$CONTROL_PORT"
as_target "$BIN_DIR/ipchanger" config set socks_port "$SOCKS_PORT"

log "Starting/restarting Tor..."
if [ "$IS_TERMUX" -eq 1 ]; then
    if command -v sv >/dev/null 2>&1 && [ -d "$PREFIX/var/service" ]; then
        if [ ! -f "$PREFIX/var/service/tor/run" ]; then
            mkdir -p "$PREFIX/var/service/tor"
            cat > "$PREFIX/var/service/tor/run" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
exec tor -f "$TORRC" 2>&1
EOF
            chmod 755 "$PREFIX/var/service/tor/run"
        fi
        run sv up tor || warn "could not start the Tor service; run: tor -f $TORRC"
    else
        if ! pgrep -x tor >/dev/null 2>&1; then
            mkdir -p "$TARGET_HOME/.local/state/ipchanger"
            nohup tor -f "$TORRC" >"$TARGET_HOME/.local/state/ipchanger/tor.log" 2>&1 &
            ok "Started Tor in the background (log: $TARGET_HOME/.local/state/ipchanger/tor.log)"
            warn "For automatic start after reboot, install termux-services and create a tor service, or use termux-boot."
        fi
    fi
else
    if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
        if systemctl cat tor.service >/dev/null 2>&1; then
            run "${SUDO[@]}" systemctl enable tor.service || true
            run "${SUDO[@]}" systemctl restart tor.service || warn "could not restart tor.service"
        elif systemctl cat 'tor@default.service' >/dev/null 2>&1; then
            run "${SUDO[@]}" systemctl enable 'tor@default.service' || true
            run "${SUDO[@]}" systemctl restart 'tor@default.service' || warn "could not restart tor@default.service"
        else
            warn "could not find a Tor systemd service; start Tor manually"
        fi
    elif [ "$OS_NAME" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
        as_target brew services start tor || warn "could not start Tor; run: brew services start tor"
    elif command -v rc-service >/dev/null 2>&1; then
        run "${SUDO[@]}" rc-service tor start || warn "could not start the Tor service"
    else
        warn "no service manager found; start Tor manually with: tor -f $TORRC"
    fi
fi

wait_for_tor() {
    python3 - "$CONTROL_PORT" <<'PY'
import socket
import sys
import time

port = int(sys.argv[1])
deadline = time.time() + 25
while time.time() < deadline:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            raise SystemExit(0)
    except OSError:
        time.sleep(0.5)
raise SystemExit(1)
PY
}

if wait_for_tor; then
    ok "Tor control port is reachable on 127.0.0.1:$CONTROL_PORT"
else
    warn "Tor control port is not reachable yet; the daemon will keep retrying"
fi

if [ "$INSTALL_SERVICE" -eq 1 ]; then
    if [ "$IS_TERMUX" -eq 1 ]; then
        if command -v sv >/dev/null 2>&1 && [ -d "$PREFIX/var/service" ]; then
            mkdir -p "$PREFIX/var/service/ipchanger"
            cat > "$PREFIX/var/service/ipchanger/run" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
exec "$BIN_DIR/ipchanger" run 2>&1
EOF
            chmod 755 "$PREFIX/var/service/ipchanger/run"
            run sv up ipchanger || warn "could not start the ipchanger service; run: ipchanger start"
        else
            mkdir -p "$TARGET_HOME/.termux/boot"
            cat > "$TARGET_HOME/.termux/boot/start-ipchanger" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
"$BIN_DIR/ipchanger" start
EOF
            chmod 755 "$TARGET_HOME/.termux/boot/start-ipchanger"
            warn "termux-services is unavailable; created ~/.termux/boot/start-ipchanger (requires the termux-boot app)"
            as_target "$BIN_DIR/ipchanger" start || warn "start manually with: ipchanger start"
        fi
    else
        if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
            log "Installing systemd service..."
            SERVICE_FILE="$(mktemp)"
            sed \
                -e "s|@USER@|$TARGET_USER|g" \
                -e "s|@GROUP@|$TARGET_USER|g" \
                -e "s|@TOR_GROUP@|$TOR_GROUP|g" \
                -e "s|@HOME@|$TARGET_HOME|g" \
                -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
                -e "s|@BIN@|$BIN_DIR/ipchanger|g" \
                "$SCRIPT_DIR/systemd/ipchanger.service" > "$SERVICE_FILE"
            run "${SUDO[@]}" install -m 644 "$SERVICE_FILE" /etc/systemd/system/ipchanger.service
            rm -f "$SERVICE_FILE"
            run "${SUDO[@]}" systemctl daemon-reload
            run "${SUDO[@]}" systemctl enable --now ipchanger.service || warn "could not start ipchanger.service"
        else
            warn "systemd is not available; start the daemon with: ipchanger start"
        fi
    fi
fi

log "Running diagnostics..."
if as_target "$BIN_DIR/ipchanger" doctor --quick; then
    ok "Diagnostics passed"
else
    warn "Diagnostics reported problems. Fix the FAIL lines, then run: ipchanger doctor"
fi

cat <<EOF

$(printf '%s' "$DISPLAY_NAME") is installed.

  Control panel : ipchanger panel
  Status        : ipchanger status
  Rotate now    : ipchanger rotate
  Logs          : ipchanger logs --follow
  Logging off   : ipchanger logs --off
  Change time   : ipchanger config set interval 5m
  Tutorial      : $INSTALL_DIR/docs/TUTORIAL.md
  Service       : $([ "$IS_TERMUX" -eq 1 ] && echo "sv status ipchanger (if termux-services is installed)" || echo "systemctl status ipchanger.service")

Your applications must use the Tor SOCKS proxy (127.0.0.1:$SOCKS_PORT)
for this to change their apparent IP. Rotating a Tor circuit does not change
your ISP-assigned address. Use Tor responsibly and follow the law.

EOF

if [ "$NEEDS_RELOGIN" -eq 1 ]; then
    warn "Log out and back in (or run 'newgrp $TOR_GROUP') so your shell can read the Tor cookie."
fi
