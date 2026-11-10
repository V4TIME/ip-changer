# IP Changer

**Rotate the Tor exit IP from a terminal or Termux control panel.**

IP Changer is a small, dependency-light Python tool that asks a local Tor
daemon for fresh circuits at a chosen interval, verifies the new exit IP, and
keeps a live status/log history. It ships a full-screen control panel as well as
scriptable CLI commands.

This repository is a from-scratch alternative to
[`techchipnet/ip-changer`](https://github.com/techchipnet/ip-changer). I studied
that project first; its bugs, missing features and lack of a control panel are
documented in [`docs/UPSTREAM_REVIEW.md`](docs/UPSTREAM_REVIEW.md).

> **Important:** this rotates the **Tor exit IP**. It does not change your
> ISP-assigned address. An application only benefits if it sends its traffic
> through Tor's SOCKS port (normally `127.0.0.1:9050`), or if you use a
> system-wide Tor transparent proxy. Use it for privacy and research, not to
> evade bans, commit fraud, or break the law.

## Features

- **Control panel** — live exit IP, previous IP, countdown, rotation counters,
  start/stop, rotate now, interval/jitter editor, log viewer and diagnostics.
- **Real scheduler** — one long-running daemon with interval, optional jitter,
  graceful SIGTERM, and a `next_rotation` timestamp (not a systemd restart
  trick).
- **Verified rotations** — sends `SIGNAL NEWNYM`, waits for a new circuit,
  compares with the previous IP, validates the result, and checks `IsTor`.
- **Zero runtime dependencies** — Python 3 standard library only. No `curl`,
  `jq`, `nc`, `xxd`, `requests` or `PySocks`.
- **Correct Tor auth** — discovers `PROTOCOLINFO`, cookie path, and supports
  cookie, password or null authentication.
- **Safe installer** — torrc backup + validation, marked block, correct Tor
  group discovery, no root service executing a user-writable script.
- **Termux ready** — no systemd required; uses `termux-services` when available
  and otherwise gives a boot-script/manual path.
- **Structured state and logs** — atomic JSON state, rotated JSONL logs, file
  lock to prevent concurrent rotations.
- **Diagnostics** — `ipchanger doctor` checks Python, Tor, control auth, cookie
  permissions, SOCKS, daemon state and the live exit IP.
- **Tested** — 42 unit tests for the control protocol, SOCKS5/HTTP client,
  config, rotator, scheduler and CLI; CI workflow included.
- **Clean uninstall** — removes the service, files, wrapper and managed torrc
  block; `--purge` also removes config/history.

## Control panel preview

```text
+ IP Changer 1.0.0 -------------------------------------------------+
|                                                                  |
| Service       RUNNING  pid 42117                                 |
| Tor exit IP   185.220.101.42                                     |
| Tor check     verified                                           |
| Previous IP   51.15.43.8                                         |
| Last change   2026-11-10 12:34:56  (12s ago)                     |
| Next change   in 1m 48s                                          |
| Interval      2m  (120s, jitter 0%)                              |
| Rotations     42 ok  /  0 failed                                 |
|                                                                  |
+------------------------------------------------------------------+
| [s] start/stop  [r] rotate now  [v] verify  [i] interval        |
| [j] jitter  [l] logs  [d] doctor  [c] config  [q] quit           |
| Ready.                                                           |
+------------------------------------------------------------------+
```

## Quick start

### Debian / Ubuntu / Kali / Parrot / Arch / Fedora / openSUSE / Alpine

```bash
git clone https://github.com/V4TIME/ip-changer.git
cd ip-changer
sudo ./install.sh --interval 5m
ipchanger doctor
ipchanger panel
```

The installer:

1. installs `tor` and Python 3 with the native package manager,
2. adds `ControlPort 9051`, cookie authentication and group-readable cookie
   settings to torrc (backed up and validated),
3. adds the invoking user to the correct Tor group (`debian-tor`, `tor`,
   `toranon` or `_tor`),
4. installs the Python package to `/opt/ip-changer` and a wrapper at
   `/usr/local/bin/ipchanger`,
5. enables and starts `ipchanger.service` as the invoking user,
6. runs diagnostics and prints the next commands.

If it says you were added to the Tor group, log out and back in (or run
`newgrp debian-tor`) once so your shell can read the cookie. The systemd service
gets the group immediately.

### Termux

```bash
pkg install -y git python tor
git clone https://github.com/V4TIME/ip-changer.git
cd ip-changer
./install.sh --interval 5m
ipchanger doctor
ipchanger panel
```

No root is needed. If `termux-services` is available, the installer creates
`tor` and `ipchanger` services. Otherwise it starts Tor in the background and
writes `~/.termux/boot/start-ipchanger` (requires the Termux:Boot app) so you can
keep it running after a reboot. You can always run it manually:

```bash
ipchanger start
ipchanger status
```

### Run from a clone without installing

```bash
python3 -m ipchanger --help
python3 -m ipchanger doctor
python3 -m ipchanger panel
```

Tor must already be running with a control port; the installer is the easy path
for that.

## Everyday usage

```bash
ipchanger                     # open the control panel
ipchanger panel               # same thing, explicit
ipchanger doctor              # check Tor, cookie, SOCKS, daemon and exit IP
ipchanger start               # start automatic rotation in the background
ipchanger status              # service, exit IP, last change, countdown
ipchanger status --json       # machine-readable status
ipchanger rotate              # NEWNYM once, wait, verify, log
ipchanger rotate --no-verify  # just signal NEWNYM
ipchanger ip                  # current Tor exit IP
ipchanger logs --follow       # follow structured rotation events
ipchanger stop                # stop the background daemon
```

Useful configuration changes:

```bash
ipchanger config list
ipchanger config get interval
ipchanger config set interval 5m
ipchanger config set jitter 20             # add 0-20% random delay
ipchanger config set require_tor true      # fail if IsTor cannot be confirmed
ipchanger config set check_urls '["https://check.torproject.org/api/ip"]'
ipchanger config reset
```

After `config set`, restart a running daemon to apply it:

```bash
ipchanger restart
```

The control panel applies interval/jitter changes immediately by restarting the
daemon for you.

### Command reference

| Command | What it does |
|---------|--------------|
| `ipchanger [panel]` | Open the interactive control panel. |
| `ipchanger run` | Run the scheduler in the foreground (used by systemd/Termux services). `--once` rotates once and exits. |
| `ipchanger start` / `stop` / `restart` | Manage the detached daemon. `--interval` and `--jitter` override for that run. |
| `ipchanger status [--json]` | Show daemon state, exit IP, last/next change and counters. |
| `ipchanger rotate [--json] [--no-verify] [--no-wait]` | Rotate once and verify. |
| `ipchanger ip [--json]` | Fetch the current exit IP through Tor. |
| `ipchanger config list/get/set/reset` | Inspect or edit JSON configuration. |
| `ipchanger logs [--lines N] [--follow] [--json]` | Read or follow the JSONL log. |
| `ipchanger doctor [--quick]` | Diagnose the environment. |
| `ipchanger --config PATH ...` | Use a custom config file. |

### Control panel keys

| Key | Action |
|-----|--------|
| `s` | Start/stop the background daemon. |
| `r` | Rotate now (NEWNYM + wait + verify) in a worker thread. |
| `v` | Fetch the current exit IP. |
| `i` | Change interval (`90`, `5m`, `1h30m`; minimum 10s). |
| `j` | Change jitter (0-100%). |
| `l` | Browse the rotation log. |
| `d` | Run diagnostics. |
| `c` | Show the active configuration. |
| `?` / `h` | Help. |
| `q` | Leave the panel; the daemon keeps running. |

## How it works

```text
panel/CLI -> scheduler -> rotator -> Tor control port (SIGNAL NEWNYM)
                               \--> SOCKS5 -> HTTPS check -> exit IP
                               \--> state.json + JSONL log
```

1. Read the last IP from state (or establish a baseline through Tor).
2. Respect Tor's 10-second NEWNYM rate limit.
3. Authenticate to `ControlPort 9051` (cookie, password or null).
4. Send `SIGNAL NEWNYM`.
5. Poll a check endpoint through `127.0.0.1:9050` until the exit IP changes or
   the timeout expires.
6. Verify `IsTor`, store the new IP/previous IP/counters, append a JSONL event.
7. Sleep for the configured interval (plus optional jitter), then repeat.

Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the module map, the raw
control-protocol exchange, the SOCKS5 handshake, and all data paths.

### Paths

| Purpose | Default |
|---------|---------|
| Config | `~/.config/ipchanger/config.json` |
| State | `~/.local/state/ipchanger/state.json` |
| Log | `~/.local/state/ipchanger/ipchanger.log` |
| Daemon output | `~/.local/state/ipchanger/daemon.log` |
| PID / lock | `~/.local/state/ipchanger/ipchanger.pid` / `.lock` |

## What I found in the upstream project

The full line-by-line review is in
[`docs/UPSTREAM_REVIEW.md`](docs/UPSTREAM_REVIEW.md). The highest-impact
findings:

| Severity | Upstream issue | This project |
|----------|----------------|--------------|
| Critical | The systemd unit runs as root and executes a script from a user-writable home directory. | Runs as the invoking user from a fixed install directory, with hardening. |
| High | `change_tor_ip.sh` logs to `HOME/tor_ip_log.txt` (missing `$`). | State/log paths are explicit and private. |
| High | Requires `nc` but never installs it; README lists unused `fq`; omits `xxd`/`jq`/`nc`. | No shell utilities; the control protocol and SOCKS5/HTTP are implemented in Python. |
| High | Accepts `abc`, `0`, negative or 1-second intervals; `sleep` fails or spins; Tor ignores NEWNYM under 10s. | Duration parser and hard minimum of 10 seconds. |
| High | No timeouts/retry/`--fail`; can log `null` or hang. | Timeouts, retries/fallback endpoints, IP validation, `IsTor` check. |
| High | Sends NEWNYM and immediately checks, so it can report the old IP. | Waits for the exit to change, with a timeout and an honest warning if Tor reuses an exit. |
| High | `grep`/append torrc without backup or validation; misses existing/indented directives. | Marked block, backup, port discovery, `tor --verify-config`. |
| High | Uses systemd `RestartSec` as the rotation interval. | A real scheduler in one long-running process. |
| Medium | Adds `root` to the Tor group under `sudo`; Fedora group is normally `toranon`. | Uses `SUDO_USER`/`--user` and discovers the real group. |
| Medium | No control panel, state, status, logs, diagnostics or Termux support. | All included. |

**What upstream is missing most:** it never routes applications through Tor. It
only rotates the daemon's circuits and prints a number. If an app is not using
SOCKS `127.0.0.1:9050`, its visible IP will not change. This project makes that
limitation explicit and verifies the exit through Tor.

## Comparison

| Capability | `techchipnet/ip-changer` | This project |
|------------|--------------------------|--------------|
| Tor circuit rotation | Yes (shell + `nc`) | Yes (implemented control protocol) |
| Control panel | No | Full curses panel + text fallback |
| Interval validation | No | Minimum 10s, duration parser |
| Wait for new circuit | No | Yes, with timeout |
| `IsTor` verification | No | Yes (fails on explicit non-Tor) |
| State/countdown | No | JSON state + live countdown |
| Structured logs | Broken path | Rotated JSONL + viewer |
| Cookie discovery | Hard-coded path | `PROTOCOLINFO` + override |
| Password/null auth | No | Yes |
| Termux | No | Yes |
| Root service executing user script | Yes | No; runs as user |
| torrc safety | Append only | Backup + managed block + verify |
| Diagnostics | No | `ipchanger doctor` |
| Tests/CI | No | 42 tests + GitHub Actions |
| Uninstall | Partial | Service/files/torrc + `--purge` |

## Termux notes

- No systemd. Use `ipchanger start/stop/status` or `termux-services`.
- If you use Termux:Boot, the installer writes
  `~/.termux/boot/start-ipchanger`; the Termux:Boot app must be installed and
  opened once.
- Battery optimisation can kill background processes. Run
  `termux-wake-lock` (the boot script does this) and exempt Termux from battery
  optimisation.
- Tor can be slow to bootstrap on mobile networks. Run `ipchanger doctor` and
  check the Tor log if the control port never opens.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `cannot connect to the Tor control port` | Start Tor (`sudo systemctl start tor`, or `tor &` in Termux) and confirm `ControlPort 9051` is in torrc. Run `ipchanger doctor`. |
| `cannot read Tor cookie` / authentication failed | Add your user to the Tor group (`debian-tor`, `tor` or `toranon`) and log in again, or set `auth_method=password` with a Tor control password. |
| `SOCKS port` connection refused | Tor is not running, or `SocksPort` is disabled. Check torrc and `ipchanger doctor`. |
| Exit IP does not change | Tor may reuse an exit; wait for the next interval. Some networks force a single exit. Check the warning in `ipchanger logs`. |
| Websites block the IP | Many sites block Tor exits. Try another rotation or accept that Tor is not suitable for that site. |
| Interval change did nothing | `ipchanger restart` after `config set`; the panel restarts automatically. |
| Service keeps restarting | `systemctl status ipchanger.service` and `journalctl -u ipchanger.service -n 100`; then `ipchanger doctor`. |
| `PYTHONPATH`/module not found | Use the installed wrapper `ipchanger`, or run `python3 -m ipchanger` from the repository root. |

## Development

```bash
git clone https://github.com/V4TIME/ip-changer.git
cd ip-changer
python3 -m unittest discover -s tests -v
python3 -m compileall -q ipchanger scripts
bash -n install.sh uninstall.sh
```

The tests do not need Tor or network access: they use a fake control server, a
fake SOCKS5 proxy and a local HTTP server. The CI workflow runs them on Python
3.9, 3.11 and 3.13.

## Uninstall

```bash
sudo ./uninstall.sh            # keep config/state
sudo ./uninstall.sh --purge    # also remove config, state and logs
```

The Tor package, the Tor group and torrc backups are left in place. Only the
marked `# BEGIN ip-changer managed block` is removed from torrc.

## License

MIT. See [`LICENSE`](LICENSE).

## Credits

The original idea and demo: [TechChip / techchipnet](https://github.com/techchipnet/ip-changer).
This alternative keeps the core NEWNYM idea, adds a control panel and a tested
implementation, and documents the original's pitfalls.
