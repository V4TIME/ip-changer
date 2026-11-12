<div align="center">

# IP Changer

**Fresh Tor exit IPs on a schedule — from a terminal or Termux control panel.**

[![Tests](https://github.com/V4TIME/ip-changer/actions/workflows/ci.yml/badge.svg)](https://github.com/V4TIME/ip-changer/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/V4TIME/ip-changer?color=blue)](https://github.com/V4TIME/ip-changer/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

[Quick start](#quick-start) · [Why IP Changer?](#why-choose-ip-changer) · [Chrome setup](#use-it-with-chrome-or-any-app) · [Tutorial](docs/TUTORIAL.md) · [Commands](#command-reference)

</div>

---

IP Changer asks a local Tor daemon for a new circuit at the interval you choose,
checks that the exit IP actually changed, and shows everything in a live terminal
panel. It works on a normal Linux desktop, a server, macOS, and inside Termux on
Android.

> **Read this first:** it rotates the **Tor exit IP**, not your ISP-assigned
> address. An app only gets a new visible IP if it sends traffic through Tor's
> SOCKS proxy (`127.0.0.1:9050`). [Set up Chrome or another app →](#use-it-with-chrome-or-any-app)

## Quick start

### Linux — one command

```bash
curl -fsSL https://raw.githubusercontent.com/V4TIME/ip-changer/main/get.sh | sudo bash -s -- --interval 5m
```

### macOS — one command

Run it **without** `sudo` so Homebrew can install as your user; the installer
asks for sudo only for the torrc step:

```bash
curl -fsSL https://raw.githubusercontent.com/V4TIME/ip-changer/main/get.sh | bash -s -- --interval 5m
```

### Android / Termux — one command

```bash
pkg install -y curl
curl -fsSL https://raw.githubusercontent.com/V4TIME/ip-changer/main/get.sh | bash -s -- --interval 5m
```

### Then open the panel

```bash
ipchanger doctor      # confirm Tor, the control port and SOCKS work
ipchanger panel       # start/stop, rotate now, change interval, logs, doctor
```

That is the whole setup. The installer adds Tor, configures the control port,
installs the `ipchanger` command, starts the background service when the
platform supports it, and runs diagnostics for you.

<details>
<summary><b>What the installer changes (and how to undo it)</b></summary>

1. Installs `tor` and Python 3.10+ with your package manager.
2. Backs up torrc and adds a marked control-port/cookie block, then validates it
   with `tor --verify-config`.
3. Adds your user to the real Tor group on Linux (`debian-tor`, `tor`, `toranon`
   or `_tor`).
4. Installs to `/opt/ip-changer` + `/usr/local/bin/ipchanger` (or a per-user
   prefix without root, or `$PREFIX/share/ip-changer` on Termux).
5. Starts Tor, waits for the control port, creates the service where the
   platform supports it, and runs `ipchanger doctor --quick`.
6. Prints the panel/status/log commands when it finishes.

Nothing is hidden: `--dry-run` shows the plan, `--no-service` skips the service,
and `--interval` avoids the prompt. Uninstall with `sudo ./uninstall.sh`; add
`--purge` to remove config, state and logs.

</details>

### Prefer a clone?

```bash
git clone https://github.com/V4TIME/ip-changer.git
cd ip-changer
sudo ./install.sh --interval 5m     # Linux
./install.sh --interval 5m          # macOS
ipchanger doctor
ipchanger panel
```

### Supported platforms

| Platform | Install | Service |
|----------|---------|---------|
| Debian, Ubuntu, Kali, Parrot | one command | systemd |
| Arch, Manjaro, Fedora, openSUSE | one command | systemd |
| Alpine | one command | manual `ipchanger start` |
| macOS | one command | manual `ipchanger start` |
| Android / Termux | one command, no root | termux-services or boot script |
| Windows | not supported natively | use WSL2 or a Linux VM |

Python 3.10 or newer is required. There are **no runtime dependencies** beyond
the standard library and Tor. macOS uses Homebrew services; Alpine uses OpenRC
when available. Neither has systemd, so start the background daemon with
`ipchanger start`.

## Why choose IP Changer?

Most “IP changer” scripts only fire `SIGNAL NEWNYM` and print an IP. IP Changer
verifies the result, keeps state, runs a real scheduler, and stays out of your
way.

- **It proves the change.** Waits for a new exit, compares old/new IPs, validates
  the response, and checks `IsTor`.
- **It schedules properly.** One long-running daemon with interval + optional
  jitter, not a systemd `RestartSec` hack.
- **It is safe by default.** No root service running a user-writable script;
  torrc edits are backed up, marked, and validated.
- **It works on Termux.** No systemd requirement, no root, battery-friendly
  start/stop and boot-script support.
- **You control the logs.** JSONL history can be turned off from the panel (`g`)
  or with `ipchanger logs --off`.
- **No runtime dependencies.** The Tor control protocol, SOCKS5 client and HTTP
  checks use only the Python standard library.

### Compared with the alternatives

| | **IP Changer** | [techchipnet/ip-changer](https://github.com/techchipnet/ip-changer) | Manual Tor + `NEWNYM` | VPN |
|---|---|---|---|---|
| Scheduled rotation | Yes | Yes | No | No |
| Verifies the new exit IP | Yes | No | No | No |
| Terminal control panel | Yes | No | No | No |
| Runs as a non-root user | Yes | No (root service) | Yes | Yes |
| Termux / Android | Yes | No | Manual | App only |
| Safe, validated torrc edits | Yes | No (blind append) | Manual | N/A |
| Logging can be disabled | Yes | No (broken path) | No | N/A |
| Setup | one command | two scripts | several steps | account/app |
| Cost | free, MIT | free | free | usually paid |

The line-by-line review of the original project is in
[`docs/UPSTREAM_REVIEW.md`](docs/UPSTREAM_REVIEW.md). In short: it installs a
root service that runs a script from a user-writable home directory, requires
`nc` without installing it, logs to the literal path `HOME/tor_ip_log.txt`, and
uses the systemd restart delay as the rotation timer.

## Use it with Chrome (or any app)

Rotating the Tor circuit is only half the job — the app must use Tor. These
instructions connect Chrome, Chromium, Edge, Brave or Firefox to the local Tor
SOCKS proxy.

### Chrome / Chromium / Edge / Brave (desktop)

Launch the browser with a dedicated profile and the SOCKS proxy:

```bash
# Linux
google-chrome --proxy-server="socks5://127.0.0.1:9050" \
  --user-data-dir="$HOME/.config/chrome-tor"
```

```bash
# Chromium / Edge / Brave: replace the binary name
chromium --proxy-server="socks5://127.0.0.1:9050" \
  --user-data-dir="$HOME/.config/chrome-tor"
```

```bash
# macOS
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --proxy-server="socks5://127.0.0.1:9050" \
  --user-data-dir="$HOME/.config/chrome-tor"
```

Then open <https://check.torproject.org/>. You should see **“Congratulations.
This browser is configured to use Tor.”** Keep using this window/profile; normal
Chrome windows still use your direct connection.

### Firefox

1. **Settings → General → Network Settings → Settings…**
2. Choose **Manual proxy configuration**.
3. Set **SOCKS Host** to `127.0.0.1` and **Port** to `9050`.
4. Select **SOCKS v5**.
5. Tick **Proxy DNS when using SOCKS v5**.
6. Open <https://check.torproject.org/> to verify.

### Android / Termux and Chrome

Android Chrome does **not** have a built-in SOCKS proxy setting. Use one of these:

- **Orbot (VPN mode)** — routes Chrome and other apps through Tor. Use Orbot’s
  own new-identity control, or point IP Changer at Orbot’s control port if you
  have enabled it.
- **A SOCKS5-capable browser or proxy/VPN front-end** — advanced; make sure it
  targets `127.0.0.1:9050`.
- **Desktop browser** — the simplest way to test per-app proxy settings with the
  Termux Tor daemon.

IP Changer still rotates the Termux Tor daemon; Chrome simply cannot use that
SOCKS port without a VPN/proxy layer on Android.

### Any command-line app

Use `torsocks`, `proxychains`, or the app’s own SOCKS5 option:

```bash
torsocks curl https://check.torproject.org/api/ip
ipchanger ip          # the exit IP IP Changer sees through Tor
```

Full browser and Android notes: [`docs/BROWSER_SETUP.md`](docs/BROWSER_SETUP.md).

## Control panel

```text
+ IP Changer 1.1.1 -------------------------------------------------+
|                                                                  |
| Service       RUNNING  pid 42117                                 |
| Tor exit IP   185.220.101.42                                     |
| Tor check     verified                                           |
| Previous IP   51.15.43.8                                         |
| Last change   2026-11-11 12:34:56  (12s ago)                     |
| Next change   in 1m 48s                                          |
| Interval      2m  (120s, jitter 0%)                              |
| Options       verify on   logs on                                |
| Rotations     42 ok  /  0 failed                                 |
| Last event    2026-11-11T12:34:56  rotation  51.15.43.8 -> ...   |
|                                                                  |
+------------------------------------------------------------------+
| [s] start/stop  [r] rotate now  [v] check IP  [i] interval       |
| [g] logging  [t] verification  [l] logs  [d] doctor  [c] config  |
| Ready. Press ? for help.                                         |
+------------------------------------------------------------------+
```

| Key | Action |
|-----|--------|
| `s` | Start or stop the background daemon. |
| `r` | Rotate now: `SIGNAL NEWNYM`, wait, verify, log. |
| `v` | Fetch the current exit IP through Tor. |
| `i` | Change the interval (`90`, `5m`, `1h30m`; minimum 10s). |
| `j` | Change jitter (0–100% random extra delay). |
| `g` | Turn rotation logging on/off. |
| `t` | Turn exit-IP verification on/off. |
| `l` | Browse the rotation log. |
| `d` | Run diagnostics. |
| `c` | Show the active configuration. |
| `?` / `h` | Help. |
| `q` | Leave the panel; the daemon keeps running. |

## Command reference

```bash
ipchanger                     # open the control panel
ipchanger panel               # same thing, explicit
ipchanger doctor              # check Tor, auth, SOCKS, daemon and exit IP
ipchanger start               # start automatic rotation in the background
ipchanger status              # service, exit IP, last change, countdown
ipchanger status --json       # machine-readable status
ipchanger rotate              # NEWNYM once, wait, verify, log
ipchanger ip                  # current Tor exit IP
ipchanger logs --follow       # follow rotation events
ipchanger logs --off          # stop writing new log entries
ipchanger logs --on           # enable logging again
ipchanger stop                # stop the background daemon
```

| Command | What it does |
|---------|--------------|
| `ipchanger [panel]` | Open the interactive control panel. |
| `ipchanger run` | Run the scheduler in the foreground (`--once` for one rotation). |
| `ipchanger start` / `stop` / `restart` | Manage the detached daemon. |
| `ipchanger status [--json]` | Show daemon state, exit IP, last/next change and counters. |
| `ipchanger rotate [--json] [--no-verify] [--no-wait]` | Rotate once and verify. |
| `ipchanger ip [--json]` | Fetch the current exit IP through Tor. |
| `ipchanger config list/get/set/reset` | Inspect or edit JSON configuration. |
| `ipchanger logs [--lines N] [--follow] [--json]` | Read or follow the JSONL log. |
| `ipchanger logs --on/--off/--status` | Enable, disable or inspect logging. |
| `ipchanger doctor [--quick]` | Diagnose the environment. |

## Configuration

```bash
ipchanger config list
ipchanger config get interval
ipchanger config set interval 5m
ipchanger config set jitter 20             # add 0–20% random delay
ipchanger config set log_enabled false     # same as: ipchanger logs --off
ipchanger config set require_tor true      # fail if IsTor cannot be confirmed
ipchanger config set check_urls '["https://check.torproject.org/api/ip"]'
ipchanger config reset
ipchanger restart                          # apply changes to a running daemon
```

| Option | Default | Why you may change it |
|--------|---------|-----------------------|
| `interval` | `120` | How often to request a new circuit (minimum 10s). |
| `jitter` | `0` | Random extra delay (0–100%) so rotations are less regular. |
| `verify` | `true` | Check the new exit IP through Tor after each rotation. |
| `wait_for_change` | `true` | Wait until the exit actually changes (or time out). |
| `require_tor` | `false` | Fail if `IsTor` cannot be confirmed. |
| `change_timeout` | `45` | Seconds to wait for a new exit. |
| `check_urls` | Tor Project + fallbacks | Exit-IP check endpoints. |
| `log_enabled` | `true` | Write rotation/daemon events to the JSONL log. |
| `control_port` | `9051` | Tor control port. |
| `socks_port` | `9050` | Tor SOCKS port used by apps and verification. |
| `auth_method` | `auto` | `auto`, `cookie`, `password` or `null`. |

Paths:

| Purpose | Default |
|---------|---------|
| Config | `~/.config/ipchanger/config.json` |
| State | `~/.local/state/ipchanger/state.json` |
| Rotation log | `~/.local/state/ipchanger/ipchanger.log` |
| Daemon output | `~/.local/state/ipchanger/daemon.log` |

## Termux notes

- No root and no systemd required.
- If `termux-services` is installed, the installer creates `tor` and `ipchanger`
  services. Otherwise it starts Tor in the background and writes
  `~/.termux/boot/start-ipchanger` for the Termux:Boot app.
- Run `termux-wake-lock` and exempt Termux from battery optimisation so Android
  does not kill the daemon.
- Chrome on Android needs Orbot/VPN mode; see
  [the browser guide](docs/BROWSER_SETUP.md).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `cannot connect to the Tor control port` | Start Tor (`sudo systemctl start tor`, or `tor &` in Termux) and run `ipchanger doctor`. |
| Cookie/authentication error | Add your user to the Tor group and log in again, or set `auth_method=password`. |
| `SOCKS port` connection refused | Tor is not running or `SocksPort` is disabled. |
| Chrome still shows the old IP | Launch Chrome with `--proxy-server="socks5://127.0.0.1:9050"` and verify at <https://check.torproject.org/>. |
| Exit IP does not change | Tor may reuse an exit; wait for the next interval or rotate again. |
| No new log entries | Logging may be off: `ipchanger logs --status`, then `ipchanger logs --on`. |
| Interval change did nothing | `ipchanger restart` after `config set`; the panel restarts automatically. |
| Service keeps restarting | `systemctl status ipchanger.service` and `journalctl -u ipchanger.service -n 100`. |

## Documentation

- [`docs/TUTORIAL.md`](docs/TUTORIAL.md) — complete walkthrough of every step.
- [`docs/BROWSER_SETUP.md`](docs/BROWSER_SETUP.md) — Chrome, Firefox, Android,
  `torsocks` and verification.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — module map and data flow.
- [`docs/SECURITY.md`](docs/SECURITY.md) — threat model and safe defaults.
- [`docs/UPSTREAM_REVIEW.md`](docs/UPSTREAM_REVIEW.md) — line-referenced review
  of the original project.

## Uninstall

```bash
sudo ./uninstall.sh            # system install; keep config/state
./uninstall.sh                 # per-user install (macOS or non-root Linux)
sudo ./uninstall.sh --purge    # also remove config, state and logs
```

The Tor package, the Tor group and torrc backups are left in place. Only the
marked `# BEGIN ip-changer managed block` is removed from torrc.

## License

MIT. See [`LICENSE`](LICENSE).

## Credits

Original idea and demo: [TechChip / techchipnet](https://github.com/techchipnet/ip-changer).
