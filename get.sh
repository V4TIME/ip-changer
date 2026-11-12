#!/usr/bin/env bash
# One-command bootstrap for IP Changer.
#
#   curl -fsSL https://raw.githubusercontent.com/V4TIME/ip-changer/main/get.sh | sudo bash -s -- --interval 5m
#
# Downloads the repository tarball, then hands over to install.sh. All
# arguments are passed through, so every install.sh option keeps working.
set -euo pipefail

REPO="${IPCHANGER_REPO:-V4TIME/ip-changer}"
REF="${IPCHANGER_REF:-main}"
ARCHIVE_URL="${IPCHANGER_ARCHIVE_URL:-https://codeload.github.com/$REPO/tar.gz/$REF}"

log() { printf '[*] %s\n' "$*"; }
die() { printf '[x] %s\n' "$*" >&2; exit 1; }

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

log "Downloading IP Changer ($REPO@$REF)..."
if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 3 --connect-timeout 15 "$ARCHIVE_URL" -o "$TMP_DIR/source.tar.gz"
elif command -v wget >/dev/null 2>&1; then
    wget -q --tries=3 --timeout=15 -O "$TMP_DIR/source.tar.gz" "$ARCHIVE_URL"
else
    die "curl or wget is required. Install one (Termux: pkg install curl) and retry."
fi

[ -s "$TMP_DIR/source.tar.gz" ] || die "download failed or produced an empty file"

log "Extracting..."
tar -xzf "$TMP_DIR/source.tar.gz" -C "$TMP_DIR" || die "could not extract the archive"

SRC_DIR=""
for candidate in "$TMP_DIR"/ip-changer-*; do
    if [ -d "$candidate" ] && [ -x "$candidate/install.sh" ]; then
        SRC_DIR="$candidate"
        break
    fi
done
[ -n "$SRC_DIR" ] || die "archive did not contain install.sh"

log "Running the installer..."
# install.sh handles sudo internally, so this also works when piped without sudo.
"$SRC_DIR/install.sh" "$@"
