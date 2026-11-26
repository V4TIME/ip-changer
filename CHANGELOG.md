# Changelog

## 1.3.1 - 2026-11-26

- Removed the Android note app (`noteapp/`), its Kivy/Buildozer configuration,
  the `apk` workflow and the APK diagnostics script. This repository is the
  terminal/Termux IP Changer; it does not ship a VPN or an Android app.
- CI no longer runs note-app storage tests.

## 1.3.0 - 2026-11-25

- Added SOCKS-identity rotation (`rotation_mode=socks`): every attempt uses a
  fresh SOCKS5 username/password, so Tor builds a new circuit even when the
  control port is unreachable. This is the mode that works with **Orbot**,
  which never exposes its control socket to other apps.
- Added `rotation_mode` (`auto`, `control`, `socks`) with validation; `auto`
  probes the control port and falls back to SOCKS.
- Added `ipchanger rotate --mode {auto,control,socks}` and `mode` in JSON
  output, log records and `status`.
- `doctor` now reports the rotation mode, downgrades an unreachable control
  port to a warning when SOCKS is allowed, and explains the Orbot case.
- The panel's Proxy row shows the rotation mode; its footer explains that SOCKS
  rotation does not redirect other apps by itself.
- Verified against a real Tor 0.4.9.16 with the control port closed: two
  rotations produced distinct, confirmed Tor exit IPs in `socks` mode.

## 1.2.0 - 2026-11-24

- Rebuilt the control panel: one filled connection bar (connected, connecting,
  unverified, not-using-Tor, disconnected), a two-column status grid, a live
  bottom-anchored activity tail, and a 1 Hz redraw gate instead of a busy loop.
- Restored every panel control after a regression that removed them: interval,
  jitter, logging, verification, log pager, doctor, config and help.
- Fixed `ipchanger.tui` failing to import, which broke `ipchanger` and
  `ipchanger panel` entirely.
- Added `ipchanger/theme.py` with the palette, glyphs and layout metrics, plus a
  monochrome and ASCII fallback for terminals without colour or UTF-8.
- Added `noteapp/`, an offline Android note app (Kivy) with private atomic
  storage, corruption recovery, legacy import, and its own tests.
- Added `.github/workflows/apk.yml` to build the note APK in CI, verified by a
  green run producing `pixnote-debug-apk` (41.8 MB).
- Added `docs/AUDIT.md` with findings, verification and remaining risks.

## 1.1.1 - 2026-11-11

- One-command installation for Linux, macOS and Termux, with a supported
  platform table and a “Why choose IP Changer?” comparison.
- Added `docs/BROWSER_SETUP.md` with desktop Chrome/Chromium/Edge/Brave,
  Firefox, Android/Orbot, `torsocks` and `proxychains` instructions.
- Fixed the installer's torrc step to use sudo when run as a non-root user
  (also makes the documented macOS no-sudo install work).

## 1.1.0 - 2026-11-11

- Added a logging switch: `ipchanger logs --on/--off/--status` and panel key `g`.
  Existing logs are preserved; disabling only stops new writes.
- Added panel key `t` to toggle exit-IP verification, and an `Options` row that
  shows both settings at a glance.
- The panel now shows the latest rotation event and displays the interval in
  seconds alongside the human-readable value.
- Added a complete step-by-step tutorial in `docs/TUTORIAL.md`.

## 1.0.1 - 2026-11-11

- Added `get.sh`, a one-command bootstrap that downloads the repository and runs
  the installer with any `install.sh` arguments. It accepts branches, tags and
  commit hashes via `IPCHANGER_REF`.
- Documented the full installation sequence and compared it step by step with
  the upstream `techchipnet/ip-changer` installer.

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
- Upstream `techchipnet/ip-changer` review with line-referenced bugs.
