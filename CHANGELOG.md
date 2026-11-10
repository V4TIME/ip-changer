# Changelog

## 1.0.0 - 2026-11-10

Initial release.

- Tor control protocol implementation with cookie, password and null auth.
- SOCKS5 + HTTPS exit-IP verification without third-party packages.
- Real scheduler with validated interval and optional jitter.
- Curses control panel with live countdown, rotate-now, interval/jitter editing,
  log viewer and diagnostics.
- Detached daemon with start/stop/restart/status and PID/lock handling.
- Atomic JSON state and rotated JSONL logs.
- `ipchanger doctor` diagnostics.
- Installer/uninstaller for Debian-family, Arch, Fedora, openSUSE, Alpine,
  macOS (CLI) and Termux.
- torrc backup, managed block, validation and correct Tor group discovery.
- 42 unit tests and GitHub Actions CI.
- Upstream `techchipnet/ip-changer` review with line-referenced bugs.
